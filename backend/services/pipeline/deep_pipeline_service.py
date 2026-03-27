"""四库树状分块 + 双知识图谱流水线：后台任务、SQLite 召回、PostgreSQL 持久化。"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from backend.services.graphs.state import parse_json_object
from backend.services.pipeline import postgres_store as pg
from backend.runtime_config import PipelineConfig

logger = logging.getLogger(__name__)


def _run_coro_sync(coro):
    """在同步上下文（含 asyncio.to_thread 工作线程）中执行协程。"""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# 无 RuntimeConfig 时的兜底（测试/脚本）；正常运行时用 env 注入的 PipelineConfig。
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
            raise RuntimeError("PostgreSQL not configured (DATABASE_URL)")
        merged = {**self._pipeline_defaults, **(config or {})}
        job_id = str(uuid.uuid4())
        conn = pg.connect(self._database_url)
        try:
            pg.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles or [])
            pg.insert_job(conn, job_id, tenant_id, sqlite_document_id, discipline, merged)
        finally:
            conn.close()
        return job_id

    def get_job(
        self,
        job_id: str,
        tenant_id: Optional[str] = None,
        user_id: str = "system",
        roles: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        conn = pg.connect(self._database_url)
        try:
            if tenant_id:
                pg.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles or [])
            row = pg.fetch_job(conn, job_id, tenant_id=tenant_id)
            if row and row.get("config") is not None and hasattr(row["config"], "__iter__"):
                pass
            return row
        finally:
            conn.close()

    def get_presentation_bundle(
        self,
        sqlite_document_id: int,
        tenant_id: Optional[str] = None,
        user_id: str = "system",
        roles: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        conn = pg.connect(self._database_url)
        try:
            if tenant_id:
                pg.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles or [])
            tree = pg.fetch_latest_tree_for_document(conn, sqlite_document_id, tenant_id=tenant_id)
            if not tree:
                return None
            nodes = pg.fetch_tree_nodes(conn, int(tree["id"]))
            return {"tree": tree, "nodes": nodes}
        finally:
            conn.close()

    async def run_job(
        self,
        job_id: str,
        tenant_id: str = "public",
        user_id: str = "system",
        roles: Optional[List[str]] = None,
    ) -> None:
        """异步执行整条流水线（在后台 task 中调用）。"""
        await asyncio.to_thread(self._run_job_sync, job_id, tenant_id, user_id, roles or [])

    def _run_job_sync(self, job_id: str, tenant_id: str, user_id: str, roles: List[str]) -> None:
        conn = pg.connect(self._database_url)
        agent_trace: List[str] = []
        try:
            pg.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles)
            job = pg.fetch_job(conn, job_id, tenant_id=tenant_id)
            if not job:
                return
            cfg = job.get("config") or {}
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
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

            rows = self._rag.load_document_chunks(
                document_id=doc_id,
                discipline_filter=discipline,
                limit=max_chunks,
                sampling_strategy="coverage",
                tenant_id=tenant_id,
            )
            if not rows:
                pg.update_job_status(conn, job_id, "failed", "no_chunks_for_document")
                return

            tree_id = pg.insert_presentation_tree(
                conn,
                job_id,
                doc_id,
                {"pipeline": "deep_four_store", "agent_trace": agent_trace},
            )
            root_id = pg.insert_tree_node(
                conn,
                tree_id,
                None,
                "/",
                0,
                {"title": "深度理解树", "type": "root"},
                [],
                0,
                0,
            )
            pg.update_tree_root(conn, tree_id, root_id)
            agent_trace.append("tree:initialized")

            current_segment_index = 0
            current_segment_id = pg.insert_validation_segment(conn, job_id, current_segment_index)
            validations_in_segment = 0
            agent_trace.append(f"validation:segment_init:{current_segment_id}")

            batch_groups: List[List[Dict[str, Any]]] = []
            for i in range(0, len(rows), batch_size):
                batch_groups.append(rows[i : i + batch_size])

            flush_counter = 0
            strategies = ["dense", "sparse", "anchor"]

            for b_idx, batch_rows in enumerate(batch_groups):
                source_refs = [
                    {
                        "vector_id": r.get("id"),
                        "chunk_id": r.get("chunk_id"),
                        "section_path": r.get("section_path"),
                    }
                    for r in batch_rows
                ]
                bid = pg.insert_ingest_batch(
                    conn,
                    job_id,
                    b_idx,
                    b_idx * batch_size,
                    min((b_idx + 1) * batch_size - 1, len(rows) - 1),
                    source_refs,
                )
                units = [
                    {
                        "sqlite_vector_id": r.get("id"),
                        "chunk_id": str(r.get("chunk_id", "")),
                        "section_path": str(r.get("section_path", "")),
                        "content_preview": str(r.get("content", ""))[:800],
                        "meta": {"discipline": r.get("discipline"), "title": r.get("title")},
                    }
                    for r in batch_rows
                ]
                pg.insert_chunk_units(conn, bid, units)
                agent_trace.append(f"batch:{b_idx}:ingested")

                merged_points: List[str] = []
                context = "\n\n".join(
                    [f"[{r.get('section_path')}]\n{str(r.get('content', ''))[:1000]}" for r in batch_rows[:12]]
                )
                query_hint = "请从下列文档片段抽取结构化要点。"

                for g in range(1, group_count + 1):
                    strategy = strategies[g - 1] if g <= len(strategies) else f"group{g}"
                    prompt = (
                        "你是知识抽取Agent。仅返回JSON对象，字段: key_points(字符串数组), entities(字符串数组), claims(对象数组，每项含text与confidence 0-1)。\n"
                        f"策略标签: {strategy}\n"
                        f"{query_hint}\n上下文:\n{context}"
                    )
                    resp = _run_coro_sync(
                        self._ai.chat_with_task(
                            [{"role": "user", "content": prompt}],
                            task_type="extract",
                            max_tokens=600,
                            temperature=0.2,
                            prefer_free=True,
                        )
                    )
                    parsed = parse_json_object(str(resp.get("content", "")))
                    provider = str(resp.get("provider", "unknown"))
                    pg.insert_abstraction_run(conn, bid, g, strategy, parsed, provider)
                    pg.insert_evidence_span(
                        conn,
                        bid,
                        g,
                        {"strategy": strategy, "key_points": parsed.get("key_points", [])},
                    )
                    for kp in parsed.get("key_points") or []:
                        if isinstance(kp, str) and kp.strip():
                            merged_points.append(kp.strip())
                    agent_trace.append(f"batch:{b_idx}:group:{g}:abstract")

                reason_prompt = (
                    "你是推理图谱构建Agent。基于下列要点，输出JSON: "
                    '{"nodes":[{"key":"唯一键","label":"短标签","payload":{}}],'
                    '"edges":[{"source":"节点key","target":"节点key","relation":"关系类型","payload":{}}]}。\n'
                    "要点列表:\n"
                    + "\n".join(f"- {p}" for p in merged_points[:25])
                )
                reason_resp = _run_coro_sync(
                    self._ai.chat_with_task(
                        [{"role": "user", "content": reason_prompt}],
                        task_type="reason",
                        max_tokens=700,
                        temperature=0.2,
                        prefer_free=True,
                    )
                )
                r_parsed = parse_json_object(str(reason_resp.get("content", "")))
                pg.insert_reasoning_trace(conn, job_id, bid, r_parsed)
                for ni, n in enumerate(r_parsed.get("nodes") or []):
                    if not isinstance(n, dict):
                        continue
                    key = str(n.get("key", "")).strip() or f"b{b_idx}_n_{ni}"
                    pg.upsert_kg_node(
                        conn,
                        job_id,
                        "reasoning",
                        f"{b_idx}_{key}"[:200],
                        str(n.get("label", ""))[:500],
                        n.get("payload") if isinstance(n.get("payload"), dict) else {"raw": n},
                        bid,
                    )
                for e in r_parsed.get("edges") or []:
                    if not isinstance(e, dict):
                        continue
                    sk = str(e.get("source", "")).strip()
                    tk = str(e.get("target", "")).strip()
                    if not sk or not tk:
                        continue
                    pg.insert_kg_edge(
                        conn,
                        job_id,
                        "reasoning",
                        f"{b_idx}_{sk}"[:200],
                        f"{b_idx}_{tk}"[:200],
                        str(e.get("relation", "related"))[:120],
                        e.get("payload") if isinstance(e.get("payload"), dict) else {},
                    )
                agent_trace.append(f"batch:{b_idx}:reasoning_kg")

                batch_path = f"/batch/{b_idx + 1}"
                parent_batch = pg.insert_tree_node(
                    conn,
                    tree_id,
                    root_id,
                    batch_path,
                    b_idx,
                    {"title": f"批次 {b_idx + 1}", "type": "batch", "chunk_span": [bid]},
                    source_refs,
                    1,
                    b_idx,
                )
                seen_kp = set()
                unique_kp: List[str] = []
                for p in merged_points:
                    if p not in seen_kp:
                        seen_kp.add(p)
                        unique_kp.append(p)
                body = "\n".join(f"- {x}" for x in unique_kp[:50])
                pg.insert_tree_node(
                    conn,
                    tree_id,
                    parent_batch,
                    f"{batch_path}/merged",
                    0,
                    {"title": "合并要点（投影轮2）", "body": body, "type": "merged_section"},
                    source_refs,
                    2,
                    b_idx,
                )
                flush_counter += 1
                agent_trace.append(f"batch:{b_idx}:tree")

                if flush_counter % val_interval == 0:
                    self._run_validation_sync(
                        conn,
                        job_id,
                        tree_id,
                        val_interval,
                        b_idx,
                        agent_trace,
                        current_segment_id,
                    )
                    validations_in_segment += 1
                    if validations_in_segment >= segment_rotate:
                        current_segment_index += 1
                        current_segment_id = pg.insert_validation_segment(conn, job_id, current_segment_index)
                        validations_in_segment = 0
                        agent_trace.append(f"validation:segment_rotate:{current_segment_id}")

            pg.update_job_status(
                conn,
                job_id,
                "completed",
                None,
                {
                    "batches": len(batch_groups),
                    "tree_id": tree_id,
                    "agent_trace": agent_trace[-80:],
                },
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("deep pipeline job failed job_id=%s", job_id)
            try:
                pg.update_job_status(conn, job_id, "failed", str(exc)[:2000])
            except Exception:
                pass
        finally:
            conn.close()

    def delete_document_data(
        self,
        sqlite_document_id: int,
        tenant_id: Optional[str] = None,
        user_id: str = "system",
        roles: Optional[List[str]] = None,
    ) -> int:
        """删除某 SQLite 文档在 PG 中关联的全部流水线数据（依赖 CASCADE）。"""
        if not self.enabled:
            return 0
        conn = pg.connect(self._database_url)
        try:
            if tenant_id:
                pg.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles or [])
            return pg.delete_pipeline_jobs_by_document(conn, sqlite_document_id, tenant_id=tenant_id)
        finally:
            conn.close()

    def _run_validation_sync(
        self,
        conn: Any,
        job_id: str,
        tree_id: int,
        trigger_flushes: int,
        batch_idx: int,
        agent_trace: List[str],
        validation_segment_id: int,
    ) -> None:
        nodes = pg.fetch_tree_nodes(conn, tree_id)
        recent = [n for n in nodes[-40:]]
        summary = [
            {
                "id": n["id"],
                "path": n.get("path"),
                "title": (n.get("payload") or {}).get("title"),
                "body_preview": str((n.get("payload") or {}).get("body", ""))[:300],
            }
            for n in recent
        ]
        prior_kg = pg.compact_validation_subgraph_for_prompt(conn, job_id, validation_segment_id)
        val_prompt = (
            "你是知识校验Agent。检查下列展示节点是否存在冗余、重复或可合并项。"
            "下列「本段校验子图」仅含当前校验分片内已有结论，用于发现与历史校验的重复；"
            "主要依据仍是节点快照。\n"
            f"本段校验子图(紧凑):\n{prior_kg}\n"
            '仅返回JSON: {"duplicate_clusters":[[节点id...]], "merge_recommendations":[{"keep_id":1,"remove_id":2,"reason":"..."}], '
            '"contradictions":[{"ids":[],"note":""}], "notes":""}\n'
            f"节点快照:\n{json.dumps(summary, ensure_ascii=False)[:6000]}"
        )
        try:
            resp = _run_coro_sync(
                self._ai.chat_with_task(
                    [{"role": "user", "content": val_prompt}],
                    task_type="reason",
                    max_tokens=800,
                    temperature=0.2,
                    prefer_free=True,
                )
            )
            parsed = parse_json_object(str(resp.get("content", "")))
        except Exception as e:
            parsed = {"error": str(e), "notes": "validation_llm_failed"}

        pg.insert_validation_run(
            conn, job_id, tree_id, trigger_flushes, parsed, segment_id=validation_segment_id
        )
        vkey = f"validation_batch_{batch_idx}"
        pg.upsert_kg_node(
            conn,
            job_id,
            "validation",
            vkey,
            f"校验_run_{batch_idx}",
            parsed,
            None,
            segment_id=validation_segment_id,
        )
        for i, cluster in enumerate(parsed.get("duplicate_clusters") or []):
            if isinstance(cluster, list) and cluster:
                pg.insert_kg_edge(
                    conn,
                    job_id,
                    "validation",
                    vkey,
                    f"cluster_{batch_idx}_{i}",
                    "duplicate_cluster",
                    {"members": cluster},
                    segment_id=validation_segment_id,
                )
        for rec in parsed.get("merge_recommendations") or []:
            if not isinstance(rec, dict):
                continue
            keep = rec.get("keep_id")
            remove = rec.get("remove_id")
            if keep and remove:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE tree_nodes SET superseded_by = %s WHERE id = %s AND tree_id = %s",
                        (int(keep), int(remove), tree_id),
                    )
                conn.commit()
        agent_trace.append(f"validation:flush:{batch_idx}")


def run_deep_pipeline_graph_stub(agent_trace: List[str]) -> Dict[str, Any]:
    """预留：与 LangGraph 编排对齐的占位导出（实际执行在 DeepPipelineService.run_job）。"""
    return {"agent_trace": agent_trace, "graph": "deep_pipeline_inline"}
