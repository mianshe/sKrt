"""
可选 Redis Queue（RQ）异步解析：INGESTION_USE_RQ=1 且 REDIS_URL 时，创建任务后入队而非进程内 asyncio。

Worker 与 API 须共享同一 SQLite 库路径（data/knowledge.db）与 uploads 目录（多机请挂共享盘或迁移 upload_tasks 至 PostgreSQL）。

Worker 启动：
  set REDIS_URL=... & set PYTHONPATH=xm1 根目录（含 backend 包）
  python -m backend.workers.ingestion_rq_worker

租户专用队列（可选）：INGESTION_RQ_QUEUE_OVERRIDES={"tenant_id":"ingest_vip"}  JSON 映射
默认队列名：INGESTION_RQ_QUEUE（默认 ingest）
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _queue_name_for_tenant(tenant_id: str) -> str:
    default = (os.getenv("INGESTION_RQ_QUEUE") or "ingest").strip() or "ingest"
    raw = (os.getenv("INGESTION_RQ_QUEUE_OVERRIDES") or "").strip()
    if not raw:
        return default
    try:
        d = json.loads(raw)
        if isinstance(d, dict) and tenant_id in d:
            return str(d[tenant_id]).strip() or default
    except Exception:
        logger.exception("INGESTION_RQ_QUEUE_OVERRIDES JSON 无效")
    return default


def _redis_url() -> Optional[str]:
    u = (os.getenv("REDIS_URL") or "").strip()
    return u or None


def ingestion_use_rq() -> bool:
    return (os.getenv("INGESTION_USE_RQ", "").strip().lower() in {"1", "true", "yes"}) and bool(_redis_url())


def enqueue_ingestion(task_id: int, tenant_id: str) -> None:
    from redis import Redis

    from rq import Queue

    url = _redis_url()
    if not url:
        raise RuntimeError("REDIS_URL 未配置，无法使用 RQ 入队")

    queue_name = _queue_name_for_tenant(tenant_id)
    redis_conn = Redis.from_url(url)
    q = Queue(queue_name, connection=redis_conn)
    q.enqueue(
        rq_ingestion_job,
        task_id,
        tenant_id,
        job_timeout=7200,
        ttl=86400,
        result_ttl=3600,
        failure_ttl=86400,
    )
    logger.info("ingestion enqueued task_id=%s tenant=%s queue=%s", task_id, tenant_id, queue_name)


def rq_ingestion_job(task_id: int, tenant_id: str) -> Any:
    """RQ worker 入口（同步，内 asyncio.run）。"""
    import asyncio

    from backend.main import _rebuild_kg_relations, upload_ingestion_service

    async def _run() -> None:
        await upload_ingestion_service.run_task(task_id, tenant_id=tenant_id)
        await _rebuild_kg_relations(tenant_id=tenant_id)

    asyncio.run(_run())
