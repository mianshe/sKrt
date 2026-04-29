"""四库树状分块 + 双知识图谱流水线：逻辑结构发现与自适应解析。"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
import re
from typing import Any, Dict, List, Optional

from ..graphs.state import parse_json_object
from .postgres_store import postgres_store as pg
from runtime_config import PipelineConfig

logger = logging.getLogger(__name__)


def _run_coro_sync(coro):
    """在同步上下文（含 asyncio.to_thread 工作线程）中执行协程。"""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# 无 RuntimeConfig 时的兜底
_FALLBACK_PIPELINE: Dict[str, Any] = PipelineConfig(
    batch_chunk_size=30,
    group_count=3,
    max_chunks=500,
    validation_flush_interval=3,
    validation_segment_rotate=3,
).as_dict()


class DeepPipelineService:
    def __init__(
        self,
        database_url: str,
        rag_engine: Any,
        ai_router: Any,
        pipeline_defaults: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._database_url = database_url
        self._rag = rag_engine
        self._ai = ai_router
        self._pipeline_defaults: Dict[str, Any] = dict(pipeline_defaults or _FALLBACK_PIPELINE)

    @property
    def enabled(self) -> bool:
        return bool(self._database_url)

    def init_schema(self) -> None:
        if not self.enabled:
            return
        conn = pg.connect(self._database_url)
        try:
            pg.init_schema(conn)
        finally:
            conn.close()

    def create_job(
        self,
        sqlite_document_id: int,
        discipline: str = "all",
        config: Optional[Dict[str, Any]] = None,
        tenant_id: str = "public",
        user_id: str = "system",
        roles: Optional[List[str]] = None,
    ) -> str:
        if not self.enabled:
            raise RuntimeError("PostgreSQL not configured")
        merged = {**self._pipeline_defaults, **(config or {})}
        job_id = str(uuid.uuid4())
        conn = pg.connect(self._database_url)
        try:
            pg.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles or [])
            pg.insert_job(conn, job_id, tenant_id, sqlite_document_id, discipline, merged)
        finally:
            conn.close()
        return job_id

    def get_job(self, job_id: str, tenant_id: Optional[str] = None, user_id: str = "system", roles: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        if not self.enabled: return None
        conn = pg.connect(self._database_url)
        try:
            if tenant_id: pg.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles or [])
            return pg.fetch_job(conn, job_id, tenant_id=tenant_id)
        finally: conn.close()

    def get_presentation_bundle(self, sqlite_document_id: int, tenant_id: Optional[str] = None, user_id: str = "system", roles: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        if not self.enabled: return None
        conn = pg.connect(self._database_url)
        try:
            if tenant_id: pg.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles or [])
            tree = pg.fetch_latest_tree_for_document(conn, sqlite_document_id, tenant_id=tenant_id)
            if not tree: return None
            nodes = pg.fetch_tree_nodes(conn, int(tree["id"]))
            return {"tree": tree, "nodes": nodes}
        finally: conn.close()

    async def run_job(self, job_id: str, tenant_id: str = "public", user_id: str = "system", roles: Optional[List[str]] = None) -> None:
        await asyncio.to_thread(self._run_job_sync, job_id, tenant_id, user_id, roles or [])

    def _run_job_sync(self, job_id: str, tenant_id: str, user_id: str, roles: List[str]) -> None:
        conn = pg.connect(self._database_url)
        agent_trace: List[str] = []
        try:
            pg.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles)
            job = pg.fetch_job(conn, job_id, tenant_id=tenant_id)
            if not job: return
            
            cfg = job.get("config") or {}
            if isinstance(cfg, str): cfg = json.loads(cfg)
            pd = self._pipeline_defaults
            batch_size = max(1, int(cfg.get("batch_chunk_size", pd["batch_chunk_size"])))
            group_count = max(1, min(3, int(cfg.get("group_count", pd["group_count"]))))
            max_chunks = max(1, int(cfg.get("max_chunks", pd["max_chunks"])))
            val_interval = max(1, int(cfg.get("validation_flush_interval", pd["validation_flush_interval"])))
            segment_rotate = max(1, int(cfg.get("validation_segment_rotate", pd["validation_segment_rotate"])))

            doc_id = int(job["sqlite_document_id"])
            discipline = str(job.get("discipline") or "all")

            pg.update_job_status(conn, job_id, "running", None)
            agent_trace.append("job:running")

            # 严格物理排序加载所有块
            all_rows = self._rag.load_document_chunks(
                document_id=doc_id, discipline_filter=discipline, limit=max_chunks,
                sampling_strategy="full_ordered", tenant_id=tenant_id,
            )
            if not all_rows:
                pg.update_job_status(conn, job_id, "failed", "no_chunks_for_document")
                return

            # Step 0: Discovery 阶段 - 解析逻辑骨架
            outline = self._run_discovery_sync(all_rows, agent_trace)
            
            tree_id = pg.insert_presentation_tree(
                conn, job_id, doc_id,
                {"pipeline": "discovery_v2", "agent_trace": agent_trace, "outline": outline},
            )
            root_id = pg.insert_tree_node(
                conn, tree_id, None, "/", 0,
                {"title": "深度解析地图", "type": "root", "summary": outline.get("summary")},
                [], 0, 0,
            )
            pg.update_tree_root(conn, tree_id, root_id)

            current_segment_index = 0
            current_segment_id = pg.insert_validation_segment(conn, job_id, current_segment_index)
            validations_in_segment = 0

            # Step 1: Adaptive Batching - 基于大纲分批
            batch_groups = self._build_adaptive_batches(all_rows, outline, batch_size)

            flush_counter = 0
            strategies = ["dense", "sparse", "anchor"]
            last_batch_summary = ""

            for b_idx, batch_rows in enumerate(batch_groups):
                # Step 2: Navigation - 获取当前章节信息
                nav_hint = self._get_navigation_hint(batch_rows, outline)
                
                bid = pg.insert_ingest_batch(conn, job_id, b_idx, 0, 0, []) # 简化记录
                pg.insert_chunk_units(conn, bid, [
                    {"sqlite_vector_id": r.get("id"), "chunk_id": str(r.get("chunk_id")), "content_preview": str(r.get("content"))[:200]}
                    for r in batch_rows
                ])

                context = "\n\n".join([f"### [Page {r.get('page_num')}] {r.get('content')}" for r in batch_rows])
                rolling_context = f"前文解析摘要：{last_batch_summary}\n" if last_batch_summary else ""
                
                query_hint = (
                    f"【当前逻辑位置：{nav_hint}】\n"
                    f"{rolling_context}请深度解析下列片段。\n"
                    "指令：\n"
                    "1. 拒绝平庸：不要重复前文已有的背景。如果是目录、页眉或摘要的重复，请略过。\n"
                    "2. 深度下钻：挖掘正文中的实验数据、逻辑因果、关键参数或核心结论。\n"
                    "3. 保持连贯：如果该片段是前文某个论点的延续，请建立关联。"
                )

                merged_points: List[str] = []
                for g in range(1, group_count + 1):
                    strategy = strategies[g - 1] if g <= len(strategies) else f"group{g}"
                    prompt = f"你是专业资料深度解析Agent。仅返回JSON，字段: key_points(数组), entities(数组)。\n{query_hint}\n上下文：\n{context}"
                    resp = _run_coro_sync(self._ai.chat_with_task([{"role": "user", "content": prompt}], task_type="extract", max_tokens=1000, prefer_free=True))
                    parsed = parse_json_object(str(resp.get("content", "")))
                    for kp in parsed.get("key_points") or []:
                        if isinstance(kp, str) and kp.strip():
                            merged_points.append(kp.strip())
                            pg.insert_fact_staging(conn, job_id, bid, kp.strip(), section_path=nav_hint)

                # 图谱构建：全局去重
                reason_prompt = f"构建 [{nav_hint}] 的因果图谱。合并相似节点。\nJSON字段: nodes, edges。\n要点：\n" + "\n".join(merged_points[:30])
                reason_resp = _run_coro_sync(self._ai.chat_with_task([{"role": "user", "content": reason_prompt}], task_type="reason", max_tokens=800, prefer_free=True))
                r_parsed = parse_json_object(str(reason_resp.get("content", "")))
                for ni, n in enumerate(r_parsed.get("nodes") or []):
                    if isinstance(n, dict):
                        key = str(n.get("key", "")).strip() or f"n_{b_idx}_{ni}"
                        pg.upsert_kg_node(conn, job_id, "reasoning", key[:200], str(n.get("label", ""))[:500], n, bid)
                for e in r_parsed.get("edges") or []:
                    if isinstance(e, dict):
                        sk, tk = str(e.get("source", "")), str(e.get("target", ""))
                        if sk and tk: pg.insert_kg_edge(conn, job_id, "reasoning", sk[:200], tk[:200], str(e.get("relation", "related")), e)

                last_batch_summary = "\n".join(merged_points[:10])[:500]
                
                # 挂载到对应的章节目录下
                chapter_slug = self._slug(nav_hint)
                chapter_node = pg.insert_tree_node(conn, tree_id, root_id, f"/chapter/{chapter_slug}", 0, {"title": nav_hint, "type": "chapter"}, [], 2, b_idx)
                pg.insert_tree_node(
                    conn, tree_id, chapter_node, f"/chapter/{chapter_slug}/detail_{b_idx}", 0,
                    {"title": f"深度解析(Batch {b_idx+1})", "body": "\n".join(f"- {x}" for x in merged_points[:50]), "type": "detail"},
                    [], 2, b_idx
                )
                
                flush_counter += 1
                if flush_counter % val_interval == 0:
                    self._run_validation_sync(conn, job_id, tree_id, val_interval, b_idx, agent_trace, current_segment_id)

            # Step 3: Synthesis 阶段
            self._run_synthesis_sync(conn, job_id, tree_id, root_id, agent_trace)

            pg.update_job_status(conn, job_id, "completed", None, {"batches": len(batch_groups), "tree_id": tree_id})
        except Exception as exc:
            logger.exception("job failed: %s", job_id)
            try: pg.update_job_status(conn, job_id, "failed", str(exc))
            except Exception: pass
        finally: conn.close()

    def _run_discovery_sync(self, rows: List[Dict[str, Any]], agent_trace: List[str]) -> Dict[str, Any]:
        """Discovery: 快速扫描文档，识别章节边界。"""
        samples = []
        seen_pages = set()
        for r in rows:
            p = r.get("page_num", 0)
            if p not in seen_pages:
                samples.append(f"[Page {p}] {str(r.get('content'))[:150]}")
                seen_pages.add(p)
        
        prompt = (
            "你是文档结构发现Agent。基于以下抽样片段，请还原该文档的逻辑坐标系。\n"
            "即使文档没有编号，也请推测章节名。返回 JSON：\n"
            '{"summary": "文档主旨", "chapters": [{"title": "章节名", "start_page": 1, "end_page": 5}]}\n'
            "采样数据：\n" + "\n".join(samples[:120])
        )
        try:
            resp = _run_coro_sync(self._ai.chat_with_task([{"role": "user", "content": prompt}], task_type="reason", max_tokens=1500, prefer_free=True))
            return parse_json_object(str(resp.get("content", "")))
        except Exception: return {"summary": "Unknown", "chapters": []}

    def _build_adaptive_batches(self, rows: List[Dict[str, Any]], outline: Dict[str, Any], fixed_size: int) -> List[List[Dict[str, Any]]]:
        chapters = outline.get("chapters", [])
        if not chapters: return [rows[i : i + fixed_size] for i in range(0, len(rows), fixed_size)]
        batches = []
        for ch in chapters:
            s, e = ch.get("start_page", 0), ch.get("end_page", 999)
            ch_rows = [r for r in rows if s <= r.get("page_num", 0) <= e]
            if not ch_rows: continue
            for i in range(0, len(ch_rows), fixed_size): batches.append(ch_rows[i : i + fixed_size])
        return batches

    def _get_navigation_hint(self, batch_rows: List[Dict[str, Any]], outline: Dict[str, Any]) -> str:
        p = batch_rows[0].get("page_num", 0)
        for ch in outline.get("chapters", []):
            if ch.get("start_page", 0) <= p <= ch.get("end_page", 999): return str(ch.get("title"))
        return f"Page {p}"

    def _slug(self, value: str) -> str:
        v = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", str(value).lower())
        return v.strip("-")[:40] or "unknown"

    def _run_synthesis_sync(self, conn: Any, job_id: str, tree_id: int, root_id: int, agent_trace: List[str]) -> None:
        facts = pg.fetch_facts_for_job(conn, job_id)
        if not facts: return
        clusters: Dict[str, List[str]] = {}
        for f in facts: clusters.setdefault(str(f.get("section_path")), []).append(str(f.get("fact_text")))
        
        for section, fact_list in clusters.items():
            if len(fact_list) < 3: continue
            prompt = f"总结章节 [{section}] 的全量要点，生成深度技术报告（不少于 500 字）。\n事实：\n" + "\n".join(fact_list[:50])
            try:
                resp = _run_coro_sync(self._ai.chat_with_task([{"role": "user", "content": prompt}], task_type="reason", max_tokens=1200, prefer_free=True))
                pg.insert_tree_node(conn, tree_id, root_id, f"/synthesis/{self._slug(section)}", 0, {"title": f"【全局复盘】{section}", "body": str(resp.get("content")), "type": "synthesis"}, [], 3, 999)
            except Exception: pass

    def delete_document_data(self, sqlite_document_id: int, tenant_id: Optional[str] = None, user_id: str = "system", roles: Optional[List[str]] = None) -> int:
        if not self.enabled: return 0
        conn = pg.connect(self._database_url)
        try:
            if tenant_id: pg.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles or [])
            return pg.delete_pipeline_jobs_by_document(conn, sqlite_document_id, tenant_id=tenant_id)
        finally: conn.close()

    def _run_validation_sync(self, conn: Any, job_id: str, tree_id: int, trigger_flushes: int, batch_idx: int, agent_trace: List[str], validation_segment_id: int) -> None:
        nodes = pg.fetch_tree_nodes(conn, tree_id)
        summary = [{"path": n.get("path"), "title": (n.get("payload") or {}).get("title"), "body": str((n.get("payload") or {}).get("body", ""))[:200]} for n in nodes[-30:]]
        val_prompt = f"你是校验Agent。检查下列节点是否有严重冲突或完全重复的内容。\n节点快照：\n{json.dumps(summary, ensure_ascii=False)}"
        try:
            resp = _run_coro_sync(self._ai.chat_with_task([{"role": "user", "content": val_prompt}], task_type="reason", max_tokens=600, prefer_free=True))
            parsed = parse_json_object(str(resp.get("content", "")))
            pg.insert_validation_run(conn, job_id, tree_id, trigger_flushes, parsed, segment_id=validation_segment_id)
        except Exception: pass
