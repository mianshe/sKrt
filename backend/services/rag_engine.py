import json
import math
import re
import time
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from . import embedding_token_billing
from . import knowledge_store
from .billing_mode import is_self_hosted_embedding_billing

from .free_ai_router import FreeAIRouter
from .text_cleanup import strip_layout_noise

try:
    from services.memory.pattern_separator import PatternSeparator
except Exception:  # pragma: no cover - optional experimental module
    PatternSeparator = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class RAGEngine:
    def __init__(self, ai_router: FreeAIRouter, pattern_separator: "PatternSeparator | None" = None) -> None:
        self.ai_router = ai_router
        self.pattern_separator = pattern_separator

    async def index_chunks(
        self, document_id: int, chunks: List[Dict[str, Any]], embedding_mode: str = "auto"
    ) -> Dict[str, Any]:
        return await self.index_chunks_for_tenant(
            document_id=document_id,
            chunks=chunks,
            tenant_id="public",
            embedding_mode=embedding_mode,
        )

    async def index_chunks_for_tenant(
        self,
        document_id: int,
        chunks: List[Dict[str, Any]],
        tenant_id: str,
        embedding_mode: str = "auto",
        enable_concept_evaluation: bool = False,
        teaching_evaluation_mode: str = "lightweight",  # "lightweight" or "full"
    ) -> Dict[str, Any]:
        conn = knowledge_store.connect()
        last_embedding_resp: Dict[str, Any] = {}
        teaching_evaluation_result: Dict[str, Any] = {}
        
        try:
            # 可选的：生成教学质量评估报告
            if enable_concept_evaluation:
                try:
                    from .concept_evaluator import ConceptTeachingEvaluator
                    from .teaching_organizer import TeachingPipeline
                    
                    evaluator = ConceptTeachingEvaluator()
                    teaching_pipeline = TeachingPipeline(enable_simulation=teaching_evaluation_mode=="full")
                    
                    # 评估文档解析结果
                    evaluation_result = evaluator.evaluate_document_parsing(
                        parsed_chunks=chunks,
                        document_title=f"Document_{document_id}"
                    )
                    
                    # 生成教学评估报告
                    teaching_report = teaching_pipeline.process_document_concepts(chunks)
                    
                    teaching_evaluation_result = {
                        "concept_evaluation": evaluation_result,
                        "teaching_report": teaching_report,
                        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "mode": teaching_evaluation_mode,
                    }
                    
                    logger.info(f"Generated teaching evaluation for document {document_id}, overall score: {evaluation_result.get('overall_score', 0):.2f}")
                    
                except Exception as e:
                    logger.warning(f"Teaching evaluation failed: {e}", exc_info=True)
                    teaching_evaluation_result = {
                        "error": str(e),
                        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "mode": teaching_evaluation_mode,
                    }
            
            # 索引chunks
            for chunk in chunks:
                embedding_resp = await self.ai_router.embed(chunk["content"], embedding_mode=embedding_mode)
                last_embedding_resp = dict(embedding_resp)
                embedding = embedding_resp["embedding"]
                
                # ② DG pattern separation: orthogonalize before storing
                if self.pattern_separator is not None:
                    embedding = self.pattern_separator.separate(embedding)
                # 存储teaching_evaluation_result到chunk的metadata中
                chunk_metadata = chunk.get("metadata", {})
                if teaching_evaluation_result:
                    chunk_metadata["teaching_evaluation"] = teaching_evaluation_result
                
                conn.execute(
                    """
                    INSERT INTO vectors (document_id, chunk_id, content, section_path, embedding, tenant_id, chunk_type, page_num, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        chunk["chunk_id"],
                        chunk["content"],
                        chunk["section_path"],
                        json.dumps(embedding),
                        tenant_id,
                        chunk.get("chunk_type", "knowledge"),
                        chunk.get("page_num", 0),
                        json.dumps(chunk_metadata),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        
        # 返回结果，包括教学评估结果
        result = last_embedding_resp
        if teaching_evaluation_result:
            result["teaching_evaluation"] = teaching_evaluation_result
        
        return result

    async def hybrid_search(
        self,
        query: str,
        discipline_filter: Optional[str] = None,
        document_id: Optional[int] = None,
        top_k: int = 8,
        tenant_id: Optional[str] = None,
        billing_client_id: Optional[str] = None,
        billing_exempt: bool = False,
        embedding_mode: str = "auto",
    ) -> Dict[str, Any]:
        embedding_resp = await self.ai_router.embed(query, embedding_mode=embedding_mode)
        query_vec = embedding_resp["embedding"]

        # ② DG pattern separation: orthogonalize query
        if self.pattern_separator is not None:
            query_vec = self.pattern_separator.separate(query_vec)
        query_model_id = str(embedding_resp.get("model_id") or "").strip()
        self._charge_query_embedding_if_needed(
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
            embedding_resp=embedding_resp,
            reason="search_query_embedding",
        )
        rows = self._fetch_rows(
            discipline_filter,
            document_id=document_id,
            tenant_id=tenant_id,
            embedding_model_id=query_model_id,
        )
        if not rows:
            return {"results": [], "cross_discipline": []}

        # 只有当明确指定document_id时才进行文档边界过滤
        # 这允许跨文档知识融合，同时保持向后兼容性
        if document_id is not None:
            actual_doc_ids = {str(r.get("document_id")) for r in rows if r.get("document_id")}
            if len(actual_doc_ids) > 1 or (actual_doc_ids and str(document_id) not in actual_doc_ids):
                logger.warning(
                    f"检索结果包含非指定文档ID: request={document_id}, actual={list(actual_doc_ids)}, "
                    f"total_rows={len(rows)}, filtering..."
                )
                # 强制过滤到指定文档
                rows = [r for r in rows if r.get("document_id") == document_id]
            else:
                # 记录跨文档检索的情况（用于监控和调试）
                if len(actual_doc_ids) > 1:
                    logger.info(
                        f"跨文档检索: 搜索查询包含 {len(actual_doc_ids)} 个文档, "
                        f"文档ID={sorted(actual_doc_ids)[:5]}, 查询='{query[:100]}...'"
                    )

        dense_ranked = self._dense_rank(rows, query_vec)
        sparse_ranked = self._sparse_rank(rows, query)
        multi_document_mode = document_id is None
        fusion_top_k = top_k if not multi_document_mode else max(top_k * 4, 24)
        merged = self._rrf_fusion(dense_ranked, sparse_ranked, top_k=fusion_top_k)
        if multi_document_mode:
            merged = self._rebalance_multi_document_results(merged, top_k=top_k)
        else:
            merged = merged[:top_k]
        cross = self._cross_discipline_expand(merged, discipline_filter)
        return {"results": merged, "cross_discipline": cross}

    async def summary_search_with_qa_focus(
        self,
        query: str,
        discipline_filter: Optional[str] = None,
        document_id: Optional[int] = None,
        top_k: int = 8,
        max_qa_pairs: int = 3,
        tenant_id: Optional[str] = None,
        billing_client_id: Optional[str] = None,
        billing_exempt: bool = False,
        embedding_mode: str = "auto",
    ) -> Dict[str, Any]:
        expanded_k = max(top_k * 3, 18)
        retrieval = await self.hybrid_search(
            query=query,
            discipline_filter=discipline_filter,
            document_id=document_id,
            top_k=expanded_k,
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
            embedding_mode=embedding_mode,
        )
        base_rows = retrieval.get("results", [])
        ranked_rows = self._prioritize_for_summary(base_rows, top_k=top_k)
        qa_focus_blocks = self._build_qa_focus_blocks(ranked_rows, max_pairs=max_qa_pairs)
        return {
            "results": ranked_rows,
            "focus_blocks": qa_focus_blocks,
            "cross_discipline": retrieval.get("cross_discipline", []),
        }

    async def prepare_agent_context(
        self,
        query: str,
        discipline_filter: Optional[str],
        document_id: Optional[int] = None,
        top_k: int = 6,
        compress_limit: int = 4,
        tenant_id: Optional[str] = None,
        billing_client_id: Optional[str] = None,
        billing_exempt: bool = False,
        embedding_mode: str = "auto",
    ) -> Dict[str, Any]:
        """
        Agent-oriented RAG pipeline: retrieve -> lightweight compress -> answer context.
        支持跨文档知识融合：当document_id为None时，从用户所有文档中检索。
        """
        retrieval = await self.hybrid_search(
            query=query,
            discipline_filter=discipline_filter,
            document_id=document_id,
            top_k=max(top_k, 1),
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
            embedding_mode=embedding_mode,
        )
        rows = retrieval.get("results", [])
        
        # 只有当明确指定document_id时才进行文档边界过滤
        # 这允许跨文档知识融合，同时保持向后兼容性
        if document_id is not None:
            filtered_rows = []
            mismatched_rows = []
            for row in rows:
                row_doc_id = row.get("document_id")
                if isinstance(row_doc_id, (int, str)):
                    try:
                        row_doc_id_int = int(row_doc_id)
                        if row_doc_id_int == document_id:
                            filtered_rows.append(row)
                        else:
                            mismatched_rows.append(row_doc_id_int)
                    except (ValueError, TypeError):
                        # 如果无法转换为整数，视为不匹配
                        mismatched_rows.append(str(row_doc_id))
                else:
                    # 没有document_id的行，视为不匹配
                    mismatched_rows.append(None)
            
            if mismatched_rows:
                logger.warning(
                    f"prepare_agent_context过滤掉{len(mismatched_rows)}条非指定文档结果: "
                    f"request_doc_id={document_id}, mismatched_doc_ids={list(set(mismatched_rows))[:5]}, "
                    f"total_rows={len(rows)}, remaining={len(filtered_rows)}"
                )
                rows = filtered_rows
            
            # 如果没有找到指定文档的结果，记录警告（仅当明确指定文档时）
            if not rows:
                logger.warning(
                    f"prepare_agent_context未找到指定文档的内容: document_id={document_id}, "
                    f"query='{query[:100]}...', discipline_filter={discipline_filter}"
                )
        else:
            # 当document_id为None时，记录跨文档检索的情况
            if rows:
                doc_ids = {str(row.get("document_id")) for row in rows if row.get("document_id")}
                if len(doc_ids) > 1:
                    logger.info(
                        f"跨文档知识融合: 查询 '{query[:80]}...' 从 {len(doc_ids)} 个文档中检索, "
                        f"文档ID={sorted(doc_ids)[:5]}, 共 {len(rows)} 个结果"
                    )
        
        compressed_blocks: List[str] = []
        for row in rows[:compress_limit]:
            title = str(row.get("title", "未命名来源"))
            section = str(row.get("section_path", "N/A"))
            content = self._clean_snippet(str(row.get("content", "")), max_len=360)
            # 添加文档ID信息以帮助区分来源
            doc_id = row.get("document_id")
            doc_info = f"[文档{int(doc_id) if doc_id else '未知'}]" if doc_id else ""
            compressed_blocks.append(f"{doc_info}[{title}::{section}]\n{content}")
        
        return {
            "results": rows,
            "cross_discipline": retrieval.get("cross_discipline", []),
            "compressed_context": "\n\n".join(compressed_blocks) if compressed_blocks else "暂无检索上下文",
            "document_id_filtered": bool(document_id is not None),
            "filtered_count": len(rows),
            "multi_document_mode": document_id is None,
            "documents_used_count": len({row.get("document_id") for row in rows if row.get("document_id")}),
        }

    async def cross_document_knowledge_transfer(
        self,
        query: str,
        source_document_id: int,
        target_document_id: Optional[int] = None,
        discipline_filter: Optional[str] = None,
        top_k: int = 6,
        transfer_threshold: float = 0.6,
        tenant_id: Optional[str] = None,
        billing_client_id: Optional[str] = None,
        billing_exempt: bool = False,
        embedding_mode: str = "auto",
    ) -> Dict[str, Any]:
        """
        跨文档知识迁移：从源文档检索相关信息，并考虑迁移到目标文档的概念融合
        """
        # 步骤1: 从源文档检索相关信息
        source_results = await self.hybrid_search(
            query=query,
            discipline_filter=discipline_filter,
            document_id=source_document_id,
            top_k=top_k,
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
            embedding_mode=embedding_mode,
        )
        source_rows = source_results.get("results", [])
        
        if not source_rows:
            return {
                "transfer_results": [],
                "source_info": {"document_id": source_document_id, "results_count": 0},
                "target_info": {"document_id": target_document_id, "results_count": 0} if target_document_id else None,
                "transfer_analysis": "未找到源文档相关内容",
                "transfer_suggestions": [],
            }
        
        # 步骤2: 提取源文档的核心概念
        source_concepts = self._extract_core_concepts(source_rows)
        
        # 步骤3: 如果指定了目标文档，在该文档中搜索相关概念
        target_results = []
        target_connections = []
        if target_document_id:
            # 为每个核心概念在目标文档中搜索
            for concept in source_concepts[:5]:  # 限制前5个概念
                concept_results = await self.hybrid_search(
                    query=concept["concept"],
                    discipline_filter=discipline_filter,
                    document_id=target_document_id,
                    top_k=2,  # 每个概念取少量相关结果
                    tenant_id=tenant_id,
                    billing_client_id=billing_client_id,
                    billing_exempt=billing_exempt,
                    embedding_mode=embedding_mode,
                )
                target_rows = concept_results.get("results", [])
                if target_rows:
                    target_results.extend(target_rows[:2])
                    # 记录概念迁移关系
                    target_connections.append({
                        "source_concept": concept["concept"],
                        "relevance_score": concept["relevance"],
                        "target_matches": [
                            {
                                "content": self._clean_snippet(row.get("content", ""), max_len=120),
                                "similarity": row.get("dense_score", 0.0),
                            }
                            for row in target_rows[:2]
                        ],
                    })
        
        # 步骤4: 生成知识迁移建议
        transfer_suggestions = self._generate_knowledge_transfer_suggestions(
            source_rows, source_concepts, target_connections, transfer_threshold
        )
        
        # 步骤5: 准备压缩的上下文（包含跨文档信息）
        compressed_blocks: List[str] = []
        
        # 源文档内容
        for i, row in enumerate(source_rows[:3]):
            title = str(row.get("title", "未命名来源"))
            section = str(row.get("section_path", "N/A"))
            content = self._clean_snippet(str(row.get("content", "")), max_len=240)
            compressed_blocks.append(f"[源文档{source_document_id}:{title}::{section}]\n{content}")
        
        # 目标文档内容（如果存在）
        if target_results:
            for i, row in enumerate(target_results[:3]):
                title = str(row.get("title", "未命名来源"))
                section = str(row.get("section_path", "N/A"))
                content = self._clean_snippet(str(row.get("content", "")), max_len=240)
                compressed_blocks.append(f"[目标文档{target_document_id}:{title}::{section}]\n{content}")
        
        return {
            "transfer_results": source_rows,
            "target_results": target_results,
            "source_info": {
                "document_id": source_document_id,
                "results_count": len(source_rows),
                "core_concepts": source_concepts,
            },
            "target_info": {
                "document_id": target_document_id,
                "results_count": len(target_results),
                "concept_connections": target_connections,
            } if target_document_id else None,
            "transfer_analysis": transfer_suggestions.get("analysis", ""),
            "transfer_suggestions": transfer_suggestions.get("suggestions", []),
            "compressed_context": "\n\n".join(compressed_blocks) if compressed_blocks else "暂无检索上下文",
            "cross_document_score": self._calculate_cross_document_score(source_rows, target_results),
        }
    
    def _extract_core_concepts(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        从检索结果中提取核心概念
        """
        concepts = []
        seen = set()
        
        for row in rows:
            content = str(row.get("content", ""))
            # 简单的概念提取：基于名词短语、专业术语等
            # 这里使用简化的提取逻辑，实际应用中可以使用更复杂的NLP方法
            
            # 提取可能的专业术语（大写字母开头、包含数字字母的组合）
            terms = re.findall(r'\b[A-Z][A-Za-z0-9]{2,}\b|\b[\u4e00-\u9fa5]{2,5}\b', content)
            
            for term in terms:
                if term not in seen and len(term) >= 2:
                    seen.add(term)
                    # 简单计算概念相关性（基于出现频率和位置）
                    frequency = content.count(term)
                    relevance = min(1.0, frequency * 0.1 + (row.get("dense_score", 0.0) or 0.0) * 0.5)
                    concepts.append({
                        "concept": term,
                        "frequency": frequency,
                        "relevance": round(relevance, 3),
                        "source_document_id": row.get("document_id"),
                        "source_snippet": self._clean_snippet(content, max_len=100),
                    })
        
        # 按相关性排序
        concepts.sort(key=lambda x: x["relevance"], reverse=True)
        return concepts[:10]  # 返回前10个核心概念
    
    def _generate_knowledge_transfer_suggestions(
        self,
        source_rows: List[Dict[str, Any]],
        source_concepts: List[Dict[str, Any]],
        target_connections: List[Dict[str, Any]],
        threshold: float = 0.6,
    ) -> Dict[str, Any]:
        """
        生成知识迁移建议
        """
        suggestions = []
        
        if not source_rows:
            return {
                "analysis": "源文档中没有找到相关内容",
                "suggestions": ["请尝试更具体的查询"],
            }
        
        # 分析源文档内容特征
        source_content_types = {}
        for row in source_rows:
            content_type = self._infer_content_type(str(row.get("content", "")))
            source_content_types[content_type] = source_content_types.get(content_type, 0) + 1
        
        # 如果没有目标文档连接，提供一般性建议
        if not target_connections:
            analysis = f"在源文档中找到 {len(source_rows)} 个相关结果，包含 {len(source_concepts)} 个核心概念。"
            suggestions.append("这些概念可以作为知识迁移的基础。")
            if len(source_concepts) > 0:
                suggestions.append(f"核心概念包括：{', '.join([c['concept'] for c in source_concepts[:3]])}")
        else:
            # 分析概念迁移的成功率
            high_connection_concepts = [
                conn for conn in target_connections 
                if conn.get("relevance_score", 0.0) >= threshold
            ]
            
            analysis = (
                f"发现 {len(high_connection_concepts)}/{len(target_connections)} 个概念在目标文档中有高相关性匹配 "
                f"(阈值: {threshold})。"
            )
            
            if high_connection_concepts:
                suggestions.append("以下概念具有良好的跨文档迁移潜力：")
                for conn in high_connection_concepts[:3]:
                    suggestions.append(f"- {conn['source_concept']}: 在目标文档中找到相关概念")
            else:
                suggestions.append("概念迁移的匹配度较低，可能需要人工干预或补充解释。")
        
        # 添加通用建议
        if "definition" in source_content_types and source_content_types["definition"] > 0:
            suggestions.append("源文档包含定义性内容，这些通常是知识迁移的良好起点。")
        if "example" in source_content_types and source_content_types["example"] > 0:
            suggestions.append("源文档包含示例内容，考虑将这些示例应用到新语境中。")
        
        return {
            "analysis": analysis,
            "suggestions": suggestions,
            "source_content_analysis": source_content_types,
        }
    
    def _infer_content_type(self, content: str) -> str:
        """
        推断内容类型
        """
        content_lower = content.lower()
        if any(marker in content_lower for marker in ["定义为", "是指", "称为", "定义", "definition"]):
            return "definition"
        elif any(marker in content_lower for marker in ["例如", "比如", "举例", "example", "例如"]):
            return "example"
        elif any(marker in content_lower for marker in ["应用", "使用", "用于", "application", "应用"]):
            return "application"
        elif any(marker in content_lower for marker in ["问题", "疑问", "question", "问题"]):
            return "question"
        elif any(marker in content_lower for marker in ["答案", "解答", "answer", "答案"]):
            return "answer"
        else:
            return "general"
    
    def _calculate_cross_document_score(
        self, 
        source_rows: List[Dict[str, Any]], 
        target_rows: List[Dict[str, Any]]
    ) -> float:
        """
        计算跨文档知识迁移分数
        """
        if not source_rows or not target_rows:
            return 0.0
        
        # 简单的评分逻辑：基于匹配数量和相关性
        source_scores = [row.get("dense_score", 0.0) or 0.0 for row in source_rows[:3]]
        target_scores = [row.get("dense_score", 0.0) or 0.0 for row in target_rows[:3]]
        
        avg_source = sum(source_scores) / len(source_scores) if source_scores else 0.0
        avg_target = sum(target_scores) / len(target_scores) if target_scores else 0.0
        
        # 综合评分
        score = (avg_source + avg_target) / 2
        return round(score, 3)

    def estimate_document_chunk_count(self, document_id: Optional[int], tenant_id: Optional[str] = None) -> int:
        conn = knowledge_store.connect()
        try:
            has_doc_id = isinstance(document_id, int) and document_id > 0
            if has_doc_id and tenant_id:
                row = conn.execute(
                    "SELECT COUNT(1) AS cnt FROM vectors WHERE document_id = ? AND tenant_id = ?",
                    (document_id, tenant_id),
                ).fetchone()
            elif has_doc_id:
                row = conn.execute("SELECT COUNT(1) AS cnt FROM vectors WHERE document_id = ?", (document_id,)).fetchone()
            elif tenant_id:
                row = conn.execute(
                    "SELECT COUNT(1) AS cnt FROM vectors WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()
            else:
                return 0
            if not row:
                return 0
            return max(0, int(row["cnt"] or 0))
        except Exception:
            return 0
        finally:
            conn.close()

    def _charge_query_embedding_if_needed(
        self,
        *,
        tenant_id: Optional[str],
        billing_client_id: Optional[str],
        billing_exempt: bool,
        embedding_resp: Dict[str, Any],
        reason: str,
    ) -> None:
        if billing_exempt or is_self_hosted_embedding_billing():
            return
        tid = str(tenant_id or "").strip()
        cid = str(billing_client_id or "").strip()
        if not tid or not cid:
            return
        provider = str(embedding_resp.get("provider") or "").strip().lower()
        model_id = str(embedding_resp.get("model_id") or "").strip().lower()
        billable_tokens = int(embedding_resp.get("billable_tokens") or 0)
        if provider != "zhipu" or model_id != "embedding-3" or billable_tokens <= 0:
            return
        balance = embedding_token_billing.get_token_balance(tid, cid)
        if balance < billable_tokens:
            raise HTTPException(
                status_code=429,
                detail=f"Embedding-3 token 不足，检索需 {billable_tokens}，当前余额 {balance}",
            )
        embedding_token_billing.add_tokens(tid, cid, -billable_tokens, reason)

    def load_document_chunks(
        self,
        document_id: Optional[int],
        discipline_filter: Optional[str] = None,
        limit: Optional[int] = None,
        sampling_strategy: str = "head",
        tenant_id: Optional[str] = None,
        embedding_model_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = self._fetch_rows(
            discipline_filter,
            document_id=document_id if isinstance(document_id, int) and document_id > 0 else None,
            tenant_id=tenant_id,
            embedding_model_id=embedding_model_id,
        )
        if not rows:
            return []
        
        # 强制物理排序：确保流水线按页码和块 ID 顺序处理
        rows.sort(key=self._row_order_key)
        
        # 如果是流水线全量解析任务（通常不设采样策略），直接返回全量
        if sampling_strategy == "full_ordered":
            return rows if not limit else rows[:limit]

        if isinstance(limit, int) and limit > 0:
            if str(sampling_strategy or "head").strip().lower() == "coverage":
                return self._sample_rows_for_coverage(rows, limit)
            return rows[:limit]
        return rows

    def _row_order_key(self, row: Dict[str, Any]) -> Tuple[Any, ...]:
        page_num = self._row_page_num(row)
        section_path = str(row.get("section_path", "") or "")
        chunk_id = str(row.get("chunk_id", "") or "")
        row_id = int(row.get("id", 0) or 0)
        if page_num > 0:
            return (0, page_num, self._natural_sort_key(section_path), self._natural_sort_key(chunk_id), row_id)
        return (1, self._natural_sort_key(section_path), self._natural_sort_key(chunk_id), row_id)

    @staticmethod
    def _row_page_num(row: Dict[str, Any]) -> int:
        try:
            return max(0, int(row.get("page_num", 0) or 0))
        except Exception:
            return 0

    @staticmethod
    def _natural_sort_key(value: str) -> Tuple[Any, ...]:
        text = str(value or "").strip().lower()
        if not text:
            return ((1, ""),)
        parts = re.split(r"(\d+)", text)
        out: List[Any] = []
        for part in parts:
            if not part:
                continue
            if part.isdigit():
                out.append((0, int(part)))
            else:
                out.append((1, part))
        return tuple(out)

    def _sample_rows_for_coverage(self, rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        if not rows:
            return []
        target = max(1, min(int(limit), len(rows)))
        if target >= len(rows):
            return rows
        if target <= 2:
            if target == 1:
                return [rows[0]]
            return [rows[0], rows[-1]]

        # Keep head/tail and evenly sample the middle to avoid front-only bias.
        selected_indices = {0, len(rows) - 1}
        span = len(rows) - 1
        middle_slots = max(0, target - 2)
        for i in range(1, middle_slots + 1):
            ratio = i / float(middle_slots + 1)
            idx = int(round(ratio * span))
            selected_indices.add(max(0, min(len(rows) - 1, idx)))
            if len(selected_indices) >= target:
                break
        ordered_indices = sorted(selected_indices)
        if len(ordered_indices) > target:
            ordered_indices = ordered_indices[:target]
        if len(ordered_indices) < target:
            for idx in range(len(rows)):
                if idx in selected_indices:
                    continue
                ordered_indices.append(idx)
                if len(ordered_indices) >= target:
                    break
            ordered_indices.sort()
        return [rows[idx] for idx in ordered_indices]

    @staticmethod
    def _sorted_debug_values(values: Any) -> List[str]:
        if isinstance(values, (set, list, tuple)):
            items = values
        else:
            items = [values]
        normalized: List[str] = []
        for item in items:
            normalized.append("None" if item is None else str(item))
        return sorted(normalized)

    def _fetch_rows(
        self,
        discipline_filter: Optional[str],
        document_id: Optional[int] = None,
        tenant_id: Optional[str] = None,
        embedding_model_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conn = knowledge_store.connect()
        try:
            where_tenant = " AND v.tenant_id = ? " if tenant_id else ""
            if isinstance(document_id, int) and document_id > 0 and discipline_filter and discipline_filter != "all":
                cursor = conn.execute(
                    """
                    SELECT v.id, v.document_id, v.chunk_id, v.content, v.section_path, v.embedding, v.page_num, v.chunk_type,
                           d.title, d.discipline, d.document_type, d.metadata
                    FROM vectors v
                    JOIN documents d ON v.document_id = d.id
                    WHERE v.document_id = ? AND d.discipline = ?""" + where_tenant,
                    ((document_id, discipline_filter, tenant_id) if tenant_id else (document_id, discipline_filter)),
                )
            elif isinstance(document_id, int) and document_id > 0:
                cursor = conn.execute(
                    """
                    SELECT v.id, v.document_id, v.chunk_id, v.content, v.section_path, v.embedding, v.page_num, v.chunk_type,
                           d.title, d.discipline, d.document_type, d.metadata
                    FROM vectors v
                    JOIN documents d ON v.document_id = d.id
                    WHERE v.document_id = ?""" + where_tenant,
                    ((document_id, tenant_id) if tenant_id else (document_id,)),
                )
            elif discipline_filter and discipline_filter != "all":
                cursor = conn.execute(
                    """
                    SELECT v.id, v.document_id, v.chunk_id, v.content, v.section_path, v.embedding, v.page_num, v.chunk_type,
                           d.title, d.discipline, d.document_type, d.metadata
                    FROM vectors v
                    JOIN documents d ON v.document_id = d.id
                    WHERE d.discipline = ?""" + where_tenant,
                    ((discipline_filter, tenant_id) if tenant_id else (discipline_filter,)),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT v.id, v.document_id, v.chunk_id, v.content, v.section_path, v.embedding, v.page_num, v.chunk_type,
                           d.title, d.discipline, d.document_type, d.metadata
                    FROM vectors v
                    JOIN documents d ON v.document_id = d.id
                    WHERE 1 = 1""" + where_tenant,
                    ((tenant_id,) if tenant_id else ()),
                )
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                row["content"] = strip_layout_noise(str(row.get("content", "")))
            return self._filter_rows_by_embedding_model(rows, embedding_model_id=embedding_model_id)
        finally:
            conn.close()

    def _filter_rows_by_embedding_model(
        self, rows: List[Dict[str, Any]], embedding_model_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        normalized_embedding_model_id = str(embedding_model_id or "").strip()
        if not normalized_embedding_model_id:
            return rows
        filtered: List[Dict[str, Any]] = []
        for row in rows:
            meta = FreeAIRouter.safe_json_loads(str(row.get("metadata", "")), {})
            model_tag = str(meta.get("embedding_model", "")).strip()
            if model_tag != normalized_embedding_model_id:
                continue
            filtered.append(row)
        return filtered

    def _dense_rank(self, rows: List[Dict[str, Any]], query_vec: List[float]) -> List[Dict[str, Any]]:
        ranked = []
        for row in rows:
            emb = FreeAIRouter.safe_json_loads(row["embedding"], [])
            if not emb:
                continue
            score = self._cosine(query_vec, emb)
            ranked.append({**row, "dense_score": score})
        ranked.sort(key=lambda x: x["dense_score"], reverse=True)
        return ranked

    def _sparse_rank(self, rows: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        tokens = self._tokenize(query)
        if not tokens:
            return []
        query_counts = Counter(tokens)
        doc_freq: Dict[str, int] = defaultdict(int)
        row_tokens = []
        for row in rows:
            tks = self._tokenize(row["content"])
            row_tokens.append(tks)
            for t in set(tks):
                doc_freq[t] += 1

        n_docs = len(rows)
        ranked = []
        for idx, row in enumerate(rows):
            tks = row_tokens[idx]
            if not tks:
                continue
            tf = Counter(tks)
            score = 0.0
            for t, qv in query_counts.items():
                if t not in tf:
                    continue
                idf = math.log((n_docs + 1) / (doc_freq[t] + 1)) + 1
                score += (tf[t] / max(len(tks), 1)) * idf * qv
            ranked.append({**row, "sparse_score": score})
        ranked.sort(key=lambda x: x["sparse_score"], reverse=True)
        return ranked

    def _rrf_fusion(
        self, dense_ranked: List[Dict[str, Any]], sparse_ranked: List[Dict[str, Any]], top_k: int
    ) -> List[Dict[str, Any]]:
        k = 60
        _TYPE_WEIGHT = {"knowledge": 1.0, "example": 1.0, "filler": 0.4}
        score_map: Dict[int, float] = defaultdict(float)
        row_map: Dict[int, Dict[str, Any]] = {}

        for rank, row in enumerate(dense_ranked, start=1):
            score_map[row["id"]] += 1.0 / (k + rank)
            row_map[row["id"]] = row
        for rank, row in enumerate(sparse_ranked, start=1):
            score_map[row["id"]] += 1.0 / (k + rank)
            row_map[row["id"]] = row

        fused = []
        for rid, score in score_map.items():
            row = row_map[rid]
            ct = str(row.get("chunk_type") or "knowledge")
            weight = _TYPE_WEIGHT.get(ct, 1.0)
            fused.append({**row, "rrf_score": score * weight})
        fused.sort(key=lambda x: x["rrf_score"], reverse=True)
        return fused[:top_k]

    def _rebalance_multi_document_results(self, rows: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        if top_k <= 0 or len(rows) <= top_k:
            return rows[:top_k] if top_k > 0 else []

        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        doc_order: List[str] = []
        for row in rows:
            raw_doc_id = row.get("document_id")
            doc_key = str(raw_doc_id if raw_doc_id is not None else "__unknown__")
            if doc_key not in grouped:
                doc_order.append(doc_key)
            grouped[doc_key].append(row)

        balanced: List[Dict[str, Any]] = []
        seen_ids = set()
        max_per_doc = max(2, int(math.ceil(top_k * 0.6)))

        # Pass 1: make sure the first screen covers as many documents as possible.
        for doc_key in doc_order:
            row = grouped[doc_key][0]
            row_id = int(row.get("id", 0) or 0)
            if row_id in seen_ids:
                continue
            balanced.append(row)
            seen_ids.add(row_id)
            if len(balanced) >= top_k:
                return balanced

        # Pass 2: fill remaining slots while preventing one document from dominating.
        per_doc_counts: Dict[str, int] = defaultdict(int)
        for row in balanced:
            raw_doc_id = row.get("document_id")
            doc_key = str(raw_doc_id if raw_doc_id is not None else "__unknown__")
            per_doc_counts[doc_key] += 1

        for row in rows:
            row_id = int(row.get("id", 0) or 0)
            if row_id in seen_ids:
                continue
            raw_doc_id = row.get("document_id")
            doc_key = str(raw_doc_id if raw_doc_id is not None else "__unknown__")
            if per_doc_counts[doc_key] >= max_per_doc:
                continue
            balanced.append(row)
            seen_ids.add(row_id)
            per_doc_counts[doc_key] += 1
            if len(balanced) >= top_k:
                return balanced

        if len(balanced) < top_k:
            for row in rows:
                row_id = int(row.get("id", 0) or 0)
                if row_id in seen_ids:
                    continue
                balanced.append(row)
                seen_ids.add(row_id)
                if len(balanced) >= top_k:
                    break
        return balanced

    def _cross_discipline_expand(
        self, merged: List[Dict[str, Any]], discipline_filter: Optional[str]
    ) -> List[Dict[str, Any]]:
        if not merged:
            return []
        base_discipline = discipline_filter if discipline_filter and discipline_filter != "all" else None
        items = []
        seen = set()
        for row in merged:
            d = row.get("discipline", "general")
            if base_discipline and d == base_discipline:
                continue
            key = (d, row.get("title"))
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "discipline": d,
                    "title": row.get("title"),
                    "reason": f"检索结果中存在与目标问题相关的 {d} 资料，可用于跨学科补充。",
                }
            )
        return items[:3]

    def _prioritize_for_summary(self, rows: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        if not rows:
            return []

        grouped: Dict[Tuple[int, str], List[Dict[str, Any]]] = defaultdict(list)
        scored_rows: List[Dict[str, Any]] = []
        for row in rows:
            role = self._infer_qa_role(row)
            qid = self._infer_question_id(row)
            row_copy = dict(row)
            row_copy["qa_role"] = role
            row_copy["question_id"] = qid
            scored_rows.append(row_copy)
            if role in {"question", "answer", "analysis"} and qid:
                grouped[(int(row_copy.get("document_id", 0)), qid)].append(row_copy)

        paired_keys = set()
        for key, group in grouped.items():
            roles = {r.get("qa_role") for r in group}
            if "question" in roles and ("answer" in roles or "analysis" in roles):
                paired_keys.add(key)

        for row in scored_rows:
            base = float(row.get("rrf_score", 0.0))
            role_bonus = 0.0
            if row.get("qa_role") == "answer":
                role_bonus = 0.18
            elif row.get("qa_role") == "analysis":
                role_bonus = 0.14
            elif row.get("qa_role") == "question":
                role_bonus = 0.08

            pair_bonus = 0.0
            qid = row.get("question_id")
            doc_id = int(row.get("document_id", 0))
            if qid and (doc_id, str(qid)) in paired_keys:
                pair_bonus = 0.16
            row["summary_rank_score"] = base + role_bonus + pair_bonus

        scored_rows.sort(
            key=lambda x: (float(x.get("summary_rank_score", 0.0)), float(x.get("rrf_score", 0.0))),
            reverse=True,
        )
        return scored_rows[:top_k]

    def _build_qa_focus_blocks(self, rows: List[Dict[str, Any]], max_pairs: int) -> List[str]:
        grouped: Dict[Tuple[int, str], Dict[str, Any]] = {}
        for row in rows:
            qid = str(row.get("question_id", "")).strip()
            role = str(row.get("qa_role", "other")).strip()
            if not qid or role not in {"question", "answer", "analysis"}:
                continue
            key = (int(row.get("document_id", 0)), qid)
            item = grouped.setdefault(
                key,
                {
                    "title": str(row.get("title", "未命名资料")).strip() or "未命名资料",
                    "section_path": str(row.get("section_path", "")).strip(),
                    "question": [],
                    "answer": [],
                    "analysis": [],
                    "score": 0.0,
                },
            )
            snippet = self._clean_snippet(str(row.get("content", "")), max_len=240)
            if not snippet:
                continue
            item[role].append(snippet)
            item["score"] = max(float(item.get("score", 0.0)), float(row.get("summary_rank_score", 0.0)))
            if not item["section_path"]:
                item["section_path"] = str(row.get("section_path", "")).strip()

        candidates = []
        for payload in grouped.values():
            has_question = bool(payload["question"])
            has_answer = bool(payload["answer"] or payload["analysis"])
            if has_question and has_answer:
                candidates.append(payload)
        candidates.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)

        blocks: List[str] = []
        for pair in candidates[:max_pairs]:
            stem = pair["question"][0] if pair["question"] else "未抽取到完整题干"
            answer_parts = []
            if pair["answer"]:
                answer_parts.append(pair["answer"][0])
            if pair["analysis"]:
                answer_parts.append(pair["analysis"][0])
            answer_text = " ".join(answer_parts).strip() or "未抽取到答案或解析"
            block = (
                f"[QA_FOCUS::{pair['title']}::{pair.get('section_path') or 'exam/question'}]\n"
                f"题干：{stem}\n"
                f"答案与解析：{answer_text}"
            )
            blocks.append(block)
        return blocks

    def _infer_question_id(self, row: Dict[str, Any]) -> Optional[str]:
        section_path = str(row.get("section_path", ""))
        match = re.search(r"exam/question/(\d+)", section_path)
        if match:
            return match.group(1)
        chunk_id = str(row.get("chunk_id", ""))
        chunk_match = re.match(r"(\d+)-\d+$", chunk_id)
        if chunk_match:
            return chunk_match.group(1)
        return None

    def _infer_qa_role(self, row: Dict[str, Any]) -> str:
        section_path = str(row.get("section_path", "")).lower()
        content = str(row.get("content", "")).lower()
        haystack = f"{section_path}\n{content}"
        if any(k in haystack for k in ["参考答案", "正确答案", "答案", "answer"]):
            return "answer"
        if any(k in haystack for k in ["解析", "解答", "思路", "analysis", "explain"]):
            return "analysis"
        if any(k in haystack for k in ["题干", "question", "题目"]):
            return "question"
        return "other"

    def _clean_snippet(self, text: str, max_len: int = 240) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if len(cleaned) <= max_len:
            return cleaned
        return cleaned[: max_len - 1] + "…"

    def _tokenize(self, text: str) -> List[str]:
        parts = []
        cur = []
        for ch in text.lower():
            if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"):
                cur.append(ch)
            else:
                if cur:
                    token = "".join(cur)
                    if len(token) > 1:
                        parts.append(token)
                    cur = []
        if cur:
            token = "".join(cur)
            if len(token) > 1:
                parts.append(token)
        return parts

    def _cosine(self, a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        n = min(len(a), len(b))
        dot = sum(a[i] * b[i] for i in range(n))
        na = math.sqrt(sum(a[i] * a[i] for i in range(n))) or 1e-8
        nb = math.sqrt(sum(b[i] * b[i] for i in range(n))) or 1e-8
        return dot / (na * nb)
