import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .state import build_reasoning_gates, normalize_evidence, parse_json_object, sanitize_answer, sanitize_brief_reasoning

try:
    from ..teaching_optimizer import TeachingOptimizer, create_teaching_optimizer
except Exception:  # pragma: no cover - optional experimental module
    TeachingOptimizer = Any  # type: ignore[misc,assignment]

    def create_teaching_optimizer(ai_router: Any) -> Any:
        return None

logger = logging.getLogger(__name__)

# 教学优化配置
ENABLE_TEACHING_OPTIMIZATION = os.getenv("ENABLE_TEACHING_OPTIMIZATION", "true").lower() == "true"
TEACHING_OPTIMIZATION_TIMEOUT = float(os.getenv("TEACHING_OPTIMIZATION_TIMEOUT", "2.0"))
MIN_ITEMS_FOR_OPTIMIZATION = int(os.getenv("MIN_ITEMS_FOR_OPTIMIZATION", "2"))


class GraphNodes:
    def __init__(self, ai_router: Any, rag_engine: Any, memory_hook: Any = None) -> None:
        self.ai_router = ai_router
        self.rag_engine = rag_engine
        self.memory_hook = memory_hook
        self.teaching_optimizer = create_teaching_optimizer(ai_router)
    
    async def _optimize_with_timeout(self, concept: str, text: str, context: str) -> str:
        """带超时保护的教学优化"""
        if not ENABLE_TEACHING_OPTIMIZATION or not text.strip() or self.teaching_optimizer is None:
            return text
            
        try:
            # 使用异步超时包装
            optimized = await asyncio.wait_for(
                asyncio.to_thread(self.teaching_optimizer.optimize_explanation, concept, text, context),
                timeout=TEACHING_OPTIMIZATION_TIMEOUT
            )
            return optimized
        except asyncio.TimeoutError:
            logger.warning(f"TeachingOptimizer超时: concept={concept}, context={context[:50]}...")
            return text  # 超时回退到原始文本
        except Exception as e:
            logger.warning(f"TeachingOptimizer异常: {e}, concept={concept}")
            return text  # 异常回退到原始文本
    
    async def _normalize_chapter_summary_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        chapter_title = str(item.get("chapter_title", "章节")).strip() or "章节"
        key_points = _to_list(item.get("key_points"))
        analysis = _to_list(item.get("analysis"))
        recommendations = _to_list(item.get("recommendations"))
        risks = _to_list(item.get("risks"))

        def _normalize_lines(items: List[str], *, max_items: int = 8) -> List[str]:
            normalized: List[str] = []
            for raw in items[:max_items]:
                text = re.sub(r"^(\d+)[\.、]?\s+", "", str(raw or "").strip())
                if text:
                    normalized.append(text)
            return normalized

        async def _build_section(title: str, items: List[str], fallback: str) -> Dict[str, Any]:
            lines = _normalize_lines(items)
            content = "\n".join([f"- {line}" for line in lines]) if lines else fallback
            
            # 当条目数达到优化阈值时触发教学优化
            if len(lines) >= MIN_ITEMS_FOR_OPTIMIZATION and content and content != fallback:
                try:
                    # 构建优化上下文
                    context = f"章节: {chapter_title}, 部分: {title}"
                    optimized_content = await self._optimize_with_timeout(
                        concept=title,
                        text=content,
                        context=context
                    )
                    if optimized_content and optimized_content != content:
                        content = optimized_content + " [教学优化后]"
                except Exception as e:
                    logger.debug(f"教学优化失败: {e}, title={title}, items_count={len(lines)}")
                    # 失败时保持原内容
            
            return {
                "title": title,
                "content": content,
                "metadata": {
                    "line_count": len(lines),
                    "optimized": len(lines) >= MIN_ITEMS_FOR_OPTIMIZATION,
                },
            }

        sections: List[Dict[str, str]] = []

        # 始终生成四个section，确保Tab2完整显示四个质量维度
        sections.append(await _build_section("核心要点", key_points, "暂无要点"))
        sections.append(await _build_section("章节分析", analysis, "暂无分析"))
        sections.append(await _build_section("建议与启发", recommendations, "暂无建议"))
        sections.append(await _build_section("风险与注意", risks, "暂无风险"))

        content_parts: List[str] = [str(section.get("content", "")).strip() for section in sections if str(section.get("content", "")).strip()]

        return {
            "chapter_key": str(item.get("chapter_key", "")).strip(),
            "chapter_title": chapter_title,
            "page_start": item.get("page_start"),
            "page_end": item.get("page_end"),
            "sections": sections,
            "content": "\n\n".join(content_parts).strip(),
        }

    async def retrieve_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        query = str(state.get("query", "")).strip()
        discipline = str(state.get("discipline", "all")).strip() or "all"
        embedding_mode = str(state.get("embedding_mode", "auto")).strip() or "auto"
        document_id = int(state.get("document_id", 0) or 0) or None
        top_k = int(state.get("top_k", 6) or 6)
        tenant_id = str(state.get("tenant_id", "")).strip() or None
        billing_client_id = str(state.get("billing_client_id", "")).strip() or None
        billing_exempt = bool(state.get("billing_exempt", False))
        retrieval = await self.rag_engine.prepare_agent_context(
            query=query,
            discipline_filter=discipline,
            document_id=document_id,
            top_k=top_k,
            compress_limit=min(4, top_k),
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
            embedding_mode=embedding_mode,
        )
        rows = retrieval.get("results", [])
        compressed_context = retrieval.get("compressed_context", "")

        # ── CA3 Pattern Completion: associative spread from top-k results ──
        if self.memory_hook:
            try:
                completer = self.memory_hook.get_completer()
                if completer and rows:
                    completed = completer.complete(
                        seed_chunks=rows[:3],
                        seed_embedding=None,
                        tenant_id=tenant_id or "public",
                        session_id=str(state.get("session_id", "default")),
                        top_k=4,
                    )
                    for cp in completed:
                        if cp.completed_text and cp.completed_text.strip():
                            compressed_context += f"\n\n[关联记忆]\n{cp.completed_text.strip()[:600]}"
            except Exception:
                pass  # pattern completion is best-effort

        return {
            "retrieved": rows,
            "compressed_context": compressed_context,
            "cross_discipline": retrieval.get("cross_discipline", []),
            "evidence": normalize_evidence(rows, limit=6),
        }

    async def recover_sparse_evidence(self, state: Dict[str, Any]) -> Dict[str, Any]:
        evidence = state.get("evidence", [])
        if isinstance(evidence, list) and len(evidence) >= 2:
            return {}
        query = str(state.get("query", "")).strip()
        discipline = str(state.get("discipline", "all")).strip() or "all"
        embedding_mode = str(state.get("embedding_mode", "auto")).strip() or "auto"
        document_id = int(state.get("document_id", 0) or 0) or None
        expanded_query = f"{query} 关键概念 定义 结论 证据"
        tenant_id = str(state.get("tenant_id", "")).strip() or None
        billing_client_id = str(state.get("billing_client_id", "")).strip() or None
        billing_exempt = bool(state.get("billing_exempt", False))
        retrieval = await self.rag_engine.prepare_agent_context(
            query=expanded_query,
            discipline_filter=discipline,
            document_id=document_id,
            top_k=max(8, int(state.get("top_k", 6) or 6)),
            compress_limit=6,
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
            embedding_mode=embedding_mode,
        )
        rows = retrieval.get("results", [])
        fallback_context = retrieval.get("compressed_context", state.get("compressed_context", ""))

        # ── CA3 Pattern Completion: associative spread from recovery results ──
        if self.memory_hook:
            try:
                completer = self.memory_hook.get_completer()
                if completer and rows:
                    completed = completer.complete(
                        seed_chunks=rows[:3],
                        seed_embedding=None,
                        tenant_id=tenant_id or "public",
                        session_id=str(state.get("session_id", "default")),
                        top_k=4,
                    )
                    for cp in completed:
                        if cp.completed_text and cp.completed_text.strip():
                            fallback_context += f"\n\n[关联记忆]\n{cp.completed_text.strip()[:600]}"
            except Exception:
                pass

        return {
            "retrieved": rows,
            "compressed_context": fallback_context,
            "cross_discipline": retrieval.get("cross_discipline", state.get("cross_discipline", [])),
            "evidence": normalize_evidence(rows, limit=6),
            "fallback_reason": "retry_retrieval",
        }

    async def retrieve_summary_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        query = str(state.get("query", "")).strip()
        discipline = str(state.get("discipline", "all")).strip() or "all"
        embedding_mode = str(state.get("embedding_mode", "auto")).strip() or "auto"
        document_id = int(state.get("document_id", 0) or 0) or None
        summary_mode = str(state.get("summary_mode", "fast")).strip().lower() or "fast"
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        tenant_id = str(state.get("tenant_id", "")).strip() or None
        billing_client_id = str(state.get("billing_client_id", "")).strip() or None
        billing_exempt = bool(state.get("billing_exempt", False))
        percent_cfg = _summary_percent_config_by_compact_level(compact_level)
        preferred_top_k = int(percent_cfg["retrieval_top_k"])
        estimated_doc_chunks = self.rag_engine.estimate_document_chunk_count(document_id, tenant_id=tenant_id)
        if summary_mode == "full":
            has_doc_id = isinstance(document_id, int) and document_id > 0
            full_limit = _full_mode_doc_limit(estimated_doc_chunks if estimated_doc_chunks > 0 else 480)
            doc_rows = self.rag_engine.load_document_chunks(
                document_id=document_id if has_doc_id else None,
                discipline_filter=discipline,
                limit=full_limit,
                tenant_id=tenant_id,
            )
            if doc_rows:
                return {
                    "retrieved": doc_rows,
                    "focus_blocks": [],
                    "cross_discipline": [],
                    "evidence": normalize_evidence(doc_rows, limit=6),
                    "estimated_doc_chunks": estimated_doc_chunks or len(doc_rows),
                }
        retrieval = await self.rag_engine.summary_search_with_qa_focus(
            query=query,
            discipline_filter=discipline,
            document_id=document_id,
            top_k=max(int(state.get("top_k", 8) or 8), preferred_top_k),
            max_qa_pairs=int(state.get("max_qa_pairs", 3) or 3),
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
            embedding_mode=embedding_mode,
        )
        rows = retrieval.get("results", [])
        return {
            "retrieved": rows,
            "focus_blocks": retrieval.get("focus_blocks", []),
            "cross_discipline": retrieval.get("cross_discipline", []),
            "evidence": normalize_evidence(rows, limit=6),
            "estimated_doc_chunks": estimated_doc_chunks,
        }

    async def compress_evidence(self, state: Dict[str, Any]) -> Dict[str, Any]:
        blocks: List[str] = []
        for row in state.get("retrieved", [])[:6]:
            blocks.append(f"[{row.get('title')}::{row.get('section_path')}]\n{str(row.get('content', ''))[:420]}")
        return {"compressed_context": "\n\n".join(blocks) if blocks else "暂无检索上下文"}

    async def generate_chat_contract(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reasoning = state.get("internal_reasoning", {}) if isinstance(state.get("internal_reasoning"), dict) else {}
        reasoning_hint = (
            f"主张: {reasoning.get('claim', '')}\n"
            f"证据摘要: {reasoning.get('evidence_summary', '')}\n"
            f"反例检查: {reasoning.get('counterexample_check', '')}\n"
            f"一致性检查: {reasoning.get('consistency_check', '')}\n"
        ).strip()
        prompt = (
            "你是学术问答Agent。仅返回JSON对象，字段: answer, brief_reasoning, purpose, subjects, subject_object_links, how_to, why。\n"
            "要求：brief_reasoning最多3条，且禁止输出完整推理链。\n"
            "其中：purpose为一句话；subjects/subject_object_links/how_to/why均为字符串数组，强调可执行步骤与因果解释。\n"
            f"问题：{state.get('query', '')}\n"
            f"模式：{state.get('mode', 'free')}\n"
            f"证据上下文：\n{state.get('compressed_context', '')}\n"
            f"内部推理参考（不要逐字复述）：\n{reasoning_hint}"
        )
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="answer",
            max_tokens=680,
            temperature=0.2,
            prefer_free=True,
        )
        parsed = parse_json_object(str(resp.get("content", "")))
        answer = sanitize_answer(parsed.get("answer") if parsed else resp.get("content", ""))
        brief_reasoning = sanitize_brief_reasoning(parsed.get("brief_reasoning") if parsed else [])
        five_dimensions, five_dimensions_meta = _resolve_five_dimensions(
            parsed if isinstance(parsed, dict) else {},
            query=str(state.get("query", "")).strip(),
            fallback={
                "purpose": answer[:180],
                "subjects": [str(state.get("discipline", "all"))],
                "subject_object_links": brief_reasoning[:2],
                "how_to": brief_reasoning[:3],
                "why": [str(reasoning.get("evidence_summary", "")).strip()],
            },
        )
        evidence = state.get("evidence", [])
        provider = str(resp.get("provider", "unknown"))
        free_tier_hit = provider in {"transformers-local", "github-models", "huggingface", "hash-fallback"}
        return {
            "answer": answer,
            "brief_reasoning": brief_reasoning,
            "five_dimensions": five_dimensions,
            "five_dimensions_meta": five_dimensions_meta,
            "provider": provider,
            "qa_regression_gates": build_reasoning_gates(answer, brief_reasoning, evidence),
            "cost_profile": {"prefer_free": True, "provider": provider, "free_tier_hit": free_tier_hit},
        }

    async def internal_reasoning_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        question_context = str(state.get("question_context", "")).strip()
        prompt = (
            "你是内部推理节点。只返回JSON对象，字段: prediction, claim, evidence_summary, counterexample_check, consistency_check。\n"
            "prediction: 在看到证据前，基于你的内部知识库预测答案方向（一句话）。\n"
            "要求：每个字段一句话，不输出多余解释。\n"
            f"问题：{state.get('query', '')}\n"
            f"证据上下文：\n{state.get('compressed_context', '')}"
        )
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="reason",
            max_tokens=520,
            temperature=0.2,
            prefer_free=True,
        )
        parsed = parse_json_object(str(resp.get("content", "")))
        reasoning = {
            "claim": str(parsed.get("claim", "")).strip()[:180],
            "evidence_summary": str(parsed.get("evidence_summary", "")).strip()[:220],
            "counterexample_check": str(parsed.get("counterexample_check", "")).strip()[:220],
            "consistency_check": str(parsed.get("consistency_check", "")).strip()[:180],
        }
        prediction = str(parsed.get("prediction", "")).strip()[:180]
        provider = str(resp.get("provider", state.get("provider", "unknown")))
        return {"internal_reasoning": reasoning, "provider": provider, "prediction": prediction, "predicted_answer": prediction}

    async def check_chat_quality(self, state: Dict[str, Any]) -> Dict[str, Any]:
        answer = str(state.get("answer", "")).strip()
        brief = state.get("brief_reasoning", [])
        evidence = state.get("evidence", [])
        evidence_coverage = len(evidence) >= 2
        has_answer = bool(answer)
        gates = build_reasoning_gates(answer, brief if isinstance(brief, list) else [], evidence if isinstance(evidence, list) else [])
        gates["evidence_coverage"] = evidence_coverage
        gates["non_empty_answer"] = has_answer
        failed = list(gates.get("failed_checks", []))
        if not evidence_coverage:
            failed.append("evidence_coverage")
        if not has_answer:
            failed.append("non_empty_answer")
        five_dimensions_meta = (
            state.get("five_dimensions_meta", {}) if isinstance(state.get("five_dimensions_meta"), dict) else {}
        )
        gates["five_dimensions_hit_rate"] = five_dimensions_meta.get("hit_rate", 0.0)
        gates["five_dimensions_source"] = five_dimensions_meta.get("source", "unknown")
        gates["failed_checks"] = failed
        gates["passed"] = len(failed) == 0
        patch: Dict[str, Any] = {"qa_regression_gates": gates, "quality_gates": gates}
        if not has_answer:
            patch["answer"] = "当前证据不足，建议补充更明确的问题边界或上传相关资料。"
            patch["brief_reasoning"] = ["触发空结果回退：未生成稳定答案。", "建议补充上下文后重试。"]
            patch["fallback_reason"] = "empty_answer"
        return patch

    async def generate_summary_contract(self, state: Dict[str, Any]) -> Dict[str, Any]:
        map_reduce_context = str(state.get("map_reduce_context", "")).strip()
        context_blocks = [map_reduce_context] if map_reduce_context else []
        context_blocks.extend(state.get("focus_blocks", []))
        summary_ctx_rows = _safe_int(os.getenv("SUMMARY_CONTEXT_ROWS", "6"), 6, 2)
        summary_ctx_clip = _safe_int(os.getenv("SUMMARY_CONTEXT_CLIP", "760"), 760, 240)
        for row in state.get("retrieved", [])[:summary_ctx_rows]:
            context_blocks.append(
                f"[{row.get('title')}::{row.get('section_path')}::{row.get('discipline')}]\n{str(row.get('content', ''))[:summary_ctx_clip]}"
            )
        prompt = (
            "你是超长文知识抽象Agent。仅返回JSON对象，字段: highlights, conclusions, actions, citations, purpose, subjects, subject_object_links, how_to, why。\n"
            "其中 citations 每项必须包含 title, discipline, section_path。\n"
            "其中 purpose为一句话；subjects/subject_object_links/how_to/why均为字符串数组，优先给具体步骤与因果解释。\n"
            f"问题：{state.get('query', '')}\n"
            f"上下文：\n{chr(10).join(context_blocks)}"
        )
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="summarize",
            max_tokens=780,
            temperature=0.2,
            prefer_free=True,
        )
        raw_model_content = str(resp.get("content", ""))
        parsed = parse_json_object(raw_model_content)
        if not parsed:
            parsed = {}
        highlights_raw = _to_list(parsed.get("highlights"))
        conclusions_raw = _to_list(parsed.get("conclusions"))
        actions_raw = _to_list(parsed.get("actions"))
        citations_raw = parsed.get("citations") if isinstance(parsed.get("citations"), list) else []
        highlights = highlights_raw or ["已完成核心信息梳理。"]
        conclusions = conclusions_raw or ["已形成可执行结论。"]
        actions = actions_raw or ["建议结合业务目标筛选行动项。"]
        citations = parsed.get("citations") if isinstance(parsed.get("citations"), list) else []
        if not citations:
            citations = state.get("evidence", [])[:6]
        if not citations:
            citations = [{"title": "基于当前检索未命中", "discipline": "all", "section_path": "N/A"}]
        brief = (highlights[:1] + conclusions[:1] + actions[:1])[:3]
        five_dimensions, five_dimensions_meta = _resolve_five_dimensions(
            parsed if isinstance(parsed, dict) else {},
            query=str(state.get("query", "")).strip(),
            fallback={
                "purpose": (conclusions[0] if conclusions else highlights[0])[:200],
                "subjects": _to_list(state.get("cross_discipline", []))[:4],
                "subject_object_links": highlights[:6],
                "how_to": actions[:8],
                "why": conclusions[:8],
            },
        )
        provider = str(resp.get("provider", "unknown"))
        free_tier_hit = provider in {"transformers-local", "github-models", "huggingface", "hash-fallback"}
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        percent_cfg = _summary_percent_config_by_compact_level(compact_level)
        summary_limits = _summary_limits_by_compact_level(compact_level, percent_cfg=percent_cfg)
        item_limit = int(summary_limits["item_limit"])
        char_limit = int(summary_limits["char_limit"])
        raw_lengths = _summary_length_stats(highlights, conclusions, actions)
        clipped_highlights = [x[:char_limit] for x in highlights[:item_limit]]
        clipped_conclusions = [x[:char_limit] for x in conclusions[:item_limit]]
        clipped_actions = [x[:char_limit] for x in actions[:item_limit]]
        clipped_lengths = _summary_length_stats(clipped_highlights, clipped_conclusions, clipped_actions)
        payload: Dict[str, Any] = {
            "summary": {
                "brief": sanitize_brief_reasoning(brief),
                "highlights": clipped_highlights,
                "conclusions": clipped_conclusions,
                "actions": clipped_actions,
                "citations": citations[:6],
            },
            "five_dimensions": five_dimensions,
            "five_dimensions_meta": five_dimensions_meta,
            "raw_lengths": raw_lengths,
            "clipped_lengths": clipped_lengths,
            "provider": provider,
            "cost_profile": {"prefer_free": True, "provider": provider, "free_tier_hit": free_tier_hit},
        }
        if bool(state.get("summary_debug_passthrough", False)):
            payload["raw_model_content"] = raw_model_content
            payload["parsed_before_clip"] = {
                "highlights": highlights_raw,
                "conclusions": conclusions_raw,
                "actions": actions_raw,
                "citations": citations_raw,
                "purpose": (parsed.get("purpose") if isinstance(parsed, dict) else ""),
                "subjects": (parsed.get("subjects") if isinstance(parsed, dict) else []),
                "subject_object_links": (parsed.get("subject_object_links") if isinstance(parsed, dict) else []),
                "how_to": (parsed.get("how_to") if isinstance(parsed, dict) else []),
                "why": (parsed.get("why") if isinstance(parsed, dict) else []),
            }
        return payload

    async def check_summary_quality(self, state: Dict[str, Any]) -> Dict[str, Any]:
        summary = state.get("summary", {}) if isinstance(state.get("summary"), dict) else {}
        highlights = summary.get("highlights", []) if isinstance(summary.get("highlights"), list) else []
        citations = summary.get("citations", []) if isinstance(summary.get("citations"), list) else []
        evidence = state.get("evidence", [])
        failed: List[str] = []
        if not highlights:
            failed.append("empty_highlights")
            summary["highlights"] = ["当前提炼结果偏弱，已触发回退填充。"]
        if not citations:
            failed.append("empty_citations")
            summary["citations"] = (evidence[:6] if isinstance(evidence, list) else [])
        if not summary.get("citations"):
            summary["citations"] = [{"title": "基于当前检索未命中", "discipline": "all", "section_path": "N/A"}]
        if not summary.get("brief"):
            summary["brief"] = sanitize_brief_reasoning(summary.get("highlights", [])[:1])
            failed.append("empty_brief")
        five_dimensions = state.get("five_dimensions", {}) if isinstance(state.get("five_dimensions"), dict) else {}
        five_dimensions_meta = (
            state.get("five_dimensions_meta", {}) if isinstance(state.get("five_dimensions_meta"), dict) else {}
        )
        if not five_dimensions:
            five_dimensions, five_dimensions_meta = _resolve_five_dimensions(
                {},
                query=str(state.get("query", "")).strip(),
                fallback={
                    "purpose": (summary.get("conclusions", [""])[0] if summary.get("conclusions") else "")[:200],
                    "subjects": [],
                    "subject_object_links": summary.get("highlights", []),
                    "how_to": summary.get("actions", []),
                    "why": summary.get("conclusions", []),
                },
            )
        gates = {
            "evidence_coverage": bool(summary.get("citations")),
            "structure_complete": bool(summary.get("highlights")) and bool(summary.get("conclusions")) and bool(summary.get("actions")),
            "five_dimensions_hit_rate": five_dimensions_meta.get("hit_rate", 0.0),
            "five_dimensions_source": five_dimensions_meta.get("source", "unknown"),
            "passed": len(failed) == 0,
            "failed_checks": failed,
        }
        patch = {
            "summary": summary,
            "quality_gates": gates,
            "five_dimensions": five_dimensions,
            "five_dimensions_meta": five_dimensions_meta,
        }
        if failed:
            patch["fallback_reason"] = "summary_quality_guard"
        return patch

    async def map_reduce_summary(self, state: Dict[str, Any]) -> Dict[str, Any]:
        rows = state.get("retrieved", [])
        if not isinstance(rows, list) or not rows:
            return {"map_reduce_context": ""}
        summary_mode = str(state.get("summary_mode", "fast")).strip().lower() or "fast"
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        percent_cfg = _summary_percent_config_by_compact_level(compact_level)
        map_reduce_cfg = _map_reduce_config_by_compact_level(compact_level, percent_cfg=percent_cfg)
        estimated_total = _estimate_summary_scale(state, rows)
        coverage_ratio = float(percent_cfg["coverage_ratio"])
        coverage_floor = int(percent_cfg["coverage_floor"])
        if summary_mode == "full":
            coverage_ratio = max(coverage_ratio, 0.75)
            coverage_floor = max(coverage_floor, int(map_reduce_cfg.get("full_coverage_floor", 24)))
        candidate_target = max(coverage_floor, int(round(estimated_total * coverage_ratio)))
        candidate_limit = max(1, min(len(rows), candidate_target))
        candidate_rows = rows[:candidate_limit]
        group_size = int(map_reduce_cfg["group_size"])
        if summary_mode == "full":
            group_size = int(map_reduce_cfg.get("full_group_size", max(8, group_size)))
        groups = _chunk_rows(candidate_rows, size=group_size)
        max_concurrency = max(1, min(4, int(state.get("map_max_concurrency", 3) or 3)))
        semaphore = asyncio.Semaphore(max_concurrency)
        progress_cb = state.get("_progress_callback")
        map_done_count = 0
        total_groups = len(groups)

        async def _run_map_group(idx: int, group: List[Any]) -> Dict[str, Any]:
            nonlocal map_done_count
            group_blocks = []
            for row in group:
                group_blocks.append(
                    f"[{row.get('title')}::{row.get('section_path')}::{row.get('discipline')}]\n"
                    f"{str(row.get('content', ''))[:520]}"
                )
            map_prompt = (
                "你是超长学术文本Map阶段摘要Agent。只返回JSON对象，字段为 highlights, conclusions, actions。\n"
                f"每个字段为数组，最多{int(map_reduce_cfg['map_item_limit'])}条；"
                f"每条写成{str(map_reduce_cfg['map_sentence_range'])}句的信息块，"
                "至少包含背景/证据/影响中的两项，不输出推理过程。\n"
                f"问题：{state.get('query', '')}\n"
                f"分组#{idx}上下文：\n{chr(10).join(group_blocks)}"
            )
            async with semaphore:
                map_resp = await self.ai_router.chat_with_task(
                    [{"role": "user", "content": map_prompt}],
                    task_type="summarize",
                    max_tokens=int(map_reduce_cfg["map_max_tokens"]),
                    temperature=0.2,
                    prefer_free=True,
                )
            parsed = parse_json_object(str(map_resp.get("content", "")))
            map_item_limit = int(map_reduce_cfg["map_item_limit"])
            map_done_count += 1
            if callable(progress_cb):
                try:
                    progress_cb({"stage": "map", "current": map_done_count, "total": total_groups, "message": f"Map 分组 {map_done_count}/{total_groups}"})
                except Exception:
                    pass
            return {
                "provider": str(map_resp.get("provider", "unknown")),
                "highlights": _to_list(parsed.get("highlights") if isinstance(parsed, dict) else [])[:map_item_limit],
                "conclusions": _to_list(parsed.get("conclusions") if isinstance(parsed, dict) else [])[:map_item_limit],
                "actions": _to_list(parsed.get("actions") if isinstance(parsed, dict) else [])[:map_item_limit],
            }

        map_outputs = await asyncio.gather(
            *[_run_map_group(idx, group) for idx, group in enumerate(groups, start=1)]
        )
        if summary_mode == "full" and len(map_outputs) > int(map_reduce_cfg.get("second_reduce_trigger", 8)):
            stage2_groups = _chunk_rows(map_outputs, size=int(map_reduce_cfg.get("second_reduce_group_size", 6)))
            stage2_outputs: List[Dict[str, Any]] = []
            for stage_idx, stage_group in enumerate(stage2_groups, start=1):
                stage_blocks = []
                for item_idx, item in enumerate(stage_group, start=1):
                    stage_blocks.append(
                        f"Map2Input#{item_idx}\n"
                        f"- highlights: {'；'.join(item.get('highlights', [])[:int(map_reduce_cfg['map_item_limit'])])}\n"
                        f"- conclusions: {'；'.join(item.get('conclusions', [])[:int(map_reduce_cfg['map_item_limit'])])}\n"
                        f"- actions: {'；'.join(item.get('actions', [])[:int(map_reduce_cfg['map_item_limit'])])}"
                    )
                stage_prompt = (
                    "你是超长学术文本二层Reduce聚合Agent。请聚合同组Map输出。\n"
                    f"仅返回JSON对象，字段：highlights, conclusions, actions；每个字段最多{int(map_reduce_cfg['reduce_item_limit'])}条。\n"
                    f"每条写成{str(map_reduce_cfg['reduce_sentence_range'])}句信息块，强调证据与影响。\n"
                    f"问题：{state.get('query', '')}\n"
                    f"同组Map结果：\n{chr(10).join(stage_blocks)}"
                )
                async with semaphore:
                    stage_resp = await self.ai_router.chat_with_task(
                        [{"role": "user", "content": stage_prompt}],
                        task_type="summarize",
                        max_tokens=int(map_reduce_cfg.get("second_reduce_max_tokens", map_reduce_cfg["reduce_max_tokens"])),
                        temperature=0.2,
                        prefer_free=True,
                    )
                stage_parsed = parse_json_object(str(stage_resp.get("content", "")))
                stage2_outputs.append(
                    {
                        "provider": str(stage_resp.get("provider", "unknown")),
                        "highlights": _to_list(stage_parsed.get("highlights") if isinstance(stage_parsed, dict) else []),
                        "conclusions": _to_list(stage_parsed.get("conclusions") if isinstance(stage_parsed, dict) else []),
                        "actions": _to_list(stage_parsed.get("actions") if isinstance(stage_parsed, dict) else []),
                    }
                )
            map_outputs = stage2_outputs
        used_provider = next(
            (str(item.get("provider", "")).strip() for item in reversed(map_outputs) if str(item.get("provider", "")).strip()),
            "unknown",
        )

        reduce_input_blocks = []
        for idx, item in enumerate(map_outputs, start=1):
            reduce_input_blocks.append(
                f"Map#{idx}\n"
                f"- highlights: {'；'.join(item.get('highlights', [])[:int(map_reduce_cfg['map_item_limit'])])}\n"
                f"- conclusions: {'；'.join(item.get('conclusions', [])[:int(map_reduce_cfg['map_item_limit'])])}\n"
                f"- actions: {'；'.join(item.get('actions', [])[:int(map_reduce_cfg['map_item_limit'])])}"
            )
        reduce_item_limit = int(map_reduce_cfg["reduce_item_limit"])
        reduce_prompt = (
            "你是超长学术文本Reduce阶段聚合Agent。请将多个Map结果合并为统一摘要基础。\n"
            f"只返回JSON对象，字段：highlights, conclusions, actions；每个字段最多{reduce_item_limit}条。\n"
            f"每条写成{str(map_reduce_cfg['reduce_sentence_range'])}句的信息块，优先保留可追溯证据，并说明影响或执行含义。\n"
            f"问题：{state.get('query', '')}\n"
            f"Map结果：\n{chr(10).join(reduce_input_blocks)}"
        )
        if callable(progress_cb):
            try:
                progress_cb({"stage": "reduce", "message": "聚合 Map 结果..."})
            except Exception:
                pass
        reduce_resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": reduce_prompt}],
            task_type="summarize",
            max_tokens=int(map_reduce_cfg["reduce_max_tokens"]),
            temperature=0.2,
            prefer_free=True,
        )
        used_provider = str(reduce_resp.get("provider", used_provider))
        reduced = parse_json_object(str(reduce_resp.get("content", "")))
        highlights = _to_list(reduced.get("highlights") if isinstance(reduced, dict) else [])[:reduce_item_limit]
        conclusions = _to_list(reduced.get("conclusions") if isinstance(reduced, dict) else [])[:reduce_item_limit]
        actions = _to_list(reduced.get("actions") if isinstance(reduced, dict) else [])[:reduce_item_limit]

        basis = (
            "Map-Reduce摘要基础：\n"
            f"- highlights: {'；'.join(highlights) if highlights else 'N/A'}\n"
            f"- conclusions: {'；'.join(conclusions) if conclusions else 'N/A'}\n"
            f"- actions: {'；'.join(actions) if actions else 'N/A'}"
        )
        return {
            "map_reduce_context": basis,
            "provider": used_provider,
            "effective_coverage": {
                "estimated_total": estimated_total,
                "candidate_rows": candidate_limit,
                "coverage_ratio": round((candidate_limit / max(estimated_total, 1)), 4),
            },
            "coverage_stats": {
                "mode": summary_mode,
                "processed_rows": candidate_limit,
                "total_rows": estimated_total,
                "coverage_ratio": round((candidate_limit / max(estimated_total, 1)), 4),
                "map_groups": len(groups),
            },
        }

    async def retrieve_report_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        query = str(state.get("query", "")).strip()
        discipline = str(state.get("discipline", "all")).strip() or "all"
        embedding_mode = str(state.get("embedding_mode", "auto")).strip() or "auto"
        document_id = int(state.get("document_id", 0) or 0) or None
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        tenant_id = str(state.get("tenant_id", "")).strip() or None
        billing_client_id = str(state.get("billing_client_id", "")).strip() or None
        billing_exempt = bool(state.get("billing_exempt", False))
        report_cfg = _report_config_by_compact_level(compact_level)
        estimated_doc_chunks = self.rag_engine.estimate_document_chunk_count(document_id, tenant_id=tenant_id)
        has_doc_id = isinstance(document_id, int) and document_id > 0
        full_runtime = _report_full_runtime_config()
        doc_rows = self.rag_engine.load_document_chunks(
            document_id=document_id if has_doc_id else None,
            discipline_filter=discipline,
            limit=None,
            sampling_strategy="head",
            tenant_id=tenant_id,
        )
        if doc_rows:
            raw_total = len(doc_rows)
            return {
                "retrieved": doc_rows,
                "focus_blocks": [],
                "cross_discipline": [],
                "evidence": normalize_evidence(doc_rows, limit=8),
                "estimated_doc_chunks": estimated_doc_chunks or raw_total,
                "retrieval_stats": {
                    "retrieval_strategy": "doc_chunks_full_scan",
                    "doc_limit": 0,
                    "retrieved_rows": raw_total,
                    "raw_total_chunks": raw_total,
                    "estimated_doc_chunks": estimated_doc_chunks or raw_total,
                    "full_require_complete": bool(full_runtime["require_complete"]),
                },
            }
        retrieval = await self.rag_engine.summary_search_with_qa_focus(
            query=query,
            discipline_filter=discipline,
            document_id=document_id,
            top_k=int(report_cfg["fallback_top_k"]),
            max_qa_pairs=4,
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
            embedding_mode=embedding_mode,
        )
        rows = retrieval.get("results", [])
        return {
            "retrieved": rows,
            "focus_blocks": retrieval.get("focus_blocks", []),
            "cross_discipline": retrieval.get("cross_discipline", []),
            "evidence": normalize_evidence(rows, limit=8),
            "estimated_doc_chunks": estimated_doc_chunks,
            "retrieval_stats": {
                "retrieval_strategy": "hybrid_fallback",
                "doc_limit": 0,
                "retrieved_rows": len(rows),
                "estimated_doc_chunks": estimated_doc_chunks,
            },
        }

    async def _legacy_map_reduce_report(self, state: Dict[str, Any]) -> Dict[str, Any]:
        rows = state.get("retrieved", [])
        if not isinstance(rows, list) or not rows:
            return {"report_reduce_context": "", "coverage_stats": {"mode": "full", "processed_rows": 0, "total_rows": 0, "coverage_ratio": 0.0, "map_groups": 0}}
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        report_cfg = _report_config_by_compact_level(compact_level)
        estimated_total = _estimate_summary_scale(state, rows)
        full_runtime = _report_full_runtime_config()
        detail_runtime = _detail_first_runtime_config()
        candidate_rows = rows
        batch_size = int(full_runtime["batch_size"])
        groups = _chunk_rows(candidate_rows, size=batch_size)

        max_concurrency = int(full_runtime["max_concurrency"])
        max_concurrency = max(1, min(6, max_concurrency))
        semaphore = asyncio.Semaphore(max_concurrency)
        progress_cb = state.get("_progress_callback")
        report_map_done = 0
        report_total_groups = len(groups)

        async def _run_report_map(idx: int, group: List[Any]) -> Dict[str, Any]:
            nonlocal report_map_done
            blocks = []
            for row in group:
                blocks.append(
                    f"[{row.get('title')}::{row.get('section_path')}::{row.get('discipline')}]\n"
                    f"{str(row.get('content', ''))[:int(max(report_cfg['content_clip'], detail_runtime['report_content_clip']))]}"
                )
            prompt = (
                "你是一个资深的文档分析专家。请根据以下上下文提取核心信息，以‘说明文’风格进行叙述。仅返回JSON对象，字段: key_points, analysis, recommendations, risks。\n"
                "【关键要求】：\n"
                "- 每个字段都必须返回**一个完整的、连贯的中文段落**，而不是列表或分点。\n"
                "- key_points: 将所有核心事实融合成一个自然的段落，使用'首先'、'其次'、'此外'等连接词。\n"
                "- analysis: 使用连贯的逻辑叙述，如'基于上述事实...'、'因此...'等句式分析事实关系。\n"
                "- recommendations/risks: 使用完整句子的建议和警示，避免要点式列举。\n"
                "- 绝对禁止返回列表格式（如['要点1', '要点2']），必须返回纯文本段落。\n"
                "- 语气稳重专业，每个字段段落长度约100-200字。\n"
                f"问题：{state.get('query', '')}\n"
                f"分组#{idx}上下文：\n{chr(10).join(blocks)}"
            )
            async with semaphore:
                resp = await self.ai_router.chat_with_task(
                    [{"role": "user", "content": prompt}],
                    task_type="summarize",
                    max_tokens=int(report_cfg["map_max_tokens"]),
                    temperature=0.2,
                    prefer_free=True,
                )
            parsed = parse_json_object(str(resp.get("content", "")))
            report_map_done += 1
            if callable(progress_cb):
                try:
                    progress_cb({"stage": "map", "current": report_map_done, "total": report_total_groups, "message": f"Map 分组 {report_map_done}/{report_total_groups}"})
                except Exception:
                    pass
            return {
                "provider": str(resp.get("provider", "unknown")),
                "key_points": _to_list(parsed.get("key_points") if isinstance(parsed, dict) else []),
                "analysis": _to_list(parsed.get("analysis") if isinstance(parsed, dict) else []),
                "recommendations": _to_list(parsed.get("recommendations") if isinstance(parsed, dict) else []),
                "risks": _to_list(parsed.get("risks") if isinstance(parsed, dict) else []),
            }

        map_outputs = await asyncio.gather(*[_run_report_map(i, g) for i, g in enumerate(groups, start=1)])
        if callable(progress_cb):
            try:
                progress_cb({"stage": "reduce", "message": "聚合 Map 结果..."})
            except Exception:
                pass
        stage2_groups = _chunk_rows(map_outputs, size=int(report_cfg["stage2_group_size"]))
        stage2_outputs: List[Dict[str, Any]] = []
        for idx, group in enumerate(stage2_groups, start=1):
            parts = []
            for j, item in enumerate(group, start=1):
                parts.append(
                    f"Group{j}\n"
                    f"- key_points: {'；'.join(item.get('key_points', [])[:int(detail_runtime['stage2_item_limit'])])}\n"
                    f"- analysis: {'；'.join(item.get('analysis', [])[:int(detail_runtime['stage2_item_limit'])])}\n"
                    f"- recommendations: {'；'.join(item.get('recommendations', [])[:int(detail_runtime['stage2_item_limit'])])}\n"
                    f"- risks: {'；'.join(item.get('risks', [])[:int(detail_runtime['stage2_item_limit'])])}"
                )
            prompt = (
                "你是长文报告二层聚合节点。仅返回JSON对象，字段: key_points, analysis, recommendations, risks。\n"
                f"问题：{state.get('query', '')}\n"
                f"二层分组#{idx}输入：\n{chr(10).join(parts)}"
            )
            resp = await self.ai_router.chat_with_task(
                [{"role": "user", "content": prompt}],
                task_type="summarize",
                max_tokens=int(report_cfg["stage2_max_tokens"]),
                temperature=0.2,
                prefer_free=True,
            )
            parsed = parse_json_object(str(resp.get("content", "")))
            stage2_outputs.append(
                {
                    "provider": str(resp.get("provider", "unknown")),
                    "key_points": _to_list(parsed.get("key_points") if isinstance(parsed, dict) else []),
                    "analysis": _to_list(parsed.get("analysis") if isinstance(parsed, dict) else []),
                    "recommendations": _to_list(parsed.get("recommendations") if isinstance(parsed, dict) else []),
                    "risks": _to_list(parsed.get("risks") if isinstance(parsed, dict) else []),
                }
            )
        blocks = []
        for idx, item in enumerate(stage2_outputs, start=1):
            blocks.append(
                f"Stage2#{idx}\n"
                f"- key_points: {'；'.join(item.get('key_points', [])[:int(detail_runtime['final_item_limit'])])}\n"
                f"- analysis: {'；'.join(item.get('analysis', [])[:int(detail_runtime['final_item_limit'])])}\n"
                f"- recommendations: {'；'.join(item.get('recommendations', [])[:int(detail_runtime['final_item_limit'])])}\n"
                f"- risks: {'；'.join(item.get('risks', [])[:int(detail_runtime['final_item_limit'])])}"
            )
        report_reduce_context = "\n\n".join(blocks)
        retrieval_stats = state.get("retrieval_stats", {}) if isinstance(state.get("retrieval_stats"), dict) else {}
        raw_retrieved = int(retrieval_stats.get("retrieved_rows", len(rows)) or len(rows))
        raw_total = int(retrieval_stats.get("raw_total_chunks", 0) or 0)
        if raw_total <= 0:
            raw_total = raw_retrieved if raw_retrieved > 0 else len(rows)
        processed_rows = len(candidate_rows)
        coverage_ratio = round((processed_rows / max(raw_total, 1)), 4)
        return {
            "report_reduce_context": report_reduce_context,
            "coverage_stats": {
                "mode": "full",
                "processed_rows": processed_rows,
                "total_rows": estimated_total,
                "coverage_ratio": coverage_ratio,
                "map_groups": len(groups),
                "raw_total_chunks": raw_total,
                "after_doc_limit": raw_retrieved,
                "after_candidate_limit": len(candidate_rows),
                "retrieval_strategy": str(retrieval_stats.get("retrieval_strategy", "unknown")),
                "full_require_complete": bool(full_runtime["require_complete"]),
            },
            "provider": (stage2_outputs[-1].get("provider", "unknown") if stage2_outputs else (map_outputs[-1].get("provider", "unknown") if map_outputs else "unknown")),
        }

    async def map_reduce_report(self, state: Dict[str, Any]) -> Dict[str, Any]:
        rows = state.get("retrieved", [])
        if not isinstance(rows, list) or not rows:
            return {
                "report_reduce_context": "",
                "coverage_stats": {"mode": "full", "processed_rows": 0, "total_rows": 0, "coverage_ratio": 0.0, "map_groups": 0},
            }
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        report_cfg = _report_config_by_compact_level(compact_level)
        estimated_total = _estimate_summary_scale(state, rows)
        full_runtime = _report_full_runtime_config()
        detail_runtime = _detail_first_runtime_config()
        candidate_rows = rows
        batch_size = int(full_runtime["batch_size"])
        groups = _group_rows_for_hierarchical_report(candidate_rows, chunk_size=batch_size)

        max_concurrency = int(full_runtime["max_concurrency"])
        max_concurrency = max(1, min(6, max_concurrency))
        semaphore = asyncio.Semaphore(max_concurrency)
        progress_cb = state.get("_progress_callback")
        report_map_done = 0
        report_total_groups = len(groups)

        async def _run_report_map(idx: int, group: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal report_map_done
            blocks: List[str] = []
            rows_in_group = group.get("rows", []) if isinstance(group.get("rows"), list) else []
            for row in rows_in_group:
                blocks.append(
                    f"[{row.get('title')}::{row.get('section_path')}::{row.get('discipline')}]\n"
                    f"{str(row.get('content', ''))[:int(max(report_cfg['content_clip'], detail_runtime['report_content_clip']))]}"
                )
            chapter_title = str(group.get("chapter_title", "") or "章节")
            segment_label = str(group.get("segment_label", "") or chapter_title)
            page_span = _format_page_span(group.get("page_start"), group.get("page_end"))
            prompt = (
                "你是一个精密的文档事实提取专家。你的任务是构建高质量的‘说明性事实与证据库’。请仅返回JSON对象，包含字段: key_points, analysis, recommendations, risks。\n"
                "【关键指令】：\n"
                "- 每个字段都必须返回**一个完整的、连贯的中文段落**，而不是列表或分点。\n"
                "- key_points: 将所有核心事实融合成一个自然的段落，包含具体的条款编号、硬性指标、原始定义，使用专业地道的说明文风格。\n"
                "- analysis: 使用连贯的逻辑叙述分析事实间的因果与支撑逻辑，侧重还原‘事实A如何支撑结论B’的逻辑链路。\n"
                "- recommendations: 将文档明确要求的执行准则或具体措施组织成完整的段落建议。\n"
                "- risks: 将文档指出的具体禁止红线、惩罚条款组织成连贯的风险警示段落。\n"
                "- 绝对禁止返回列表格式（如['要点1', '要点2']），必须返回纯文本段落。\n"
                "- 段落长度约150-300字，确保结果既专业又易读。\n"
                f"用户查询：{state.get('query', '')}\n"
                f"当前章节：{chapter_title}\n"
                f"当前段落：{segment_label}{page_span}\n"
                f"文档内容：\n{chr(10).join(blocks)}"
            )
            async with semaphore:
                resp = await self.ai_router.chat_with_task(
                    [{"role": "user", "content": prompt}],
                    task_type="summarize",
                    max_tokens=int(report_cfg["map_max_tokens"]),
                    temperature=0.2,
                    prefer_free=True,
                )
            parsed = parse_json_object(str(resp.get("content", "")))
            report_map_done += 1
            if callable(progress_cb):
                try:
                    progress_cb({"stage": "map", "current": report_map_done, "total": report_total_groups, "message": f"Map 章节分组 {report_map_done}/{report_total_groups}"})
                except Exception:
                    pass
            return {
                "chapter_key": str(group.get("chapter_key", "")),
                "chapter_title": chapter_title,
                "segment_label": segment_label,
                "page_start": group.get("page_start"),
                "page_end": group.get("page_end"),
                "provider": str(resp.get("provider", "unknown")),
                "key_points": _to_list(parsed.get("key_points") if isinstance(parsed, dict) else []),
                "analysis": _to_list(parsed.get("analysis") if isinstance(parsed, dict) else []),
                "recommendations": _to_list(parsed.get("recommendations") if isinstance(parsed, dict) else []),
                "risks": _to_list(parsed.get("risks") if isinstance(parsed, dict) else []),
            }

        map_outputs = await asyncio.gather(*[_run_report_map(i, g) for i, g in enumerate(groups, start=1)])
        if callable(progress_cb):
            try:
                progress_cb({"stage": "reduce", "message": "聚合章节结果..."})
            except Exception:
                pass

        chapter_outputs: List[Dict[str, Any]] = []
        chapter_groups = _group_map_outputs_by_chapter(map_outputs)
        for idx, group in enumerate(chapter_groups, start=1):
            chapter_items = group.get("items", []) if isinstance(group.get("items"), list) else []
            if len(chapter_items) == 1:
                item = dict(chapter_items[0])
                item["segment_label"] = str(group.get("chapter_title", item.get("segment_label", "章节")))
                chapter_outputs.append(item)
                continue
            parts: List[str] = []
            for j, item in enumerate(chapter_items, start=1):
                parts.append(
                    f"Part{j}\n"
                    f"- key_points: {'; '.join(item.get('key_points', [])[:int(detail_runtime['stage2_item_limit'])])}\n"
                    f"- analysis: {'; '.join(item.get('analysis', [])[:int(detail_runtime['stage2_item_limit'])])}\n"
                    f"- recommendations: {'; '.join(item.get('recommendations', [])[:int(detail_runtime['stage2_item_limit'])])}\n"
                    f"- risks: {'; '.join(item.get('risks', [])[:int(detail_runtime['stage2_item_limit'])])}"
                )
            page_span = _format_page_span(group.get("page_start"), group.get("page_end"))
            prompt = (
                "你是一个资深的报告聚合专家。请将以下同一章节不同部分的片段信息聚合为连贯、专业的‘章节级事实综述’。仅返回JSON对象，包含字段: key_points, analysis, recommendations, risks。\n"
                "【聚合要求】：\n"
                "- 使用规范的说明文语言，语气稳重且逻辑严密。\n"
                "- 归纳并去重时保留核心事实，将离散的要点串联成有深度的陈述句，严禁关键词堆砌。\n"
                "- key_points: 归并核心事实证据；analysis: 整合因果逻辑；recommendations/risks: 总结执行准则与红线。\n"
                "每个字段应返回2-5个高质量且逻辑连贯的段落式短句。\n"
                f"用户查询：{state.get('query', '')}\n"
                f"章节：#{idx} {group.get('chapter_title', '章节')}{page_span}\n"
                f"各部分摘要：\n{chr(10).join(parts)}"
            )
            resp = await self.ai_router.chat_with_task(
                [{"role": "user", "content": prompt}],
                task_type="summarize",
                max_tokens=int(report_cfg["stage2_max_tokens"]),
                temperature=0.2,
                prefer_free=True,
            )
            parsed = parse_json_object(str(resp.get("content", "")))
            chapter_outputs.append(
                {
                    "chapter_key": str(group.get("chapter_key", "")),
                    "chapter_title": str(group.get("chapter_title", "章节")),
                    "segment_label": str(group.get("chapter_title", "章节")),
                    "page_start": group.get("page_start"),
                    "page_end": group.get("page_end"),
                    "provider": str(resp.get("provider", "unknown")),
                    "key_points": _to_list(parsed.get("key_points") if isinstance(parsed, dict) else []),
                    "analysis": _to_list(parsed.get("analysis") if isinstance(parsed, dict) else []),
                    "recommendations": _to_list(parsed.get("recommendations") if isinstance(parsed, dict) else []),
                    "risks": _to_list(parsed.get("risks") if isinstance(parsed, dict) else []),
                }
            )

        if _prefer_chinese_for_long_doc(rows):
            translated_outputs: List[Dict[str, Any]] = []
            for item in chapter_outputs:
                translated_outputs.append(await self._ensure_chapter_output_language(item, prefer_chinese=True))
            chapter_outputs = translated_outputs

        stage2_groups = _chunk_rows(chapter_outputs, size=int(report_cfg["stage2_group_size"]))
        stage2_outputs: List[Dict[str, Any]] = []
        for idx, group in enumerate(stage2_groups, start=1):
            parts: List[str] = []
            for j, item in enumerate(group, start=1):
                page_span = _format_page_span(item.get("page_start"), item.get("page_end"))
                parts.append(
                    f"Chapter{j}: {item.get('chapter_title', '章节')}{page_span}\n"
                    f"- key_points: {'; '.join(item.get('key_points', [])[:int(detail_runtime['stage2_item_limit'])])}\n"
                    f"- analysis: {'; '.join(item.get('analysis', [])[:int(detail_runtime['stage2_item_limit'])])}\n"
                    f"- recommendations: {'; '.join(item.get('recommendations', [])[:int(detail_runtime['stage2_item_limit'])])}\n"
                    f"- risks: {'; '.join(item.get('risks', [])[:int(detail_runtime['stage2_item_limit'])])}"
                )
            prompt = (
                "你是一个全局事实提炼专家。请根据以下各章节的事实摘要，生成更高层级的‘全局事实综述’。仅返回JSON对象，包含字段: key_points, analysis, recommendations, risks。\n"
                "注意：这是为后续‘战略分析报告’提供的基础事实库。请确保提炼的内容具有全局性，覆盖多章节的核心价值。\n"
                "【要求】：\n"
                "- 使用规范的说明文语言，语气宏大且逻辑连贯。\n"
                "- key_points应提炼最关键的全局要点；analysis应提供跨章节的逻辑关联总结。\n"
                "- 避免机械堆砌，每个字段返回2到5个精炼且具有深度分析价值的陈述句。\n"
                f"用户查询：{state.get('query', '')}\n"
                f"章节摘要：\n{chr(10).join(parts)}"
            )
            resp = await self.ai_router.chat_with_task(
                [{"role": "user", "content": prompt}],
                task_type="summarize",
                max_tokens=int(report_cfg["stage2_max_tokens"]),
                temperature=0.2,
                prefer_free=True,
            )
            parsed = parse_json_object(str(resp.get("content", "")))
            stage2_outputs.append(
                {
                    "provider": str(resp.get("provider", "unknown")),
                    "key_points": _to_list(parsed.get("key_points") if isinstance(parsed, dict) else []),
                    "analysis": _to_list(parsed.get("analysis") if isinstance(parsed, dict) else []),
                    "recommendations": _to_list(parsed.get("recommendations") if isinstance(parsed, dict) else []),
                    "risks": _to_list(parsed.get("risks") if isinstance(parsed, dict) else []),
                }
            )

        # 添加全局综述的最终合并步骤
        unified_global_summary: Optional[Dict[str, Any]] = None
        if stage2_outputs and len(stage2_outputs) > 1:
            # 如果有多个Part，进行最终合并
            try:
                unified_global_summary = await self._merge_global_summaries(stage2_outputs, state, report_cfg, detail_runtime)
            except Exception as e:
                logger.warning(f"Failed to merge global summaries: {e}")
                unified_global_summary = None
        
        blocks: List[str] = []
        total_chars = sum(len(str(r.get("content", ""))) for r in candidate_rows)
        # 如果总字符数不多（例如 < 6000 字），直接将原始文本分块作为上下文传给最终报告生成阶段
        # 这样可以避免“摘要的摘要”导致的内容趋同
        if total_chars < 6000:
            for i, row in enumerate(candidate_rows[:30]):  # 限制分块数量防止超限
                page_num = _row_page_num(row)
                blocks.append(
                    f"### 原文片段#{i+1} (第{page_num}页, {row.get('section_path', '章节')}):\n"
                    f"{str(row.get('content', ''))[:800]}"
                )
        elif unified_global_summary:
            # 使用统一的全局综述
            blocks.append(
                f"### 全局综述（统一整合）\n"
                f"- 核心点: {' '.join(unified_global_summary.get('key_points', [])[:int(detail_runtime['final_item_limit'])])}\n"
                f"- 深度分析: {' '.join(unified_global_summary.get('analysis', [])[:int(detail_runtime['final_item_limit'])])}\n"
                f"- 建议与风险: {' '.join(unified_global_summary.get('recommendations', [])[:int(detail_runtime['final_item_limit'])])} {' '.join(unified_global_summary.get('risks', [])[:int(detail_runtime['final_item_limit'])])}"
            )
        elif stage2_outputs:
            # 对于长文档，使用 Stage2 聚合结果
            for idx, item in enumerate(stage2_outputs, start=1):
                blocks.append(
                    f"### 全局综述 Part#{idx}\n"
                    f"- 核心点: {' '.join(item.get('key_points', [])[:int(detail_runtime['final_item_limit'])])}\n"
                    f"- 深度分析: {' '.join(item.get('analysis', [])[:int(detail_runtime['final_item_limit'])])}\n"
                    f"- 建议与风险: {' '.join(item.get('recommendations', [])[:int(detail_runtime['final_item_limit'])])} {' '.join(item.get('risks', [])[:int(detail_runtime['final_item_limit'])])}"
                )
        else:
            # 回退到章节摘要
            for item in chapter_outputs[: max(8, int(detail_runtime["stage2_item_limit"]))]:
                page_span = _format_page_span(item.get("page_start"), item.get("page_end"))
                blocks.append(
                    f"### 章节摘要: {item.get('chapter_title', '章节')}{page_span}\n"
                    f"- 核心事实: {' '.join(item.get('key_points', [])[:int(detail_runtime['final_item_limit'])])}\n"
                    f"- 事实逻辑: {' '.join(item.get('analysis', [])[:int(detail_runtime['final_item_limit'])])}\n"
                    f"- 明确要求/合规建议: {' '.join(item.get('recommendations', [])[:int(detail_runtime['final_item_limit'])])}\n"
                    f"- 禁止项/客观风险: {' '.join(item.get('risks', [])[:int(detail_runtime['final_item_limit'])])}"
                )
        report_reduce_context = "\n\n".join(blocks)
        retrieval_stats = state.get("retrieval_stats", {}) if isinstance(state.get("retrieval_stats"), dict) else {}
        raw_retrieved = int(retrieval_stats.get("retrieved_rows", len(rows)) or len(rows))
        raw_total = int(retrieval_stats.get("raw_total_chunks", 0) or 0)
        if raw_total <= 0:
            raw_total = raw_retrieved if raw_retrieved > 0 else len(rows)
        processed_rows = len(candidate_rows)
        coverage_ratio = round((processed_rows / max(raw_total, 1)), 4)
        return {
            "report_reduce_context": report_reduce_context,
            "chapter_summaries": [await self._normalize_chapter_summary_item(item) for item in chapter_outputs],
            "coverage_stats": {
                "mode": "full",
                "processed_rows": processed_rows,
                "total_rows": estimated_total,
                "coverage_ratio": coverage_ratio,
                "map_groups": len(groups),
                "chapter_groups": len(chapter_groups),
                "raw_total_chunks": raw_total,
                "after_doc_limit": raw_retrieved,
                "after_candidate_limit": len(candidate_rows),
                "retrieval_strategy": str(retrieval_stats.get("retrieval_strategy", "unknown")),
                "full_require_complete": bool(full_runtime["require_complete"]),
            },
            "provider": (stage2_outputs[-1].get("provider", "unknown") if stage2_outputs else (map_outputs[-1].get("provider", "unknown") if map_outputs else "unknown")),
        }

    async def _ensure_chapter_output_language(self, item: Dict[str, Any], *, prefer_chinese: bool) -> Dict[str, Any]:
        if not prefer_chinese:
            return item
        combined = " ".join(
            _to_list(item.get("key_points"))
            + _to_list(item.get("analysis"))
            + _to_list(item.get("recommendations"))
            + _to_list(item.get("risks"))
        ).strip()
        if not combined or not _looks_mostly_english(combined):
            return item
        prompt = (
            "你是一个多语言翻译与摘要专家。请将以下摘要内容翻译或改写为高质量中文。仅返回JSON对象，包含字段: key_points, analysis, recommendations, risks。\n"
            "确保术语专业、表达地道、逻辑清晰。\n"
            f"章节标题：{item.get('chapter_title', '章节')}\n"
            f"原始内容：{combined}"
        )
        try:
            resp = await self.ai_router.chat_with_task(
                [{"role": "user", "content": prompt}],
                task_type="summarize",
                max_tokens=900,
                temperature=0.1,
                prefer_free=True,
            )
            parsed = parse_json_object(str(resp.get("content", "")))
            normalized = dict(item)
            if isinstance(parsed, dict):
                normalized["key_points"] = _to_list(parsed.get("key_points")) or _to_list(item.get("key_points"))
                normalized["analysis"] = _to_list(parsed.get("analysis")) or _to_list(item.get("analysis"))
                normalized["recommendations"] = _to_list(parsed.get("recommendations")) or _to_list(item.get("recommendations"))
                normalized["risks"] = _to_list(parsed.get("risks")) or _to_list(item.get("risks"))
            return normalized
        except Exception:
            return item

    async def _legacy_generate_report_contract(self, state: Dict[str, Any]) -> Dict[str, Any]:
        rows = state.get("retrieved", [])
        context_blocks = [str(state.get("report_reduce_context", "")).strip()]
        detail_runtime = _detail_first_runtime_config()
        for row in rows[: int(detail_runtime["report_context_rows"])]:
            context_blocks.append(
                f"[{row.get('title')}::{row.get('section_path')}::{row.get('discipline')}]\n"
                f"{str(row.get('content', ''))[:int(detail_runtime['report_context_clip'])]}"
            )
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        targets = _adaptive_report_targets(int(state.get("estimated_doc_chunks", 0) or 0), compact_level)
        max_chars = int(max(targets["max_chars"], detail_runtime["report_max_chars_floor"]))
        prompt = (
            "你是一个资深的战略分析师与超长文报告专家。请基于以下上下文生成一份高质量、极具穿透力的‘说明型研究报告’。仅返回JSON对象，字段: report, sections, citations, purpose, subjects, subject_object_links, how_to, why。\n"
            "【输出内容】：\n"
            "- report: 完整的、具有逻辑深度的中文说明文正文。\n"
            "- sections: 报告的分节数组，每项包含title和content。务必确保content是连贯的段落，严禁机械堆砌关键词。\n"
            "- citations: 引用数组，包含title, discipline, section_path。\n"
            "- purpose (一句话摘要)、subjects/subject_object_links/how_to/why (各为数组)。\n"
            "【语言要求】：\n"
            "- 语气专业、逻辑严丝合缝、风格沉稳、地道。避免使用‘首先、其次、最后’等过于简单的连接词，多采用因果、递进等逻辑关联词。\n"
            f"目标报告总字数区间：{int(targets['min_chars'])}-{max_chars}。\n"
            f"用户查询：{state.get('query', '')}\n"
            f"上下文信息：\n{chr(10).join([x for x in context_blocks if x])}"
        )
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="summarize",
            max_tokens=int(targets["max_tokens"]),
            temperature=0.2,
            prefer_free=True,
        )
        parsed = parse_json_object(str(resp.get("content", "")))
        report = str(parsed.get("report", "") if isinstance(parsed, dict) else "").strip()
        sections = _normalize_report_sections(parsed.get("sections") if isinstance(parsed, dict) else [])
        fallback_reason = "none"
        citations = parsed.get("citations") if isinstance(parsed, dict) and isinstance(parsed.get("citations"), list) else []
        if not citations:
            citations = state.get("evidence", [])[:8]
        if not citations:
            citations = [{"title": "基于当前检索未命中", "discipline": "all", "section_path": "N/A"}]
        if not report and sections:
            report = "\n\n".join([f"## {item.get('title', '章节')}\n{item.get('content', '')}" for item in sections]).strip()
        if not report or not sections:
            fallback = _build_minimum_report_fallback(
                query=str(state.get("query", "")).strip(),
                report_reduce_context=str(state.get("report_reduce_context", "")).strip(),
                rows=rows if isinstance(rows, list) else [],
                evidence=state.get("evidence", []) if isinstance(state.get("evidence"), list) else [],
            )
            sections = fallback["sections"]
            report = fallback["report"]
            fallback_reason = "report_parse_fallback"
        five_dimensions, five_dimensions_meta = _resolve_five_dimensions(
            parsed if isinstance(parsed, dict) else {},
            query=str(state.get("query", "")).strip(),
            fallback={
                "purpose": (sections[0].get("content", "") if sections else report)[:220],
                "subjects": [str(x.get("discipline", "all")) for x in citations[:6] if isinstance(x, dict)],
                "subject_object_links": [x.get("content", "") for x in sections[:2] if isinstance(x, dict)],
                "how_to": [x.get("content", "") for x in sections if isinstance(x, dict) and "建议" in str(x.get("title", ""))][:8],
                "why": [x.get("content", "") for x in sections if isinstance(x, dict) and "结论" in str(x.get("title", ""))][:8],
            },
        )
        report = report[:max_chars]
        provider = str(resp.get("provider", state.get("provider", "unknown")))
        non_empty_report = bool(report.strip())
        structure_complete = bool(sections)
        citation_coverage = bool(citations)
        failed_checks: List[str] = []
        if not non_empty_report:
            failed_checks.append("empty_report")
        if not structure_complete:
            failed_checks.append("empty_sections")
        if not citation_coverage:
            failed_checks.append("empty_citations")
        return {
            "report": report,
            "report_sections": sections,
            "citations": citations[:8],
            "five_dimensions": five_dimensions,
            "five_dimensions_meta": five_dimensions_meta,
            "provider": provider,
            "quality_gates": {
                "non_empty_report": non_empty_report,
                "structure_complete": structure_complete,
                "citation_coverage": citation_coverage,
                "passed": len(failed_checks) == 0,
                "failed_checks": failed_checks,
            },
            "fallback_reason": fallback_reason,
        }

    async def generate_report_contract(self, state: Dict[str, Any]) -> Dict[str, Any]:
        rows = state.get("retrieved", [])
        report_profile = _infer_academic_report_profile(rows if isinstance(rows, list) else [])
        document_tree = _build_document_tree(rows if isinstance(rows, list) else [])
        outline_text = _render_document_tree(document_tree)
        context_blocks = [str(state.get("report_reduce_context", "")).strip()]
        question_context = ""
        detail_runtime = _detail_first_runtime_config()
        for row in rows[: int(detail_runtime["report_context_rows"])]:
            page_num = _row_page_num(row)
            page_span = _format_page_span(page_num, page_num)
            context_blocks.append(
                f"[{row.get('title')}::{row.get('section_path')}::{row.get('discipline')}{page_span}]\n"
                f"{str(row.get('content', ''))[:int(detail_runtime['report_context_clip'])]}"
            )
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        targets = _adaptive_report_targets(int(state.get("estimated_doc_chunks", 0) or 0), compact_level)
        max_chars = int(max(targets["max_chars"], detail_runtime["report_max_chars_floor"]))
        blueprint_text = " / ".join([str(x) for x in report_profile.get("section_blueprint", []) if str(x).strip()])
        prompt = (
            "你是一个资深战略分析与报告主笔。请仅返回JSON对象，包含字段: report, sections, citations, purpose, subjects, subject_object_links, how_to, why。\n"
            "【特别提示】：参考资料已为你提供了各章节的‘事实证据摘要’。你的任务是基于这些事实，进行跨维度的‘升维分析’，严禁直接复制摘要内容。\n"
            "【生成策略】：\n"
            "1. **report (执行摘要)**：全篇灵魂。侧重回答‘文档的全局核心意义是什么？’、‘这对用户有何战略级影响？’。以全局决策者的语调撰写。\n"
            "2. **sections (深度专题分析)**：严禁简单拆分。每一节必须是一个独立的‘洞察专题’。例如：\n"
            "   - 专题 A：基于各章事实的‘潜在风险矩阵’；\n"
            "   - 专题 B：全文反映出的‘逻辑漏洞或未尽事宜’；\n"
            "   - 专题 C：对标行业标准的‘执行指南’。\n"
            "   - **核心要求**：如果摘要里写了‘A条款规定了B’，你应当分析‘这意味着用户在执行中会遇到C障碍’。提供摘要中没有的深度见解。\n"
            "3. **内容互补**：摘要负责事实，报告负责‘事实背后的意义’。sections 必须包含严密的因果推演。\n"
            f"分节蓝图指导：{blueprint_text or '核心观点 / 研究现状 / 实验分析 / 结论建议 / 风险提示'}。\n"
            f"目标总字数：{int(targets['min_chars'])}-{max_chars}。\n"
            f"报告风格：{report_profile.get('label', '学术研究型')}。\n"
            f"用户查询：{state.get('query', '')}\n"
            f"参考资料（事实库）：\n{chr(10).join([x for x in context_blocks if x])}"
        )
        if question_context:
            prompt = f"题目上下文：\n{question_context}\n\n{prompt}"
        if question_context:
            prompt = f"问题提示：\n{question_context}\n\n{prompt}"
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="summarize",
            max_tokens=int(targets["max_tokens"]),
            temperature=0.2,
            prefer_free=True,
        )
        parsed = parse_json_object(str(resp.get("content", "")))
        report = str(parsed.get("report", "") if isinstance(parsed, dict) else "").strip()
        sections = _normalize_report_sections(parsed.get("sections") if isinstance(parsed, dict) else [])
        fallback_reason = "none"
        citations = parsed.get("citations") if isinstance(parsed, dict) and isinstance(parsed.get("citations"), list) else []
        if not citations:
            citations = state.get("evidence", [])[:8]
        if not citations:
            citations = [{"title": "未命中具体引用", "discipline": "all", "section_path": "N/A"}]
        if not report and sections:
            report = "\n\n".join([f"## {item.get('title', '章节')}\n{item.get('content', '')}" for item in sections]).strip()
        if not report or not sections:
            fallback = _build_minimum_report_fallback(
                query=str(state.get("query", "")).strip(),
                report_reduce_context=str(state.get("report_reduce_context", "")).strip(),
                rows=rows if isinstance(rows, list) else [],
                evidence=state.get("evidence", []) if isinstance(state.get("evidence"), list) else [],
                profile=report_profile,
            )
            sections = fallback["sections"]
            report = fallback["report"]
            fallback_reason = "report_parse_fallback"
        five_dimensions, five_dimensions_meta = _resolve_five_dimensions(
            parsed if isinstance(parsed, dict) else {},
            query=str(state.get("query", "")).strip(),
            fallback={
                "purpose": (sections[0].get("content", "") if sections else report)[:220],
                "subjects": [str(x.get("discipline", "all")) for x in citations[:6] if isinstance(x, dict)],
                "subject_object_links": [x.get("content", "") for x in sections[:2] if isinstance(x, dict)],
                "how_to": [x.get("content", "") for x in sections if isinstance(x, dict) and any(kw in str(x.get("title", "")) for kw in ["怎么", "如何", "方法", "步骤", "how", "step"])][:8],
                "why": [x.get("content", "") for x in sections if isinstance(x, dict) and any(kw in str(x.get("title", "")) for kw in ["为什么", "原因", "背景", "意义", "why", "reason"])][:8],
            },
        )
        report = report[:max_chars]
        provider = str(resp.get("provider", state.get("provider", "unknown")))
        non_empty_report = bool(report.strip())
        structure_complete = bool(sections)
        citation_coverage = bool(citations)
        failed_checks: List[str] = []
        if not non_empty_report:
            failed_checks.append("empty_report")
        if not structure_complete:
            failed_checks.append("empty_sections")
        if not citation_coverage:
            failed_checks.append("empty_citations")
        return {
            "report": report,
            "report_sections": sections,
            "report_profile": report_profile,
            "document_tree": document_tree,
            "citations": citations[:8],
            "five_dimensions": five_dimensions,
            "five_dimensions_meta": five_dimensions_meta,
            "provider": provider,
            "quality_gates": {
                "non_empty_report": non_empty_report,
                "structure_complete": structure_complete,
                "citation_coverage": citation_coverage,
                "passed": len(failed_checks) == 0,
                "failed_checks": failed_checks,
            },
            "fallback_reason": fallback_reason,
        }
    async def check_report_quality(self, state: Dict[str, Any]) -> Dict[str, Any]:
        report = str(state.get("report", "")).strip()
        sections = state.get("report_sections", []) if isinstance(state.get("report_sections"), list) else []
        citations = state.get("citations", []) if isinstance(state.get("citations"), list) else []
        query = str(state.get("query", "")).strip()
        failed: List[str] = []
        if not report:
            failed.append("empty_report")
        if not sections:
            failed.append("empty_sections")
        if ("empty_report" in failed) or ("empty_sections" in failed):
            fallback = _build_minimum_report_fallback(
                query=query,
                report_reduce_context=str(state.get("report_reduce_context", "")).strip(),
                rows=state.get("retrieved", []) if isinstance(state.get("retrieved"), list) else [],
                evidence=state.get("evidence", []) if isinstance(state.get("evidence"), list) else [],
            )
            if not sections:
                sections = fallback["sections"]
            if not report:
                report = fallback["report"]
            failed = [x for x in failed if x not in {"empty_report", "empty_sections"}]
        if not citations:
            failed.append("empty_citations")
            citations = state.get("evidence", [])[:8]
        if not citations:
            citations = [{"title": "基于当前检索未命中", "discipline": "all", "section_path": "N/A"}]
        coverage_stats = state.get("coverage_stats", {}) if isinstance(state.get("coverage_stats"), dict) else {}
        validation_runtime = _report_validation_runtime_config()
        validation_graph_skipped = bool(validation_runtime["disable_validation_graph"])
        if validation_graph_skipped:
            coverage_stats = dict(coverage_stats)
            coverage_stats["validation_graph_skipped"] = True
        full_runtime = _report_full_runtime_config()
        if bool(full_runtime["require_complete"]) and not validation_graph_skipped:
            processed = int(coverage_stats.get("processed_rows", 0) or 0)
            raw_total = int(coverage_stats.get("raw_total_chunks", 0) or 0)
            if raw_total > 0 and processed < raw_total:
                failed.append("full_parse_under_coverage")
                coverage_stats = dict(coverage_stats)
                coverage_stats["missed_chunks"] = max(0, raw_total - processed)
        five_dimensions = state.get("five_dimensions", {}) if isinstance(state.get("five_dimensions"), dict) else {}
        five_dimensions_meta = (
            state.get("five_dimensions_meta", {}) if isinstance(state.get("five_dimensions_meta"), dict) else {}
        )
        if not five_dimensions:
            five_dimensions, five_dimensions_meta = _resolve_five_dimensions(
                {},
                query=query,
                fallback={
                    "purpose": report[:220],
                    "subjects": [str(x.get("discipline", "all")) for x in citations if isinstance(x, dict)],
                    "subject_object_links": [x.get("content", "") for x in sections[:2] if isinstance(x, dict)],
                    "how_to": [x.get("content", "") for x in sections if isinstance(x, dict) and "建议" in str(x.get("title", ""))],
                    "why": [x.get("content", "") for x in sections if isinstance(x, dict) and "结论" in str(x.get("title", ""))],
                },
            )
        coverage_stats = dict(coverage_stats)
        coverage_stats["five_dimensions_hit_rate"] = five_dimensions_meta.get("hit_rate", 0.0)
        coverage_stats["five_dimensions_source"] = five_dimensions_meta.get("source", "unknown")
        gates = {
            "non_empty_report": bool(report),
            "structure_complete": bool(sections),
            "citation_coverage": bool(citations),
            "five_dimensions_hit_rate": five_dimensions_meta.get("hit_rate", 0.0),
            "five_dimensions_source": five_dimensions_meta.get("source", "unknown"),
            "passed": len(failed) == 0,
            "failed_checks": failed,
        }
        patch: Dict[str, Any] = {
            "quality_gates": gates,
            "citations": citations,
            "report": report,
            "report_sections": sections,
            "coverage_stats": coverage_stats,
            "validation_graph_skipped": validation_graph_skipped,
            "five_dimensions": five_dimensions,
            "five_dimensions_meta": five_dimensions_meta,
        }
        if failed:
            patch["fallback_reason"] = (
                "full_parse_under_coverage" if "full_parse_under_coverage" in failed else "report_quality_guard"
            )
        elif state.get("fallback_reason") in {"none", "", None}:
            patch["fallback_reason"] = "report_quality_repaired"
        return patch

    async def generate_exam_contract(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reasoning = state.get("internal_reasoning", {}) if isinstance(state.get("internal_reasoning"), dict) else {}
        question_context = str(state.get("question_context", "")).strip()
        reasoning_hint = (
            f"主张: {reasoning.get('claim', '')}\n"
            f"证据摘要: {reasoning.get('evidence_summary', '')}\n"
            f"反例检查: {reasoning.get('counterexample_check', '')}\n"
            f"一致性检查: {reasoning.get('consistency_check', '')}\n"
        ).strip()
        # 题型策略提示
        _EXAM_TYPE_HINTS: Dict[str, str] = {
            "choice": "选择题：逐选项分析，answer首字符为所选字母。",
            "fill_blank": "填空题：直接给出填空答案，多空用分号分隔。",
            "true_false": "判断题：明确正确或错误，给出理由。",
            "short_answer": "简答题：分要点列出，每要点一句话。",
            "essay": "论述题：亮明论点，分层展开论证。",
            "calculation": "计算题：写出公式和代入过程，给出数值结果。",
            "proof": "证明题：按演绎链给出步骤，标注所用定理。",
            "design": "设计题：明确目标、约束、评估指标、步骤。",
            "material_analysis": "材料分析题：概括材料主旨，结合材料事实回答。",
        }
        qtype = str(state.get("question_type", "standard"))
        type_hint = _EXAM_TYPE_HINTS.get(qtype, "")
        type_line = f"题型提示：{type_hint}\n" if type_hint else ""
        prompt = (
            "你是考试作答Agent，仅返回JSON对象，字段: answer, brief_reasoning, answer_strategy, purpose, subjects, subject_object_links, how_to, why。\n"
            "answer_strategy必须包含: concept_induction, information_compression, reverse_check, distractor_design。\n"
            "brief_reasoning最多3条，禁止泄露完整推理链。\n"
            "五维字段要求：purpose一句话；subjects/subject_object_links/how_to/why均为字符串数组。\n"
            f"{type_line}"
            f"题目：{state.get('query', '')}\n"
            f"证据上下文：\n{state.get('compressed_context', '')}\n"
            f"内部推理参考（不要逐字复述）：\n{reasoning_hint}"
        )
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="exam",
            max_tokens=680,
            temperature=0.2,
            prefer_free=True,
        )
        parsed = parse_json_object(str(resp.get("content", "")))
        strategy_default = {
            "concept_induction": "待补充题目意图与考点。",
            "information_compression": "待补充证据压缩结果。",
            "reverse_check": "待执行反向检验。",
            "distractor_design": "待补充干扰项设计说明。",
        }
        strategy = parsed.get("answer_strategy") if isinstance(parsed.get("answer_strategy"), dict) else {}
        for k in strategy_default:
            v = str(strategy.get(k, "")).strip()
            strategy_default[k] = v[:220] if v else strategy_default[k]
        answer = sanitize_answer(parsed.get("answer") if parsed else resp.get("content", ""), max_len=500)
        brief_reasoning = sanitize_brief_reasoning(parsed.get("brief_reasoning") if parsed else [])
        five_dimensions, five_dimensions_meta = _resolve_five_dimensions(
            parsed if isinstance(parsed, dict) else {},
            query=str(state.get("query", "")).strip(),
            fallback={
                "purpose": answer[:200],
                "subjects": [str(state.get("discipline", "all"))],
                "subject_object_links": brief_reasoning[:2],
                "how_to": brief_reasoning[:3],
                "why": [str(reasoning.get("evidence_summary", "")).strip()],
            },
        )
        evidence = state.get("evidence", [])
        provider = str(resp.get("provider", "unknown"))
        free_tier_hit = provider in {"transformers-local", "github-models", "huggingface", "hash-fallback"}
        return {
            "answer": answer,
            "brief_reasoning": brief_reasoning,
            "answer_strategy": strategy_default,
            "five_dimensions": five_dimensions,
            "five_dimensions_meta": five_dimensions_meta,
            "provider": provider,
            "qa_regression_gates": build_reasoning_gates(answer, brief_reasoning, evidence),
            "cost_profile": {"prefer_free": True, "provider": provider, "free_tier_hit": free_tier_hit},
        }

    async def check_exam_quality(self, state: Dict[str, Any]) -> Dict[str, Any]:
        answer = str(state.get("answer", "")).strip()
        brief = state.get("brief_reasoning", [])
        evidence = state.get("evidence", [])
        strategy = state.get("answer_strategy", {}) if isinstance(state.get("answer_strategy"), dict) else {}
        has_strategy = all(bool(str(strategy.get(k, "")).strip()) for k in ("concept_induction", "information_compression", "reverse_check", "distractor_design"))
        gates = build_reasoning_gates(answer, brief if isinstance(brief, list) else [], evidence if isinstance(evidence, list) else [])
        failed = list(gates.get("failed_checks", []))
        if not has_strategy:
            failed.append("strategy_incomplete")
        five_dimensions_meta = (
            state.get("five_dimensions_meta", {}) if isinstance(state.get("five_dimensions_meta"), dict) else {}
        )
        gates["five_dimensions_hit_rate"] = five_dimensions_meta.get("hit_rate", 0.0)
        gates["five_dimensions_source"] = five_dimensions_meta.get("source", "unknown")
        gates["strategy_complete"] = has_strategy
        gates["passed"] = len(failed) == 0
        gates["failed_checks"] = failed
        return {"qa_regression_gates": gates, "quality_gates": gates}

    async def internal_reasoning_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        question_context = str(state.get("question_context", "")).strip()
        prompt_parts = [
            "你是内部推理节点。只返回JSON对象，字段: prediction, claim, evidence_summary, counterexample_check, consistency_check。",
            "prediction: 在看到证据前，基于你的内部知识库预测答案方向（一句话）。",
            "要求：每个字段一句话，不输出多余解释。",
        ]
        if question_context:
            prompt_parts.append(f"题目结构上下文：\n{question_context}")
        prompt_parts.append(f"问题：{state.get('query', '')}")
        prompt_parts.append(f"证据上下文：\n{state.get('compressed_context', '')}")
        prompt = "\n".join(prompt_parts)
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="reason",
            max_tokens=520,
            temperature=0.2,
            prefer_free=True,
        )
        parsed = parse_json_object(str(resp.get("content", "")))
        reasoning = {
            "claim": str(parsed.get("claim", "")).strip()[:220],
            "evidence_summary": str(parsed.get("evidence_summary", "")).strip()[:260],
            "counterexample_check": str(parsed.get("counterexample_check", "")).strip()[:220],
            "consistency_check": str(parsed.get("consistency_check", "")).strip()[:220],
        }
        prediction = str(parsed.get("prediction", "")).strip()[:180]
        return {"internal_reasoning": reasoning, "prediction": prediction, "predicted_answer": prediction}

    async def generate_exam_contract(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reasoning = state.get("internal_reasoning", {}) if isinstance(state.get("internal_reasoning"), dict) else {}
        question_context = str(state.get("question_context", "")).strip()
        reasoning_hint = (
            f"主张: {reasoning.get('claim', '')}\n"
            f"证据摘要: {reasoning.get('evidence_summary', '')}\n"
            f"反例检查: {reasoning.get('counterexample_check', '')}\n"
            f"一致性检查: {reasoning.get('consistency_check', '')}\n"
        ).strip()
        exam_type_hints: Dict[str, str] = {
            "choice": "选择题：逐选项分析，answer 首字符为所选字母。",
            "fill_blank": "填空题：直接给出答案，多空用分号分隔。",
            "true_false": "判断题：明确正确或错误，并给出理由。",
            "short_answer": "简答题：按要点输出，每要点一句。",
            "essay": "论述题：先给中心论点，再分层论证。",
            "calculation": "计算题：保留关键公式、代入过程与结果。",
            "proof": "证明题：按逻辑链给出步骤，并注明所用定理。",
            "design": "设计题：明确目标、约束、步骤与评估指标。",
            "material_analysis": "材料分析题：先概括材料，再结合材料事实回答。",
        }
        qtype = str(state.get("question_type", "standard"))
        prompt_parts = [
            "你是考试作答 Agent，仅返回 JSON 对象，字段: answer, brief_reasoning, answer_strategy, purpose, subjects, subject_object_links, how_to, why。",
            "answer_strategy 必须包含: concept_induction, information_compression, reverse_check, distractor_design。",
            "brief_reasoning 最多 3 条，不得泄露完整思维链。",
        ]
        type_hint = exam_type_hints.get(qtype, "")
        if type_hint:
            prompt_parts.append(f"题型提示：{type_hint}")
        if question_context:
            prompt_parts.append(f"题目树上下文：\n{question_context}")
        prompt_parts.extend(
            [
                f"题目：{state.get('query', '')}",
                f"证据上下文：\n{state.get('compressed_context', '')}",
                f"内部推理参考（不要逐字复述）：\n{reasoning_hint}",
            ]
        )
        prompt = "\n".join(prompt_parts)
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="exam",
            max_tokens=680,
            temperature=0.2,
            prefer_free=True,
        )
        parsed = parse_json_object(str(resp.get("content", "")))
        strategy_default = {
            "concept_induction": "待补充题目意图与考点。",
            "information_compression": "待补充证据压缩结果。",
            "reverse_check": "待执行反向检验。",
            "distractor_design": "待补充易错点说明。",
        }
        strategy = parsed.get("answer_strategy") if isinstance(parsed.get("answer_strategy"), dict) else {}
        for key in strategy_default:
            value = str(strategy.get(key, "")).strip()
            if value:
                strategy_default[key] = value[:220]
        answer = sanitize_answer(parsed.get("answer") if parsed else resp.get("content", ""), max_len=500)
        brief_reasoning = sanitize_brief_reasoning(parsed.get("brief_reasoning") if parsed else [])
        five_dimensions, five_dimensions_meta = _resolve_five_dimensions(
            parsed if isinstance(parsed, dict) else {},
            query=str(state.get("query", "")).strip(),
            fallback={
                "purpose": answer[:200],
                "subjects": [str(state.get("discipline", "all"))],
                "subject_object_links": brief_reasoning[:2],
                "how_to": brief_reasoning[:3],
                "why": [str(reasoning.get("evidence_summary", "")).strip()],
            },
        )
        evidence = state.get("evidence", [])
        provider = str(resp.get("provider", "unknown"))
        free_tier_hit = provider in {"transformers-local", "github-models", "huggingface", "hash-fallback"}
        return {
            "answer": answer,
            "brief_reasoning": brief_reasoning,
            "answer_strategy": strategy_default,
            "five_dimensions": five_dimensions,
            "five_dimensions_meta": five_dimensions_meta,
            "provider": provider,
            "qa_regression_gates": build_reasoning_gates(answer, brief_reasoning, evidence),
            "cost_profile": {"prefer_free": True, "provider": provider, "free_tier_hit": free_tier_hit},
        }

    async def check_exam_quality(self, state: Dict[str, Any]) -> Dict[str, Any]:
        answer = str(state.get("answer", "")).strip()
        brief = state.get("brief_reasoning", [])
        evidence = state.get("evidence", [])
        strategy = state.get("answer_strategy", {}) if isinstance(state.get("answer_strategy"), dict) else {}
        has_strategy = all(bool(str(strategy.get(key, "")).strip()) for key in ("concept_induction", "information_compression", "reverse_check", "distractor_design"))
        gates = build_reasoning_gates(answer, brief if isinstance(brief, list) else [], evidence if isinstance(evidence, list) else [])
        failed = list(gates.get("failed_checks", []))
        if not has_strategy:
            failed.append("strategy_incomplete")
        five_dimensions_meta = state.get("five_dimensions_meta", {}) if isinstance(state.get("five_dimensions_meta"), dict) else {}
        gates["five_dimensions_hit_rate"] = five_dimensions_meta.get("hit_rate", 0.0)
        gates["five_dimensions_source"] = five_dimensions_meta.get("source", "unknown")
        gates["strategy_complete"] = has_strategy
        gates["passed"] = len(failed) == 0
        gates["failed_checks"] = failed
        return {"qa_regression_gates": gates, "quality_gates": gates}

    async def split_long_text(self, state: Dict[str, Any]) -> Dict[str, Any]:
        text = str(state.get("doc_text", "")).replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return {"chunks": [], "sections": []}
        chunk_size = max(200, int(state.get("chunk_size", 900) or 900))
        overlap = max(0, int(state.get("chunk_overlap", 180) or 180))
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text]

        sentence_splits: List[str] = []
        for p in paragraphs:
            parts = re.split(r"(?<=[。！？.!?；;])\s+", p)
            for part in parts:
                clean = part.strip()
                if clean:
                    sentence_splits.append(clean)
        if not sentence_splits:
            sentence_splits = paragraphs

        chunks: List[str] = []
        current_parts: List[str] = []
        current_len = 0
        for sent in sentence_splits:
            sent_len = len(sent)
            if current_len + sent_len + 1 <= chunk_size:
                current_parts.append(sent)
                current_len += sent_len + 1
                continue
            if current_parts:
                chunks.append(" ".join(current_parts).strip())
            # keep overlap by sentence tail
            overlap_parts: List[str] = []
            overlap_len = 0
            for prev in reversed(current_parts):
                overlap_parts.insert(0, prev)
                overlap_len += len(prev) + 1
                if overlap_len >= overlap:
                    break
            current_parts = overlap_parts + [sent]
            current_len = sum(len(x) + 1 for x in current_parts)
        if current_parts:
            chunks.append(" ".join(current_parts).strip())

        if not chunks:
            stride = max(1, chunk_size - overlap)
            chunks = [text[i : i + chunk_size] for i in range(0, len(text), stride)]

        dtype = str(state.get("document_type", "academic"))
        sections = [{"section_path": f"{dtype}/chunk/{idx + 1}", "content": chunk[: chunk_size + 120]} for idx, chunk in enumerate(chunks)]
        return {"chunks": sections, "sections": sections}

    async def abstract_chunks(self, state: Dict[str, Any]) -> Dict[str, Any]:
        sections = state.get("sections", [])
        if not isinstance(sections, list) or not sections:
            return {"sections": []}
        preview_blocks = []
        for item in sections[:8]:
            if not isinstance(item, dict):
                continue
            preview_blocks.append(str(item.get("content", ""))[:260])
        if not preview_blocks:
            return {"sections": sections}
        prompt = (
            "你是长文本知识抽象Agent。请用不超过4条中文要点提炼核心知识，不输出推理过程。\n"
            "仅返回JSON对象：{\"key_points\": [\"...\"]}\n"
            f"文本片段：\n{chr(10).join(preview_blocks)}"
        )
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="extract",
            max_tokens=360,
            temperature=0.2,
            prefer_free=True,
        )
        parsed = parse_json_object(str(resp.get("content", "")))
        key_points = _to_list(parsed.get("key_points") if isinstance(parsed, dict) else [])
        if not key_points:
            return {"sections": sections}
        abstract = "；".join([x[:100] for x in key_points[:4]])
        dtype = str(state.get("document_type", "academic"))
        augmented = [{"section_path": f"{dtype}/abstract/0", "content": abstract}, *sections]
        return {"sections": augmented, "provider": resp.get("provider", state.get("provider", "unknown"))}


