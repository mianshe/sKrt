import asyncio
import os
import re
from typing import Any, Dict, List

from .state import build_reasoning_gates, normalize_evidence, parse_json_object, sanitize_answer, sanitize_brief_reasoning


class GraphNodes:
    def __init__(self, ai_router: Any, rag_engine: Any) -> None:
        self.ai_router = ai_router
        self.rag_engine = rag_engine

    async def retrieve_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        query = str(state.get("query", "")).strip()
        discipline = str(state.get("discipline", "all")).strip() or "all"
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
        )
        rows = retrieval.get("results", [])
        return {
            "retrieved": rows,
            "compressed_context": retrieval.get("compressed_context", ""),
            "cross_discipline": retrieval.get("cross_discipline", []),
            "evidence": normalize_evidence(rows, limit=6),
        }

    async def recover_sparse_evidence(self, state: Dict[str, Any]) -> Dict[str, Any]:
        evidence = state.get("evidence", [])
        if isinstance(evidence, list) and len(evidence) >= 2:
            return {}
        query = str(state.get("query", "")).strip()
        discipline = str(state.get("discipline", "all")).strip() or "all"
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
        )
        rows = retrieval.get("results", [])
        return {
            "retrieved": rows,
            "compressed_context": retrieval.get("compressed_context", state.get("compressed_context", "")),
            "cross_discipline": retrieval.get("cross_discipline", state.get("cross_discipline", [])),
            "evidence": normalize_evidence(rows, limit=6),
            "fallback_reason": "retry_retrieval",
        }

    async def retrieve_summary_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        query = str(state.get("query", "")).strip()
        discipline = str(state.get("discipline", "all")).strip() or "all"
        document_id = int(state.get("document_id", 0) or 0) or None
        summary_mode = str(state.get("summary_mode", "fast")).strip().lower() or "fast"
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        tenant_id = str(state.get("tenant_id", "")).strip() or None
        billing_client_id = str(state.get("billing_client_id", "")).strip() or None
        billing_exempt = bool(state.get("billing_exempt", False))
        percent_cfg = _summary_percent_config_by_compact_level(compact_level)
        preferred_top_k = int(percent_cfg["retrieval_top_k"])
        estimated_doc_chunks = self.rag_engine.estimate_document_chunk_count(document_id, tenant_id=tenant_id)
        if summary_mode == "full" and isinstance(document_id, int) and document_id > 0:
            full_limit = _full_mode_doc_limit(estimated_doc_chunks)
            doc_rows = self.rag_engine.load_document_chunks(
                document_id=document_id,
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
                    "estimated_doc_chunks": estimated_doc_chunks,
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
        prompt = (
            "你是内部推理节点。只返回JSON对象，字段: claim, evidence_summary, counterexample_check, consistency_check。\n"
            "要求：每个字段一句话，不输出多余解释。\n"
            f"问题：{state.get('query', '')}\n"
            f"证据上下文：\n{state.get('compressed_context', '')}"
        )
        resp = await self.ai_router.chat_with_task(
            [{"role": "user", "content": prompt}],
            task_type="reason",
            max_tokens=420,
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
        provider = str(resp.get("provider", state.get("provider", "unknown")))
        return {"internal_reasoning": reasoning, "provider": provider}

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

        async def _run_map_group(idx: int, group: List[Any]) -> Dict[str, Any]:
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
        document_id = int(state.get("document_id", 0) or 0) or None
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        report_mode = str(state.get("report_mode", "full")).strip().lower() or "full"
        tenant_id = str(state.get("tenant_id", "")).strip() or None
        billing_client_id = str(state.get("billing_client_id", "")).strip() or None
        billing_exempt = bool(state.get("billing_exempt", False))
        report_cfg = _report_config_by_compact_level(compact_level)
        estimated_doc_chunks = self.rag_engine.estimate_document_chunk_count(document_id, tenant_id=tenant_id)
        sampling_strategy = "coverage" if report_mode == "full" else "head"
        if isinstance(document_id, int) and document_id > 0:
            full_runtime = _report_full_runtime_config()
            if report_mode == "full":
                doc_rows = self.rag_engine.load_document_chunks(
                    document_id=document_id,
                    discipline_filter=discipline,
                    limit=None,
                    sampling_strategy="head",
                    tenant_id=tenant_id,
                )
                raw_total = len(doc_rows)
                return {
                    "retrieved": doc_rows,
                    "focus_blocks": [],
                    "cross_discipline": [],
                    "evidence": normalize_evidence(doc_rows, limit=8),
                    "estimated_doc_chunks": estimated_doc_chunks,
                    "retrieval_stats": {
                        "retrieval_strategy": "doc_chunks_full_scan",
                        "doc_limit": 0,
                        "retrieved_rows": raw_total,
                        "raw_total_chunks": raw_total,
                        "estimated_doc_chunks": estimated_doc_chunks,
                        "full_require_complete": bool(full_runtime["require_complete"]),
                    },
                }
            doc_limit = int(report_cfg["doc_limit"])
            if report_mode == "full" and compact_level == 0:
                doc_limit = int(max(doc_limit, min(estimated_doc_chunks, doc_limit * 2)))
            doc_rows = self.rag_engine.load_document_chunks(
                document_id=document_id,
                discipline_filter=discipline,
                limit=doc_limit,
                sampling_strategy=sampling_strategy,
                tenant_id=tenant_id,
            )
            if doc_rows:
                return {
                    "retrieved": doc_rows,
                    "focus_blocks": [],
                    "cross_discipline": [],
                    "evidence": normalize_evidence(doc_rows, limit=8),
                    "estimated_doc_chunks": estimated_doc_chunks,
                    "retrieval_stats": {
                        "retrieval_strategy": f"doc_chunks_{sampling_strategy}",
                        "doc_limit": doc_limit,
                        "retrieved_rows": len(doc_rows),
                        "estimated_doc_chunks": estimated_doc_chunks,
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

    async def map_reduce_report(self, state: Dict[str, Any]) -> Dict[str, Any]:
        rows = state.get("retrieved", [])
        if not isinstance(rows, list) or not rows:
            return {"report_reduce_context": "", "coverage_stats": {"mode": "full", "processed_rows": 0, "total_rows": 0, "coverage_ratio": 0.0, "map_groups": 0}}
        compact_level = _normalize_compact_level(state.get("summary_compact_level", 1), default=1)
        report_mode = str(state.get("report_mode", "full")).strip().lower() or "full"
        report_cfg = _report_config_by_compact_level(compact_level)
        estimated_total = _estimate_summary_scale(state, rows)
        full_runtime = _report_full_runtime_config()
        detail_runtime = _detail_first_runtime_config()
        if report_mode == "full":
            candidate_rows = rows
            batch_size = int(full_runtime["batch_size"])
            groups = _chunk_rows(candidate_rows, size=batch_size)
        else:
            candidate_limit = max(1, min(len(rows), int(report_cfg["coverage_floor"])))
            if estimated_total > 0:
                candidate_limit = max(candidate_limit, min(len(rows), int(round(estimated_total * float(report_cfg["coverage_ratio"])))))
            candidate_rows = _sample_rows_for_coverage(rows, candidate_limit)
            groups = _chunk_rows(candidate_rows, size=int(report_cfg["group_size"]))

        max_concurrency = int(full_runtime["max_concurrency"]) if report_mode == "full" else 3
        max_concurrency = max(1, min(6, max_concurrency))
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _run_report_map(idx: int, group: List[Any]) -> Dict[str, Any]:
            blocks = []
            for row in group:
                blocks.append(
                    f"[{row.get('title')}::{row.get('section_path')}::{row.get('discipline')}]\n"
                    f"{str(row.get('content', ''))[:int(max(report_cfg['content_clip'], detail_runtime['report_content_clip']))]}"
                )
            prompt = (
                "你是长文报告Map节点。仅返回JSON对象，字段: key_points, analysis, recommendations, risks。\n"
                "每个字段为数组，内容要具体并含证据指向，不输出推理过程。\n"
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
            return {
                "provider": str(resp.get("provider", "unknown")),
                "key_points": _to_list(parsed.get("key_points") if isinstance(parsed, dict) else []),
                "analysis": _to_list(parsed.get("analysis") if isinstance(parsed, dict) else []),
                "recommendations": _to_list(parsed.get("recommendations") if isinstance(parsed, dict) else []),
                "risks": _to_list(parsed.get("risks") if isinstance(parsed, dict) else []),
            }

        map_outputs = await asyncio.gather(*[_run_report_map(i, g) for i, g in enumerate(groups, start=1)])
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
                "mode": report_mode,
                "processed_rows": processed_rows,
                "total_rows": estimated_total,
                "coverage_ratio": coverage_ratio,
                "map_groups": len(groups),
                "raw_total_chunks": raw_total,
                "after_doc_limit": raw_retrieved,
                "after_candidate_limit": len(candidate_rows),
                "retrieval_strategy": str(retrieval_stats.get("retrieval_strategy", "unknown")),
                "full_require_complete": bool(full_runtime["require_complete"]) if report_mode == "full" else False,
            },
            "provider": (stage2_outputs[-1].get("provider", "unknown") if stage2_outputs else (map_outputs[-1].get("provider", "unknown") if map_outputs else "unknown")),
        }

    async def generate_report_contract(self, state: Dict[str, Any]) -> Dict[str, Any]:
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
            "你是超长文报告生成Agent。仅返回JSON对象，字段: report, sections, citations, purpose, subjects, subject_object_links, how_to, why。\n"
            "report为完整中文报告文本；sections为数组，每项包含title和content；citations每项包含title, discipline, section_path。\n"
            "另外返回五维字段：purpose（一句话）、subjects、subject_object_links、how_to、why（后四项均为数组）。\n"
            f"目标报告字数区间：{int(targets['min_chars'])}-{max_chars}。\n"
            f"问题：{state.get('query', '')}\n"
            f"上下文：\n{chr(10).join([x for x in context_blocks if x])}"
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
        report_mode = str(state.get("report_mode", "full")).strip().lower() or "full"
        if report_mode == "full" and bool(full_runtime["require_complete"]) and not validation_graph_skipped:
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
        base = {"min_chars": 4200, "max_chars": 6200, "max_tokens": 3600}
    elif estimated_doc_chunks >= 600:
        base = {"min_chars": 3000, "max_chars": 4600, "max_tokens": 2800}
    elif estimated_doc_chunks >= 240:
        base = {"min_chars": 2200, "max_chars": 3400, "max_tokens": 2200}
    elif estimated_doc_chunks >= 80:
        base = {"min_chars": 1500, "max_chars": 2600, "max_tokens": 1800}
    else:
        base = {"min_chars": 900, "max_chars": 1800, "max_tokens": 1400}
    scale = {0: 1.15, 1: 1.0, 2: 0.85}.get(compact_level, 1.0)
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
        "stage2_item_limit": _safe_int(os.getenv("REPORT_STAGE2_ITEM_LIMIT", "8"), 8, 2),
        "final_item_limit": _safe_int(os.getenv("REPORT_FINAL_ITEM_LIMIT", "12"), 12, 3),
        "report_context_rows": _safe_int(os.getenv("REPORT_CONTEXT_ROWS", "12"), 12, 4),
        "report_context_clip": _safe_int(os.getenv("REPORT_CONTEXT_CLIP", "1200"), 1200, 400),
        "report_max_chars_floor": _safe_int(os.getenv("REPORT_MAX_CHARS_FLOOR", "8800"), 8800, 1200),
        "report_content_clip": _safe_int(os.getenv("REPORT_CONTENT_CLIP", "1200"), 1200, 400),
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


def _build_minimum_report_fallback(
    query: str,
    report_reduce_context: str,
    rows: List[Any],
    evidence: List[Dict[str, Any]],
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
