import json
import math
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from backend.services import embedding_token_billing
from backend.services import knowledge_store
from backend.services.billing_mode import is_self_hosted_embedding_billing

from .free_ai_router import FreeAIRouter


class RAGEngine:
    def __init__(self, ai_router: FreeAIRouter) -> None:
        self.ai_router = ai_router

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
    ) -> Dict[str, Any]:
        conn = knowledge_store.connect()
        last_embedding_resp: Dict[str, Any] = {}
        try:
            for chunk in chunks:
                embedding_resp = await self.ai_router.embed(chunk["content"], embedding_mode=embedding_mode)
                last_embedding_resp = dict(embedding_resp)
                embedding = embedding_resp["embedding"]
                conn.execute(
                    """
                    INSERT INTO vectors (document_id, chunk_id, content, section_path, embedding, tenant_id, chunk_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        chunk["chunk_id"],
                        chunk["content"],
                        chunk["section_path"],
                        json.dumps(embedding),
                        tenant_id,
                        chunk.get("chunk_type", "knowledge"),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return last_embedding_resp

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

        dense_ranked = self._dense_rank(rows, query_vec)
        sparse_ranked = self._sparse_rank(rows, query)
        merged = self._rrf_fusion(dense_ranked, sparse_ranked, top_k=top_k)
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
        compressed_blocks: List[str] = []
        for row in rows[:compress_limit]:
            title = str(row.get("title", "未命名来源"))
            section = str(row.get("section_path", "N/A"))
            content = self._clean_snippet(str(row.get("content", "")), max_len=360)
            compressed_blocks.append(f"[{title}::{section}]\n{content}")
        return {
            "results": rows,
            "cross_discipline": retrieval.get("cross_discipline", []),
            "compressed_context": "\n\n".join(compressed_blocks) if compressed_blocks else "暂无检索上下文",
        }

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
        rows.sort(key=lambda x: str(x.get("section_path", "")))
        if isinstance(limit, int) and limit > 0:
            if str(sampling_strategy or "head").strip().lower() == "coverage":
                return self._sample_rows_for_coverage(rows, limit)
            return rows[:limit]
        return rows

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
                    SELECT v.id, v.document_id, v.chunk_id, v.content, v.section_path, v.embedding, v.chunk_type,
                           d.title, d.discipline, d.document_type, d.metadata
                    FROM vectors v
                    JOIN documents d ON v.document_id = d.id
                    WHERE v.document_id = ? AND d.discipline = ?""" + where_tenant,
                    ((document_id, discipline_filter, tenant_id) if tenant_id else (document_id, discipline_filter)),
                )
            elif isinstance(document_id, int) and document_id > 0:
                cursor = conn.execute(
                    """
                    SELECT v.id, v.document_id, v.chunk_id, v.content, v.section_path, v.embedding, v.chunk_type,
                           d.title, d.discipline, d.document_type, d.metadata
                    FROM vectors v
                    JOIN documents d ON v.document_id = d.id
                    WHERE v.document_id = ?""" + where_tenant,
                    ((document_id, tenant_id) if tenant_id else (document_id,)),
                )
            elif discipline_filter and discipline_filter != "all":
                cursor = conn.execute(
                    """
                    SELECT v.id, v.document_id, v.chunk_id, v.content, v.section_path, v.embedding, v.chunk_type,
                           d.title, d.discipline, d.document_type, d.metadata
                    FROM vectors v
                    JOIN documents d ON v.document_id = d.id
                    WHERE d.discipline = ?""" + where_tenant,
                    ((discipline_filter, tenant_id) if tenant_id else (discipline_filter,)),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT v.id, v.document_id, v.chunk_id, v.content, v.section_path, v.embedding, v.chunk_type,
                           d.title, d.discipline, d.document_type, d.metadata
                    FROM vectors v
                    JOIN documents d ON v.document_id = d.id
                    WHERE 1 = 1""" + where_tenant,
                    ((tenant_id,) if tenant_id else ()),
                )
            rows = [dict(row) for row in cursor.fetchall()]
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