def _to_list(value: Any) -> List[str]:
    if isinstance(value, list):
        out: List[str] = []
        for x in value:
            normalized = _normalize_summary_item(x)
            if normalized:
                out.append(normalized)
        return out
    if isinstance(value, dict):
        normalized = _normalize_summary_item(value)
        return [normalized] if normalized else []
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_summary_item(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        text = str(value).strip()
        return text
    key_text = str(value.get("key", "")).strip()
    details_text = str(value.get("details", "")).strip()
    if key_text and details_text:
        return f"{key_text}：{details_text}"
    if details_text:
        return details_text
    if key_text:
        return key_text
    raw_text = str(value).strip()
    return raw_text if raw_text not in {"{}", ""} else ""


def _resolve_five_dimensions(
    parsed: Dict[str, Any],
    query: str,
    fallback: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    def _pick_list(key: str) -> List[str]:
        return [x[:280] for x in _to_list(parsed.get(key)) if x.strip()]

    purpose_raw = str(parsed.get("purpose", "")).strip()
    subjects = _pick_list("subjects")
    links = _pick_list("subject_object_links")
    how_to = _pick_list("how_to")
    why = _pick_list("why")

    used_fallback = False
    if not purpose_raw:
        purpose_raw = str(fallback.get("purpose", "")).strip() or f"回答“{query or '当前问题'}”并给出可执行解释。"
        used_fallback = True
    if not subjects:
        subjects = [x[:280] for x in _to_list(fallback.get("subjects"))][:8]
        used_fallback = True
    if not links:
        links = [x[:280] for x in _to_list(fallback.get("subject_object_links"))][:8]
        used_fallback = True
    if not how_to:
        how_to = [x[:280] for x in _to_list(fallback.get("how_to"))][:10]
        used_fallback = True
    if not why:
        why = [x[:280] for x in _to_list(fallback.get("why"))][:10]
        used_fallback = True

    hit_fields = int(bool(str(parsed.get("purpose", "")).strip())) + int(bool(_to_list(parsed.get("subjects")))) + int(
        bool(_to_list(parsed.get("subject_object_links")))
    ) + int(bool(_to_list(parsed.get("how_to")))) + int(bool(_to_list(parsed.get("why"))))
    total_fields = 5
    out = {
        "purpose": purpose_raw[:280],
        "subjects": subjects[:10],
        "subject_object_links": links[:10],
        "how_to": how_to[:12],
        "why": why[:12],
    }
    meta = {
        "hit_fields": hit_fields,
        "total_fields": total_fields,
        "hit_rate": round(hit_fields / total_fields, 3),
        "source": "fallback_mix" if used_fallback else "model",
    }
    return out, meta


def _chunk_rows(rows: List[Any], size: int) -> List[List[Any]]:
    bucket_size = max(1, size)
    return [rows[i : i + bucket_size] for i in range(0, len(rows), bucket_size)]


def _group_rows_for_hierarchical_report(rows: List[Dict[str, Any]], chunk_size: int) -> List[Dict[str, Any]]:
    ordered_rows = sorted(rows, key=_report_row_order_key)
    toc_items = _extract_report_toc_items(ordered_rows)
    chapter_groups: Dict[str, Dict[str, Any]] = {}
    active_chapter_key = ""
    active_chapter_title = ""
    for row in ordered_rows:
        chapter_key, chapter_title = _resolve_report_chapter(
            row,
            toc_items,
            active_chapter_key=active_chapter_key,
            active_chapter_title=active_chapter_title,
        )
        bucket = chapter_groups.get(chapter_key)
        if bucket is None:
            bucket = {
                "chapter_key": chapter_key,
                "chapter_title": chapter_title,
                "rows": [],
                "page_start": None,
                "page_end": None,
            }
            chapter_groups[chapter_key] = bucket
        bucket["rows"].append(row)
        if chapter_key and chapter_title and not chapter_key.startswith("pages:"):
            active_chapter_key = chapter_key
            active_chapter_title = chapter_title
        page_num = _row_page_num(row)
        if page_num > 0:
            if bucket["page_start"] is None or page_num < bucket["page_start"]:
                bucket["page_start"] = page_num
            if bucket["page_end"] is None or page_num > bucket["page_end"]:
                bucket["page_end"] = page_num
    output: List[Dict[str, Any]] = []
    for bucket in chapter_groups.values():
        row_groups = _split_rows_for_report_chapter(
            rows=bucket["rows"],
            chunk_size=max(1, chunk_size),
            page_start=bucket.get("page_start"),
            page_end=bucket.get("page_end"),
        )
        for index, group_rows in enumerate(row_groups, start=1):
            page_start, page_end = _page_span_from_rows(group_rows)
            segment_label = str(bucket["chapter_title"])
            if len(row_groups) > 1:
                segment_label = f"{segment_label} 第{index}/{len(row_groups)}段"
            output.append(
                {
                    "chapter_key": bucket["chapter_key"],
                    "chapter_title": bucket["chapter_title"],
                    "segment_label": segment_label,
                    "page_start": page_start or bucket["page_start"],
                    "page_end": page_end or bucket["page_end"],
                    "rows": group_rows,
                }
            )
    return output


def _split_rows_for_report_chapter(
    rows: List[Dict[str, Any]],
    chunk_size: int,
    page_start: Optional[int],
    page_end: Optional[int],
) -> List[List[Dict[str, Any]]]:
    row_count = len(rows)
    if row_count <= 0:
        return []
    if row_count <= chunk_size:
        return [rows]

    page_span = 0
    if isinstance(page_start, int) and isinstance(page_end, int) and page_start > 0 and page_end >= page_start:
        page_span = page_end - page_start + 1

    # 章节优先：小章整章处理，只有超长章节才继续拆段。
    if page_span and page_span <= 24 and row_count <= chunk_size * 2:
        return [rows]
    if not page_span and row_count <= chunk_size * 2:
        return [rows]

    desired_segments = 1
    if page_span > 0:
        if page_span <= 48:
            desired_segments = 2
        elif page_span <= 90:
            desired_segments = 3
        else:
            desired_segments = 4
    else:
        if row_count <= chunk_size * 3:
            desired_segments = 2
        elif row_count <= chunk_size * 5:
            desired_segments = 3
        else:
            desired_segments = 4

    desired_segments = max(1, min(4, desired_segments))
    adaptive_chunk_size = max(chunk_size, (row_count + desired_segments - 1) // desired_segments)
    return _chunk_rows(rows, adaptive_chunk_size)


def _group_map_outputs_by_chapter(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in items:
        # 获取chapter_key和segment_label
        raw_chapter_key = str(item.get("chapter_key", "") or "")
        raw_segment_label = str(item.get("segment_label", "") or "")
        
        # 确定最终的chapter_key
        if raw_chapter_key:
            chapter_key = raw_chapter_key
        elif raw_segment_label:
            chapter_key = raw_segment_label
        else:
            # 当chapter_key和segment_label都为空时，生成基于页面范围的唯一key
            page_start = item.get("page_start")
            page_end = item.get("page_end")
            if isinstance(page_start, int) and page_start > 0:
                if isinstance(page_end, int) and page_end >= page_start:
                    chapter_key = f"pages:{page_start}-{page_end}"
                else:
                    chapter_key = f"page:{page_start}"
            else:
                # 如果连页面信息都没有，使用内容哈希生成唯一key
                content_key = ""
                for field in ["key_points", "analysis", "recommendations", "risks"]:
                    field_val = item.get(field)
                    if field_val:
                        content_key += str(field_val)[:50]
                if content_key:
                    import hashlib
                    hash_key = hashlib.md5(content_key.encode()).hexdigest()[:8]
                    chapter_key = f"auto:{hash_key}"
                else:
                    # 最后的手段：使用索引
                    chapter_key = f"item:{len(grouped)}"
        
        bucket = grouped.get(chapter_key)
        if bucket is None:
            # 确定chapter_title
            chapter_title = str(item.get("chapter_title", "") or "")
            if not chapter_title:
                if raw_segment_label:
                    chapter_title = raw_segment_label
                elif raw_chapter_key and not raw_chapter_key.startswith("pages:") and not raw_chapter_key.startswith("page:"):
                    chapter_title = raw_chapter_key
                else:
                    chapter_title = "章节"
            
            bucket = {
                "chapter_key": chapter_key,
                "chapter_title": chapter_title,
                "page_start": item.get("page_start"),
                "page_end": item.get("page_end"),
                "items": [],
            }
            grouped[chapter_key] = bucket
        
        bucket["items"].append(item)
        page_start = item.get("page_start")
        page_end = item.get("page_end")
        if isinstance(page_start, int) and page_start > 0:
            if not isinstance(bucket.get("page_start"), int) or page_start < int(bucket["page_start"]):
                bucket["page_start"] = page_start
        if isinstance(page_end, int) and page_end > 0:
            if not isinstance(bucket.get("page_end"), int) or page_end > int(bucket["page_end"]):
                bucket["page_end"] = page_end
    return list(grouped.values())


def _extract_report_toc_items(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    metadata = parse_json_object(str(rows[0].get("metadata", "")))
    toc = metadata.get("toc") if isinstance(metadata, dict) else []
    if not isinstance(toc, list):
        return []
    items: List[Dict[str, Any]] = []
    for entry in toc:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        page_num = _safe_int(entry.get("page", entry.get("page_num", 0)), 0, 0)
        level = _safe_int(entry.get("level", 1), 1, 1)
        if not title or page_num <= 0:
            continue
        items.append({"title": title, "page_num": page_num, "level": level})
    items.sort(key=lambda x: (int(x["page_num"]), int(x["level"])))
    return items


def _is_generic_report_section_title(title: str) -> bool:
    normalized = str(title or "").strip()
    if not normalized:
        return True
    lowered = normalized.lower().rstrip(":：")
    generic_exact = {
        "解题思路",
        "典型试题",
        "答案",
        "工作目标",
        "职权和职责",
        "示例",
        "案例",
        "例题",
        "练习题",
        "相关知识",
    }
    if lowered in generic_exact:
        return True
    generic_prefixes = (
        "答案",
        "解题思路",
        "典型试题",
        "示例",
        "案例",
        "附录",
    )
    return any(lowered.startswith(prefix) for prefix in generic_prefixes)


def _looks_like_anchor_report_title(title: str) -> bool:
    normalized = str(title or "").strip()
    if not normalized:
        return False
    if normalized in {"目录", "前言", "编者的话", "考试大纲内容"}:
        return True
    if normalized.startswith("第") and ("章" in normalized or "节" in normalized):
        return True
    if re.match(r"^[IVXLC]+\.\s*$", normalized):
        return True
    if re.match(r"^[A-Z]\.\s*", normalized):
        return True
    if re.match(r"^\d+(?:\.\d+){0,3}\s*", normalized):
        return True
    return not _is_generic_report_section_title(normalized)


def _clean_heading_candidate(line: str) -> str:
    text = str(line or "").strip()
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^[#>\-\*\s]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[.·•]{3,}\s*\d+\s*$", "", text).strip()
    return text[:100]


def _extract_heading_from_content(content: str) -> str:
    if not content:
        return ""
    normalized = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    inline_patterns = [
        r"##\s*(摘\s*要|Abstract)\b",
        r"##\s*([0-9]+(?:\.[0-9]+){0,4}\s*[^\n#]{1,80})",
        r"##\s*(第[一二三四五六七八九十百0-9]+[章节篇部]\s*[^\n#]{0,60})",
        r"(?:^|[\n。！？；;]\s*)(摘\s*要|Abstract)\b",
        r"(?:^|[\n。！？；;]\s*)([0-9]+(?:\.[0-9]+){0,4}\s*[^\n。！？；;#]{1,80})",
        r"(?:^|[\n。！？；;]\s*)(第[一二三四五六七八九十百0-9]+[章节篇部]\s*[^\n。！？；;#]{0,60})",
    ]
    for pattern in inline_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            line = _clean_heading_candidate(match.group(1))
            if line and len(line) <= 90:
                return line
    fallback_candidates: List[str] = []
    for raw_line in normalized.splitlines()[:16]:
        line = _clean_heading_candidate(raw_line)
        if not line:
            continue
        if len(line) > 90:
            continue
        if re.match(r"^(摘\s*要|abstract|目录|前言|引言|参考文献|总结)\b", line, flags=re.IGNORECASE):
            return line
        if re.match(r"^[A-Z]\.\s+\S+", line):
            return line
        if re.match(r"^[IVXLC]+\.\s+\S+", line):
            return line
        if re.match(r"^\d+(?:\.\d+){0,4}\s+\S+", line):
            return line
        if re.match(r"^第[一二三四五六七八九十百0-9]+[章节篇部]\s*\S*", line):
            return line
        if re.match(r"^[\u4e00-\u9fffA-Za-z][^\n]{1,40}$", line):
            fallback_candidates.append(line)
    return fallback_candidates[0] if fallback_candidates else ""


def _resolve_report_chapter(
    row: Dict[str, Any],
    toc_items: List[Dict[str, Any]],
    *,
    active_chapter_key: str = "",
    active_chapter_title: str = "",
) -> Tuple[str, str]:
    page_num = _row_page_num(row)
    toc_hit = _match_toc_title(page_num, toc_items)
    if toc_hit:
        return (f"toc:{toc_hit['page_num']}:{_slug_key(toc_hit['title'])}", str(toc_hit["title"]))
    section_title = _derive_section_title_from_row(row)
    toc_text_hit = _match_toc_title_by_text(section_title, toc_items) if section_title else None
    if toc_text_hit:
        return (f"toc:{toc_text_hit['page_num']}:{_slug_key(toc_text_hit['title'])}", str(toc_text_hit["title"]))
    if section_title and _is_generic_report_section_title(section_title) and active_chapter_key and active_chapter_title:
        return (active_chapter_key, active_chapter_title)
    if section_title:
        if not _looks_like_anchor_report_title(section_title) and active_chapter_key and active_chapter_title:
            return (active_chapter_key, active_chapter_title)
        return (f"section:{_slug_key(section_title)}", section_title)
    if page_num > 0:
        bucket_size = 12
        start = ((page_num - 1) // bucket_size) * bucket_size + 1
        end = start + bucket_size - 1
        return (f"pages:{start}-{end}", f"页 {start}-{end}")
    fallback_title = str(row.get("title", "")).strip() or "文档内容"
    return (f"document:{_slug_key(fallback_title)}", fallback_title)


def _match_toc_title(page_num: int, toc_items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if page_num <= 0 or not toc_items:
        return None
    matched: Optional[Dict[str, Any]] = None
    for item in toc_items:
        item_page = int(item.get("page_num", 0) or 0)
        if item_page <= 0:
            continue
        if item_page > page_num:
            break
        matched = item
    return matched


def _normalize_title_lookup(value: str) -> str:
    return re.sub(r"[\s\-_.:/\\|()\[\]【】（）·*★#]+", "", str(value or "").strip().lower())


def _match_toc_title_by_text(title: str, toc_items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    normalized = _normalize_title_lookup(title)
    if not normalized or not toc_items:
        return None
    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for item in toc_items:
        toc_title = str(item.get("title", "")).strip()
        toc_normalized = _normalize_title_lookup(toc_title)
        if not toc_normalized:
            continue
        if toc_normalized in normalized or normalized in toc_normalized:
            score = len(toc_normalized)
            if score > best_score:
                best = item
                best_score = score
    return best


def _derive_section_title(section_path: str) -> str:
    path = str(section_path or "").strip().strip("/")
    if not path:
        return ""
    parts = [part for part in path.split("/") if part]
    filtered: List[str] = []
    for part in parts:
        lowered = part.lower()
        if lowered.startswith("page") and re.fullmatch(r"page\d+", lowered):
            continue
        if lowered in {"section", "chunk", "academic", "technical", "project", "exam", "intro", "overall"}:
            continue
        if re.fullmatch(r"\d+", lowered):
            continue
        filtered.append(part)
    if not filtered:
        return ""
    title = re.sub(r"[-_]+", " ", filtered[0]).strip()
    if len(title) <= 1:
        return ""
    return title[:80]


def _derive_section_title_from_row(row: Dict[str, Any]) -> str:
    section_title = _derive_section_title(str(row.get("section_path", "")))
    if section_title:
        return section_title
    content_title = _extract_heading_from_content(str(row.get("content", "")))
    if content_title:
        return content_title
    title = _clean_heading_candidate(str(row.get("title", "")))
    if title:
        return title
    return ""


def _slug_key(value: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", str(value or "").strip().lower())
    return normalized.strip("-") or "section"


def _page_span_from_rows(rows: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int]]:
    pages = [page for page in (_row_page_num(row) for row in rows) if page > 0]
    if not pages:
        return (None, None)
    return (min(pages), max(pages))


def _format_page_span(page_start: Any, page_end: Any) -> str:
    try:
        start = int(page_start or 0)
        end = int(page_end or 0)
    except Exception:
        return ""
    if start <= 0 and end <= 0:
        return ""
    if start > 0 and end > 0:
        if start == end:
            return f"（第{start}页）"
        return f"（第{start}-{end}页）"
    if start > 0:
        return f"（第{start}页起）"
    return f"（至第{end}页）"


def _row_page_num(row: Dict[str, Any]) -> int:
    try:
        page_num = int(row.get("page_num", 0) or 0)
    except Exception:
        page_num = 0
    if page_num > 0:
        return page_num
    section_path = str(row.get("section_path", "") or "")
    match = re.search(r"page(\d+)", section_path, flags=re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return 0
    return 0


def _report_row_order_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    page_num = _row_page_num(row)
    section_path = str(row.get("section_path", "") or "")
    chunk_id = str(row.get("chunk_id", "") or "")
    row_id = int(row.get("id", 0) or 0)
    if page_num > 0:
        return (0, page_num, _natural_sort_key(section_path), _natural_sort_key(chunk_id), row_id)
    return (1, _natural_sort_key(section_path), _natural_sort_key(chunk_id), row_id)


def _natural_sort_key(value: str) -> Tuple[Any, ...]:
    text = str(value or "").strip().lower()
    if not text:
        return ((1, ""),)
    parts = re.split(r"(\d+)", text)
    output: List[Any] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            output.append((0, int(part)))
        else:
            output.append((1, part))
    return tuple(output)


def _sample_rows_for_coverage(rows: List[Any], limit: int) -> List[Any]:
    if not rows:
        return []
    target = max(1, min(int(limit), len(rows)))
    if target >= len(rows):
        return list(rows)
    if target <= 2:
        if target == 1:
            return [rows[0]]
        return [rows[0], rows[-1]]
    selected_indices = {0, len(rows) - 1}
    span = len(rows) - 1
    middle_slots = max(0, target - 2)
    for i in range(1, middle_slots + 1):
        ratio = i / float(middle_slots + 1)
        idx = int(round(ratio * span))
        selected_indices.add(max(0, min(len(rows) - 1, idx)))
        if len(selected_indices) >= target:
            break
    ordered = sorted(selected_indices)
    if len(ordered) < target:
        for idx in range(len(rows)):
            if idx in selected_indices:
                continue
            ordered.append(idx)
            if len(ordered) >= target:
                break
        ordered.sort()
    return [rows[idx] for idx in ordered[:target]]


def _normalize_compact_level(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed not in (0, 1, 2):
        return default
    return parsed


def _summary_limits_by_compact_level(level: int, percent_cfg: Dict[str, Any] | None = None) -> Dict[str, int]:
    # 0: 最少缩减（更长）；1: 平衡；2: 更紧凑
    configs = {
        0: {"item_limit": 14, "char_limit": 680},
        1: {"item_limit": 12, "char_limit": 520},
        2: {"item_limit": 10, "char_limit": 360},
    }
    base = dict(configs.get(level, configs[1]))
    if isinstance(percent_cfg, dict):
        base["item_limit"] = max(6, min(16, int(percent_cfg.get("clip_item_limit", base["item_limit"]))))
        base["char_limit"] = max(240, min(900, int(percent_cfg.get("clip_char_limit", base["char_limit"]))))
    base["item_limit"] = max(4, _safe_int(os.getenv("SUMMARY_CLIP_ITEM_LIMIT", str(base["item_limit"])), base["item_limit"], 4))
    base["char_limit"] = max(180, _safe_int(os.getenv("SUMMARY_CLIP_CHAR_LIMIT", str(base["char_limit"])), base["char_limit"], 180))
    return base


def _summary_length_stats(highlights: List[str], conclusions: List[str], actions: List[str]) -> Dict[str, Dict[str, int]]:
    return {
        "highlights": {"count": len(highlights), "chars": sum(len(x) for x in highlights)},
        "conclusions": {"count": len(conclusions), "chars": sum(len(x) for x in conclusions)},
        "actions": {"count": len(actions), "chars": sum(len(x) for x in actions)},
    }


def _map_reduce_config_by_compact_level(level: int, percent_cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    # 0: 更长, 1: 平衡, 2: 更短
    configs: Dict[int, Dict[str, Any]] = {
        0: {
            "map_item_limit": 4,
            "reduce_item_limit": 10,
            "group_size": 3,
            "map_sentence_range": "3-4",
            "reduce_sentence_range": "3-4",
            "map_max_tokens": 560,
            "reduce_max_tokens": 860,
            "full_group_size": 12,
            "full_coverage_floor": 28,
            "second_reduce_trigger": 10,
            "second_reduce_group_size": 6,
            "second_reduce_max_tokens": 780,
        },
        1: {
            "map_item_limit": 3,
            "reduce_item_limit": 8,
            "group_size": 3,
            "map_sentence_range": "2-3",
            "reduce_sentence_range": "2-3",
            "map_max_tokens": 460,
            "reduce_max_tokens": 700,
            "full_group_size": 10,
            "full_coverage_floor": 24,
            "second_reduce_trigger": 10,
            "second_reduce_group_size": 6,
            "second_reduce_max_tokens": 700,
        },
        2: {
            "map_item_limit": 2,
            "reduce_item_limit": 6,
            "group_size": 4,
            "map_sentence_range": "1-2",
            "reduce_sentence_range": "1-2",
            "map_max_tokens": 320,
            "reduce_max_tokens": 520,
            "full_group_size": 8,
            "full_coverage_floor": 18,
            "second_reduce_trigger": 8,
            "second_reduce_group_size": 5,
            "second_reduce_max_tokens": 560,
        },
    }
    base = dict(configs.get(level, configs[1]))
    if isinstance(percent_cfg, dict):
        base["reduce_item_limit"] = max(4, min(10, int(percent_cfg.get("reduce_item_limit", base["reduce_item_limit"]))))
    base["map_item_limit"] = _safe_int(os.getenv("SUMMARY_MAP_ITEM_LIMIT", str(base["map_item_limit"])), base["map_item_limit"], 1)
    base["reduce_item_limit"] = _safe_int(
        os.getenv("SUMMARY_REDUCE_ITEM_LIMIT", str(base["reduce_item_limit"])), base["reduce_item_limit"], 1
    )
    base["group_size"] = _safe_int(os.getenv("SUMMARY_GROUP_SIZE", str(base["group_size"])), base["group_size"], 1)
    base["map_max_tokens"] = _safe_int(
        os.getenv("SUMMARY_MAP_MAX_TOKENS", str(base["map_max_tokens"])), base["map_max_tokens"], 200
    )
    base["reduce_max_tokens"] = _safe_int(
        os.getenv("SUMMARY_REDUCE_MAX_TOKENS", str(base["reduce_max_tokens"])), base["reduce_max_tokens"], 280
    )
    return base


def _summary_percent_config_by_compact_level(level: int) -> Dict[str, Any]:
    # 混合策略：用于 map 覆盖、reduce 上限、末端裁剪联动
    configs: Dict[int, Dict[str, Any]] = {
        0: {
            "coverage_ratio": 0.50,
            "coverage_floor": 12,
            "reduce_item_limit": 12,
            "clip_item_limit": 14,
            "clip_char_limit": 720,
            "retrieval_top_k": 40,
        },
        1: {
            "coverage_ratio": 0.25,
            "coverage_floor": 8,
            "reduce_item_limit": 10,
            "clip_item_limit": 12,
            "clip_char_limit": 560,
            "retrieval_top_k": 28,
        },
        2: {
            "coverage_ratio": 0.10,
            "coverage_floor": 6,
            "reduce_item_limit": 6,
            "clip_item_limit": 10,
            "clip_char_limit": 420,
            "retrieval_top_k": 18,
        },
    }
    return configs.get(level, configs[1])


def _estimate_summary_scale(state: Dict[str, Any], rows: List[Any]) -> int:
    estimated_doc_chunks = int(state.get("estimated_doc_chunks", 0) or 0)
    if estimated_doc_chunks > 0:
        return estimated_doc_chunks
    return max(1, len(rows))


def _full_mode_doc_limit(estimated_doc_chunks: int) -> int:
    if estimated_doc_chunks <= 0:
        return 240
    if estimated_doc_chunks <= 120:
        return min(estimated_doc_chunks, 120)
    return min(estimated_doc_chunks, 480)


def _report_config_by_compact_level(level: int) -> Dict[str, Any]:
    configs: Dict[int, Dict[str, Any]] = {
        0: {
            "coverage_ratio": 0.9,
            "coverage_floor": 120,
            "group_size": 12,
            "stage2_group_size": 6,
            "content_clip": 1400,
            "map_max_tokens": 980,
            "stage2_max_tokens": 1200,
            "doc_limit": 1200,
            "fallback_top_k": 120,
        },
        1: {
            "coverage_ratio": 0.8,
            "coverage_floor": 90,
            "group_size": 10,
            "stage2_group_size": 6,
            "content_clip": 1200,
            "map_max_tokens": 880,
            "stage2_max_tokens": 1080,
            "doc_limit": 900,
            "fallback_top_k": 90,
        },
        2: {
            "coverage_ratio": 0.65,
            "coverage_floor": 70,
            "group_size": 8,
            "stage2_group_size": 5,
            "content_clip": 980,
            "map_max_tokens": 760,
            "stage2_max_tokens": 900,
            "doc_limit": 720,
            "fallback_top_k": 72,
        },
    }
    base = dict(configs.get(level, configs[1]))
    base["content_clip"] = _safe_int(os.getenv("REPORT_CONTENT_CLIP_BASE", str(base["content_clip"])), base["content_clip"], 320)
    base["group_size"] = _safe_int(os.getenv("REPORT_GROUP_SIZE", str(base["group_size"])), base["group_size"], 1)
    base["stage2_group_size"] = _safe_int(
        os.getenv("REPORT_STAGE2_GROUP_SIZE", str(base["stage2_group_size"])), base["stage2_group_size"], 1
    )
    base["map_max_tokens"] = _safe_int(
        os.getenv("REPORT_MAP_MAX_TOKENS", str(base["map_max_tokens"])), base["map_max_tokens"], 240
    )
    base["stage2_max_tokens"] = _safe_int(
        os.getenv("REPORT_STAGE2_MAX_TOKENS", str(base["stage2_max_tokens"])), base["stage2_max_tokens"], 320
    )
    return base


def _adaptive_report_targets(estimated_doc_chunks: int, compact_level: int) -> Dict[str, int]:
    if estimated_doc_chunks >= 1200:
        base = {"min_chars": 12000, "max_chars": 18000, "max_tokens": 8000}
    elif estimated_doc_chunks >= 600:
        base = {"min_chars": 8000, "max_chars": 14000, "max_tokens": 6000}
    elif estimated_doc_chunks >= 240:
        base = {"min_chars": 5000, "max_chars": 10000, "max_tokens": 4500}
    elif estimated_doc_chunks >= 80:
        base = {"min_chars": 3000, "max_chars": 6000, "max_tokens": 3200}
    else:
        base = {"min_chars": 1500, "max_chars": 3500, "max_tokens": 2200}
    scale = {0: 1.2, 1: 1.0, 2: 0.75}.get(compact_level, 1.0)
    return {
        "min_chars": int(base["min_chars"] * scale),
        "max_chars": int(base["max_chars"] * scale),
        "max_tokens": int(base["max_tokens"] * scale),
    }


def _safe_int(value: Any, default: int, min_value: int = 1) -> int:
    try:
        return max(min_value, int(str(value).strip()))
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _report_full_runtime_config() -> Dict[str, Any]:
    return {
        "batch_size": _safe_int(os.getenv("REPORT_FULL_BATCH_SIZE", "24"), 24, 1),
        "max_concurrency": _safe_int(os.getenv("REPORT_FULL_MAX_CONCURRENCY", "2"), 2, 1),
        "require_complete": _safe_bool(os.getenv("REPORT_FULL_REQUIRE_COMPLETE", "1"), True),
    }


def _report_validation_runtime_config() -> Dict[str, Any]:
    return {
        # 临时开关：仅影响 /insights/report 链路的校验图谱影响，不改四库流水线写入逻辑。
        "disable_validation_graph": _safe_bool(os.getenv("REPORT_DISABLE_VALIDATION_GRAPH", "1"), True),
    }


def _detail_first_runtime_config() -> Dict[str, Any]:
    return {
        "stage2_item_limit": _safe_int(os.getenv("REPORT_STAGE2_ITEM_LIMIT", "16"), 16, 2),
        "final_item_limit": _safe_int(os.getenv("REPORT_FINAL_ITEM_LIMIT", "24"), 24, 3),
        "report_context_rows": _safe_int(os.getenv("REPORT_CONTEXT_ROWS", "24"), 24, 4),
        "report_context_clip": _safe_int(os.getenv("REPORT_CONTEXT_CLIP", "1600"), 1600, 400),
        "report_max_chars_floor": _safe_int(os.getenv("REPORT_MAX_CHARS_FLOOR", "12000"), 12000, 1200),
        "report_content_clip": _safe_int(os.getenv("REPORT_CONTENT_CLIP", "1600"), 1600, 400),
    }


def _normalize_report_sections(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()
            if not content:
                normalized = _normalize_summary_item(item)
                content = normalized
            if title or content:
                out.append({"title": title or "分节", "content": content})
                continue
        text = _normalize_summary_item(item)
        if text:
            out.append({"title": "分节", "content": text})
    return out[:16]


def _legacy_build_minimum_report_fallback(
    query: str,
    report_reduce_context: str,
    rows: List[Any],
    evidence: List[Dict[str, Any]],
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    detail_runtime = _detail_first_runtime_config()
    fallback_key_limit = max(6, int(detail_runtime["stage2_item_limit"]))
    fallback_section_limit = max(8, int(detail_runtime["final_item_limit"]))
    key_points: List[str] = []
    reduced_lines = [line.strip("- ").strip() for line in report_reduce_context.splitlines() if line.strip()]
    for line in reduced_lines:
        if len(line) < 6:
            continue
        key_points.append(line[:260])
        if len(key_points) >= fallback_key_limit:
            break
    if not key_points:
        for row in rows[:fallback_key_limit]:
            title = str(row.get("title", "未命名来源")).strip() or "未命名来源"
            snippet = str(row.get("content", "")).replace("\n", " ").strip()
            if snippet:
                key_points.append(f"{title}：{snippet[:240]}")
            if len(key_points) >= fallback_key_limit:
                break
    if not key_points:
        key_points = ["当前检索结果有限，已基于现有上下文整理可读摘要。"]

    conclusions: List[str] = [f"围绕“{query or '当前问题'}”已完成基础归纳，可作为后续决策起点。"]
    if evidence:
        conclusions.append("已有可追溯引用来源，建议结合原文片段复核关键判断。")
    else:
        conclusions.append("当前引用覆盖较弱，建议补充资料以提高结论稳健性。")

    actions: List[str] = [
        "优先核验引用条目的章节与结论是否一致。",
        "补充问题边界（场景、对象、时间范围）后再生成完整版报告。",
        "如用于执行，请将关键建议转为负责人和时间节点。",
    ]
    sections = [
        {"title": "要点", "content": "；".join(key_points[:fallback_section_limit])},
        {"title": "结论", "content": "；".join(conclusions[: max(4, fallback_section_limit // 2)])},
        {"title": "行动建议", "content": "；".join(actions[: max(4, fallback_section_limit // 2)])},
    ]
    report = "\n\n".join([f"## {item['title']}\n- " + "\n- ".join(item["content"].split("；")) for item in sections]).strip()
    return {"report": report, "sections": sections}


def _build_minimum_report_fallback(
    query: str,
    report_reduce_context: str,
    rows: List[Any],
    evidence: List[Dict[str, Any]],
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    detail_runtime = _detail_first_runtime_config()
    fallback_key_limit = max(6, int(detail_runtime["stage2_item_limit"]))
    fallback_section_limit = max(8, int(detail_runtime["final_item_limit"]))
    key_points: List[str] = []
    reduced_lines = [line.strip("- ").strip() for line in report_reduce_context.splitlines() if line.strip()]
    for line in reduced_lines:
        if len(line) < 6:
            continue
        key_points.append(line[:260])
        if len(key_points) >= fallback_key_limit:
            break
    if not key_points:
        for row in rows[:fallback_key_limit]:
            title = str(row.get("title", "未命名来源")).strip() or "未命名来源"
            snippet = str(row.get("content", "")).replace("\n", " ").strip()
            if snippet:
                key_points.append(f"{title}: {snippet[:240]}")
            if len(key_points) >= fallback_key_limit:
                break
    if not key_points:
        key_points = ["当前检索结果有限，已基于现有上下文整理出可读摘要。"]

    conclusions: List[str] = [f"围绕“{query or '当前问题'}”已完成基础归纳，可作为后续研究和写作的起点。"]
    if evidence:
        conclusions.append("已有可追溯引用来源，建议结合原文页码与章节位置继续复核关键判断。")
    else:
        conclusions.append("当前引用覆盖较弱，建议补充资料以提高结论稳健性。")

    actions: List[str] = [
        "优先核验引用条目所在章节与当前结论是否一致。",
        "补充更明确的研究边界、对象和时间范围后再生成完整版分析。",
        "若用于研究写作，请把关键判断转写为可追溯的观点-证据对。",
    ]
    profile_info = profile or _infer_academic_report_profile(rows)
    profile_kind = str(profile_info.get("kind", "generic"))
    structure_lines = _extract_tree_lines_from_profile(profile_info, limit=max(4, fallback_section_limit // 2))
    if profile_kind == "paper":
        sections = [
            {"title": "研究问题与核心论点", "content": "；".join(key_points[:fallback_section_limit])},
            {"title": "方法与证据", "content": "；".join(conclusions[: max(4, fallback_section_limit // 2)])},
            {"title": "结构与章节线索", "content": "；".join(structure_lines or ["已按论文结构组织信息，但章节树仍可继续细化。"])},
            {"title": "局限性与后续建议", "content": "；".join(actions[: max(4, fallback_section_limit // 2)])},
        ]
    elif profile_kind == "book":
        sections = [
            {"title": "全书核心主题", "content": "；".join(key_points[:fallback_section_limit])},
            {"title": "章节递进与论证线索", "content": "；".join(structure_lines or conclusions[: max(4, fallback_section_limit // 2)])},
            {"title": "关键结论与证据", "content": "；".join(conclusions[: max(4, fallback_section_limit // 2)])},
            {"title": "研究价值与延展建议", "content": "；".join(actions[: max(4, fallback_section_limit // 2)])},
        ]
    else:
        sections = [
            {"title": "要点", "content": "；".join(key_points[:fallback_section_limit])},
            {"title": "结论", "content": "；".join(conclusions[: max(4, fallback_section_limit // 2)])},
            {"title": "行动建议", "content": "；".join(actions[: max(4, fallback_section_limit // 2)])},
        ]
    report = "\n\n".join([f"## {item['title']}\n- " + "\n- ".join(item["content"].split("；")) for item in sections]).strip()
    return {"report": report, "sections": sections}


def _infer_academic_report_profile(rows: List[Any]) -> Dict[str, Any]:
    metadata = parse_json_object(str(rows[0].get("metadata", ""))) if rows else {}
    document_type = str((rows[0].get("document_type", "") if rows else "") or metadata.get("document_type", "")).strip().lower()
    tree = _build_document_tree(rows)
    derived_titles = [str(item.get("title", "")).strip().lower() for item in tree]
    paper_markers = ("abstract", "introduction", "method", "results", "discussion", "conclusion", "references")
    paper_hits = sum(1 for title in derived_titles if any(marker in title for marker in paper_markers))
    toc_count = len(metadata.get("toc", [])) if isinstance(metadata.get("toc"), list) else 0
    if document_type == "academic" and (paper_hits >= 2 or any("abstract" in title for title in derived_titles)):
        kind = "paper"
    elif toc_count >= 3 or len(tree) >= 5:
        kind = "book"
    else:
        kind = "generic"
    if kind == "paper":
        blueprint = ["研究问题与核心论点", "方法与数据", "主要发现", "局限性与可信度", "研究价值与延展"]
        label = "论文/学术文章"
    elif kind == "book":
        blueprint = ["全书结构图", "核心主题", "章节递进关系", "关键证据与案例", "研究价值与局限"]
        label = "书籍/长篇著作"
    else:
        blueprint = ["研究问题", "结构脉络", "方法与证据", "主要发现", "局限性", "研究价值"]
        label = "学术长文档"
    return {"kind": kind, "label": label, "section_blueprint": blueprint, "tree": tree}


def _build_document_tree(rows: List[Any]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    metadata = parse_json_object(str(rows[0].get("metadata", "")))
    toc = metadata.get("toc") if isinstance(metadata, dict) else []
    if isinstance(toc, list) and toc:
        out: List[Dict[str, Any]] = []
        for item in toc[:24]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            page_num = _safe_int(item.get("page", item.get("page_num", 0)), 0, 0)
            level = _safe_int(item.get("level", 1), 1, 1)
            if not title:
                continue
            out.append({"title": title, "page_start": page_num, "page_end": page_num, "level": level, "source": "toc"})
        if out:
            return out
    grouped = _group_rows_for_hierarchical_report(rows, chunk_size=max(len(rows), 1))
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in grouped:
        key = str(item.get("chapter_key", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": str(item.get("chapter_title", "章节")), "page_start": item.get("page_start"), "page_end": item.get("page_end"), "level": 1, "source": "derived"})
    return out[:24]


def _render_document_tree(tree: List[Dict[str, Any]], limit: int = 18) -> str:
    lines: List[str] = []
    for item in tree[:limit]:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        level = max(1, _safe_int(item.get("level", 1), 1, 1))
        indent = "  " * max(0, min(level - 1, 3))
        page_span = _format_page_span(item.get("page_start"), item.get("page_end"))
        lines.append(f"{indent}- {title}{page_span}")
    return "\n".join(lines)


def _extract_tree_lines_from_profile(profile: Dict[str, Any], limit: int = 6) -> List[str]:
    tree = profile.get("tree", []) if isinstance(profile, dict) else []
    if not isinstance(tree, list):
        return []
    lines: List[str] = []
    for item in tree[:limit]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        lines.append(f"{title}{_format_page_span(item.get('page_start'), item.get('page_end'))}")
    return lines


def _prefer_chinese_for_long_doc(rows: List[Any]) -> bool:
    if not rows:
        return False
    sample_parts: List[str] = []
    metadata = parse_json_object(str(rows[0].get("metadata", ""))) if rows else {}
    if isinstance(metadata, dict):
        sample_parts.append(str(metadata.get("title", "")))
        toc = metadata.get("toc", [])
        if isinstance(toc, list):
            for item in toc[:6]:
                if isinstance(item, dict):
                    sample_parts.append(str(item.get("title", "")))
    for row in rows[:6]:
        sample_parts.append(str(row.get("title", "")))
        sample_parts.append(str(row.get("content", ""))[:300])
    return _contains_cjk(" ".join(sample_parts))


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text or ""))


def _looks_mostly_english(text: str) -> bool:
    if not text:
        return False
    latin = len(re.findall(r"[A-Za-z]", text))
    cjk = len(re.findall(r"[一-鿿]", text))
    return latin >= 40 and latin > cjk * 3





async def _merge_global_summaries(
    self,
    stage2_outputs: List[Dict[str, Any]],
    state: Dict[str, Any],
    report_cfg: Dict[str, Any],
    detail_runtime: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    合并多个全局综述Part，生成统一的全局综述
    """
    if not stage2_outputs or len(stage2_outputs) <= 1:
        return None
    
    # 构建合并的上下文
    parts_text: List[str] = []
    for idx, item in enumerate(stage2_outputs, start=1):
        key_points = " ".join(item.get("key_points", [])[:int(detail_runtime["final_item_limit"])])
        analysis = " ".join(item.get("analysis", [])[:int(detail_runtime["final_item_limit"])])
        recommendations = " ".join(item.get("recommendations", [])[:int(detail_runtime["final_item_limit"])])
        risks = " ".join(item.get("risks", [])[:int(detail_runtime["final_item_limit"])])
        
        parts_text.append(
            f"### 全局综述 Part#{idx}\n"
            f"核心要点: {key_points}\n"
            f"分析深度: {analysis}\n"
            f"建议措施: {recommendations}\n"
            f"风险提示: {risks}"
        )
    
    prompt = (
        "你是一个高级文档分析专家。请将以下多个全局综述部分合并成一个统一、连贯的全局综述。\n"
        "【关键要求】：\n"
        "- 将多个部分的核心信息有机融合，避免简单拼接\n"
        "- 保持逻辑连贯性，使用'首先'、'其次'、'此外'、'综上所述'等连接词\n"
        "- 提取最关键的全局洞察，避免冗余信息\n"
        "- 返回JSON对象，包含字段: key_points, analysis, recommendations, risks\n"
        "- 每个字段都必须返回一个完整的、连贯的中文段落，而不是列表\n"
        "- 段落长度约300-500字，确保覆盖所有重要方面\n"
        f"用户查询：{state.get('query', '')}\n\n"
        f"需要合并的全局综述部分：\n{chr(10).join(parts_text)}"
    )
    
    try:
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="summarize",
            max_tokens=int(report_cfg.get("stage2_max_tokens", 1200)),
            temperature=0.2,
            prefer_free=True,
        )
        parsed = parse_json_object(str(resp.get("content", "")))
        if isinstance(parsed, dict):
            return {
                "provider": str(resp.get("provider", "unknown")),
                "key_points": _to_list(parsed.get("key_points")),
                "analysis": _to_list(parsed.get("analysis")),
                "recommendations": _to_list(parsed.get("recommendations")),
                "risks": _to_list(parsed.get("risks")),
            }
    except Exception as e:
        logger.warning(f"Failed to merge global summaries: {e}")
    
    return None
