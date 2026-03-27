"""PostgreSQL 访问层：四库流水线 schema 初始化与基础写入。"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).resolve().parent / "pg_schema.sql"


def connect(database_url: str):
    return psycopg2.connect(database_url)


def set_request_context(conn, tenant_id: str, user_id: str, roles: Optional[List[str]] = None) -> None:
    roles = roles or []
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        cur.execute("SELECT set_config('app.user_id', %s, false)", (user_id,))
        cur.execute("SELECT set_config('app.roles', %s, false)", (",".join(roles),))


def check_tenant_membership(conn, tenant_id: str, user_id: str) -> Dict[str, Any]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT tenant_id, user_id, roles, permissions, status
            FROM tenant_users
            WHERE tenant_id = %s AND user_id = %s
            LIMIT 1
            """,
            (tenant_id, user_id),
        )
        row = cur.fetchone()
    return dict(row) if row else {}


def insert_audit_log(
    conn,
    tenant_id: str,
    user_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    result: str,
    ip_address: str = "",
    user_agent: str = "",
    reason: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_logs (
                tenant_id, user_id, action, resource_type, resource_id,
                result, ip_address, user_agent, reason, details
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                tenant_id,
                user_id,
                action,
                resource_type,
                resource_id,
                result,
                ip_address,
                user_agent,
                reason,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )
    conn.commit()


def insert_security_event(
    conn,
    tenant_id: str,
    user_id: str,
    event_type: str,
    severity: str,
    message: str,
    ip_address: str = "",
    user_agent: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO security_events (
                tenant_id, user_id, event_type, severity, message, ip_address, user_agent, details
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                tenant_id,
                user_id,
                event_type,
                severity,
                message,
                ip_address,
                user_agent,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )
    conn.commit()


def get_tenant_quota(conn, tenant_id: str) -> Dict[str, Any]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT *
            FROM tenant_quotas
            WHERE tenant_id = %s
            LIMIT 1
            """,
            (tenant_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else {}


def _split_postgresql_statements(sql: str) -> List[str]:
    """按分号拆分 SQL，忽略单引号字符串与 $$...$$ / $tag$...$tag$ 内的分号。"""
    statements: List[str] = []
    current: List[str] = []
    i = 0
    n = len(sql)
    in_single_quote = False
    in_dollar_quote = False
    dollar_delimiter = ""

    while i < n:
        ch = sql[i]

        if in_dollar_quote:
            if dollar_delimiter and sql.startswith(dollar_delimiter, i):
                current.extend(dollar_delimiter)
                i += len(dollar_delimiter)
                in_dollar_quote = False
                dollar_delimiter = ""
                continue
            current.append(ch)
            i += 1
            continue

        if in_single_quote:
            current.append(ch)
            if ch == "'" and i + 1 < n and sql[i + 1] == "'":
                current.append(sql[i + 1])
                i += 2
                continue
            if ch == "'":
                in_single_quote = False
            i += 1
            continue

        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            current.append(ch)
            current.append(sql[i + 1])
            i += 2
            while i < n and sql[i] != "\n":
                current.append(sql[i])
                i += 1
            continue

        if ch == "$":
            m = re.match(r"\$([A-Za-z0-9_]*)\$", sql[i:])
            if m:
                delim = m.group(0)
                current.extend(delim)
                i += len(delim)
                in_dollar_quote = True
                dollar_delimiter = delim
                continue

        if ch == "'":
            in_single_quote = True
            current.append(ch)
            i += 1
            continue

        if ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def init_schema(conn) -> None:
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        for stmt in _split_postgresql_statements(sql):
            cur.execute(stmt)
    conn.commit()


def touch_job_updated(conn, job_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE pipeline_jobs SET updated_at = NOW() WHERE id = %s::uuid",
            (job_id,),
        )
    conn.commit()


def insert_job(
    conn,
    job_id: str,
    tenant_id: str,
    sqlite_document_id: int,
    discipline: str,
    config: Dict[str, Any],
    pipeline_version: int = 1,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pipeline_jobs (id, tenant_id, sqlite_document_id, discipline, status, pipeline_version, config)
            VALUES (%s::uuid, %s, %s, %s, 'queued', %s, %s::jsonb)
            """,
            (job_id, tenant_id, sqlite_document_id, discipline, pipeline_version, json.dumps(config, ensure_ascii=False)),
        )
    conn.commit()


def update_job_status(
    conn,
    job_id: str,
    status: str,
    error_message: Optional[str] = None,
    result_summary: Optional[Dict[str, Any]] = None,
) -> None:
    with conn.cursor() as cur:
        if result_summary is not None:
            cur.execute(
                """
                UPDATE pipeline_jobs
                SET status = %s, error_message = %s, result_summary = %s::jsonb, updated_at = NOW()
                WHERE id = %s::uuid
                """,
                (status, error_message, json.dumps(result_summary, ensure_ascii=False), job_id),
            )
        else:
            cur.execute(
                """
                UPDATE pipeline_jobs
                SET status = %s, error_message = %s, updated_at = NOW()
                WHERE id = %s::uuid
                """,
                (status, error_message, job_id),
            )
    conn.commit()


def fetch_job(conn, job_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if tenant_id:
            cur.execute("SELECT * FROM pipeline_jobs WHERE id = %s::uuid AND tenant_id = %s", (job_id, tenant_id))
        else:
            cur.execute("SELECT * FROM pipeline_jobs WHERE id = %s::uuid", (job_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def insert_ingest_batch(
    conn,
    job_id: str,
    batch_index: int,
    chunk_start_idx: int,
    chunk_end_idx: int,
    source_refs: List[Dict[str, Any]],
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingest_batches (job_id, batch_index, chunk_start_idx, chunk_end_idx, source_refs)
            VALUES (%s::uuid, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (job_id, batch_index, chunk_start_idx, chunk_end_idx, json.dumps(source_refs, ensure_ascii=False)),
        )
        bid = cur.fetchone()[0]
    conn.commit()
    return int(bid)


def insert_chunk_units(conn, batch_id: int, units: List[Dict[str, Any]]) -> None:
    with conn.cursor() as cur:
        for u in units:
            cur.execute(
                """
                INSERT INTO chunk_units (batch_id, sqlite_vector_id, chunk_id, section_path, content_preview, meta)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    batch_id,
                    u.get("sqlite_vector_id"),
                    u.get("chunk_id"),
                    u.get("section_path"),
                    (u.get("content_preview") or "")[:2000],
                    json.dumps(u.get("meta") or {}, ensure_ascii=False),
                ),
            )
    conn.commit()


def insert_evidence_span(conn, batch_id: int, group_id: int, span_json: Dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO evidence_spans (batch_id, group_id, span_json)
            VALUES (%s, %s, %s::jsonb)
            """,
            (batch_id, group_id, json.dumps(span_json, ensure_ascii=False)),
        )
    conn.commit()


def insert_abstraction_run(
    conn,
    batch_id: int,
    group_id: int,
    strategy: str,
    abstraction_json: Dict[str, Any],
    provider: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO abstraction_runs (batch_id, group_id, strategy, abstraction_json, provider)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            """,
            (batch_id, group_id, strategy, json.dumps(abstraction_json, ensure_ascii=False), provider),
        )
    conn.commit()


def upsert_kg_node(
    conn,
    job_id: str,
    graph_role: str,
    external_key: str,
    label: Optional[str],
    payload: Dict[str, Any],
    batch_id: Optional[int],
    segment_id: Optional[int] = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kg_nodes (job_id, graph_role, external_key, label, payload, batch_id, segment_id)
            VALUES (%s::uuid, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (job_id, graph_role, external_key, segment_discrim) DO UPDATE
            SET label = EXCLUDED.label, payload = EXCLUDED.payload, batch_id = EXCLUDED.batch_id,
                segment_id = EXCLUDED.segment_id
            """,
            (
                job_id,
                graph_role,
                external_key,
                label,
                json.dumps(payload, ensure_ascii=False),
                batch_id,
                segment_id,
            ),
        )
    conn.commit()


def insert_kg_edge(
    conn,
    job_id: str,
    graph_role: str,
    source_key: str,
    target_key: str,
    relation_type: str,
    payload: Dict[str, Any],
    version: int = 1,
    segment_id: Optional[int] = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kg_edges (job_id, graph_role, source_key, target_key, relation_type, payload, version, segment_id)
            VALUES (%s::uuid, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                job_id,
                graph_role,
                source_key,
                target_key,
                relation_type,
                json.dumps(payload, ensure_ascii=False),
                version,
                segment_id,
            ),
        )
    conn.commit()


def insert_reasoning_trace(conn, job_id: str, batch_id: Optional[int], trace_json: Dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO reasoning_traces (job_id, batch_id, trace_json)
            VALUES (%s::uuid, %s, %s::jsonb)
            """,
            (job_id, batch_id, json.dumps(trace_json, ensure_ascii=False)),
        )
    conn.commit()


def insert_presentation_tree(conn, job_id: str, sqlite_document_id: int, meta: Dict[str, Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO presentation_trees (job_id, sqlite_document_id, meta)
            VALUES (%s::uuid, %s, %s::jsonb)
            RETURNING id
            """,
            (job_id, sqlite_document_id, json.dumps(meta, ensure_ascii=False)),
        )
        tid = cur.fetchone()[0]
    conn.commit()
    return int(tid)


def update_tree_root(conn, tree_id: int, root_node_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE presentation_trees SET root_node_id = %s WHERE id = %s",
            (root_node_id, tree_id),
        )
    conn.commit()


def insert_tree_node(
    conn,
    tree_id: int,
    parent_id: Optional[int],
    path: str,
    sort_order: int,
    payload: Dict[str, Any],
    source_span_refs: List[Dict[str, Any]],
    projection_round: int,
    flush_batch_index: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tree_nodes (tree_id, parent_id, path, sort_order, payload, source_span_refs, projection_round, flush_batch_index)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
            RETURNING id
            """,
            (
                tree_id,
                parent_id,
                path,
                sort_order,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(source_span_refs, ensure_ascii=False),
                projection_round,
                flush_batch_index,
            ),
        )
        nid = cur.fetchone()[0]
    conn.commit()
    return int(nid)


def insert_validation_run(
    conn,
    job_id: str,
    tree_id: int,
    trigger_after_flushes: int,
    result_json: Dict[str, Any],
    segment_id: Optional[int] = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO validation_runs (job_id, tree_id, segment_id, trigger_after_flushes, result_json)
            VALUES (%s::uuid, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (
                job_id,
                tree_id,
                segment_id,
                trigger_after_flushes,
                json.dumps(result_json, ensure_ascii=False),
            ),
        )
        rid = cur.fetchone()[0]
    conn.commit()
    return int(rid)


def insert_validation_segment(conn, job_id: str, segment_index: int) -> int:
    """创建校验子图分片；同一 (job_id, segment_index) 已存在则返回已有 id。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO validation_segments (job_id, segment_index)
            VALUES (%s::uuid, %s)
            ON CONFLICT (job_id, segment_index) DO UPDATE SET created_at = validation_segments.created_at
            RETURNING id
            """,
            (job_id, segment_index),
        )
        sid = cur.fetchone()[0]
    conn.commit()
    return int(sid)


def delete_pipeline_jobs_by_document(conn, sqlite_document_id: int, tenant_id: Optional[str] = None) -> int:
    """按 SQLite 文档 id 删除流水线任务；子表依赖 ON DELETE CASCADE 一并清理。"""
    with conn.cursor() as cur:
        if tenant_id:
            cur.execute(
                "DELETE FROM pipeline_jobs WHERE sqlite_document_id = %s AND tenant_id = %s",
                (sqlite_document_id, tenant_id),
            )
        else:
            cur.execute(
                "DELETE FROM pipeline_jobs WHERE sqlite_document_id = %s",
                (sqlite_document_id,),
            )
        n = cur.rowcount or 0
    conn.commit()
    return int(n)


def fetch_latest_tree_for_document(conn, sqlite_document_id: int, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if tenant_id:
            cur.execute(
                """
                SELECT pt.* FROM presentation_trees pt
                JOIN pipeline_jobs pj ON pj.id = pt.job_id
                WHERE pt.sqlite_document_id = %s AND pj.status = 'completed' AND pj.tenant_id = %s
                ORDER BY pt.created_at DESC
                LIMIT 1
                """,
                (sqlite_document_id, tenant_id),
            )
        else:
            cur.execute(
                """
                SELECT pt.* FROM presentation_trees pt
                JOIN pipeline_jobs pj ON pj.id = pt.job_id
                WHERE pt.sqlite_document_id = %s AND pj.status = 'completed'
                ORDER BY pt.created_at DESC
                LIMIT 1
                """,
                (sqlite_document_id,),
            )
        row = cur.fetchone()
    return dict(row) if row else None


def insert_chat_turn(
    conn,
    tenant_id: str,
    user_id: str,
    session_id: str,
    question: str,
    answer: str,
    source_json: Optional[List[Dict[str, Any]]] = None,
    expire_seconds: int = 0,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chat_work_memory (tenant_id, user_id, session_id, question, answer, source_json, expire_at)
            VALUES (
                %s, %s, %s, %s, %s, %s::jsonb,
                CASE WHEN %s > 0 THEN NOW() + (%s || ' seconds')::interval ELSE NULL END
            )
            """,
            (
                tenant_id,
                user_id,
                session_id,
                question,
                answer,
                json.dumps(source_json or [], ensure_ascii=False),
                int(expire_seconds),
                int(expire_seconds),
            ),
        )
    conn.commit()


def list_recent_chat_turns(
    conn, tenant_id: str, session_id: str, user_id: Optional[str] = None, limit: int = 6
) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if user_id:
            cur.execute(
                """
                SELECT * FROM chat_work_memory
                WHERE tenant_id = %s AND session_id = %s AND user_id = %s
                  AND (expire_at IS NULL OR expire_at > NOW())
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (tenant_id, session_id, user_id, max(1, int(limit))),
            )
        else:
            cur.execute(
                """
                SELECT * FROM chat_work_memory
                WHERE tenant_id = %s AND session_id = %s
                  AND (expire_at IS NULL OR expire_at > NOW())
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (tenant_id, session_id, max(1, int(limit))),
            )
        rows = [dict(r) for r in cur.fetchall()]
    rows.reverse()
    return rows


def clear_chat_turns(conn, tenant_id: str, session_id: str, user_id: Optional[str] = None) -> int:
    with conn.cursor() as cur:
        if user_id:
            cur.execute(
                "DELETE FROM chat_work_memory WHERE tenant_id = %s AND session_id = %s AND user_id = %s",
                (tenant_id, session_id, user_id),
            )
        else:
            cur.execute(
                "DELETE FROM chat_work_memory WHERE tenant_id = %s AND session_id = %s",
                (tenant_id, session_id),
            )
        count = cur.rowcount or 0
    conn.commit()
    return int(count)


def fetch_tree_nodes(conn, tree_id: int) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM tree_nodes WHERE tree_id = %s ORDER BY path, sort_order, id",
            (tree_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def list_validation_nodes(conn, job_id: str, segment_id: Optional[int] = None) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if segment_id is None:
            cur.execute(
                "SELECT * FROM kg_nodes WHERE job_id = %s::uuid AND graph_role = 'validation' ORDER BY id",
                (job_id,),
            )
        else:
            cur.execute(
                """
                SELECT * FROM kg_nodes
                WHERE job_id = %s::uuid AND graph_role = 'validation' AND segment_id = %s
                ORDER BY id
                """,
                (job_id, segment_id),
            )
        return [dict(r) for r in cur.fetchall()]


def list_validation_edges(conn, job_id: str, segment_id: int) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT * FROM kg_edges
            WHERE job_id = %s::uuid AND graph_role = 'validation' AND segment_id = %s
            ORDER BY id
            """,
            (job_id, segment_id),
        )
        return [dict(r) for r in cur.fetchall()]


def compact_validation_subgraph_for_prompt(conn, job_id: str, segment_id: int, max_chars: int = 2800) -> str:
    """当前 segment 的校验子图摘要，供下一轮校验 CoT 引用（避免跨段无限膨胀）。"""
    nodes = list_validation_nodes(conn, job_id, segment_id)
    edges = list_validation_edges(conn, job_id, segment_id)
    snap: Dict[str, Any] = {
        "segment_id": segment_id,
        "nodes": [
            {
                "key": n.get("external_key"),
                "label": n.get("label"),
                "notes": (n.get("payload") or {}).get("notes") if isinstance(n.get("payload"), dict) else None,
            }
            for n in nodes[-20:]
        ],
        "edges": [
            {
                "s": e.get("source_key"),
                "t": e.get("target_key"),
                "rel": e.get("relation_type"),
            }
            for e in edges[-30:]
        ],
    }
    raw = json.dumps(snap, ensure_ascii=False)
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + "…"
