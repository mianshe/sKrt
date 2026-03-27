"""
上传解析抗压：限流、队列指标、可选 Redis 协同（多进程部署时共享计数）。

环境变量（0 表示不限制该项）：
- UPLOAD_GLOBAL_QUEUE_BACKPRESS：全站 queued+running 超过则拒绝新建任务（429）
- UPLOAD_MAX_CONCURRENT_TASKS_PER_TENANT：单租户 queued+running 上限
- UPLOAD_MAX_CREATES_PER_MINUTE：单租户+单 client 每分钟创建任务数上限
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_redis_client: Any = None  # lazy singleton: None=unset, False=disabled


def get_redis_client():
    global _redis_client
    if _redis_client is False:
        return None
    if _redis_client is not None:
        return _redis_client
    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        _redis_client = False
        return None
    try:
        import redis  # type: ignore

        _redis_client = redis.from_url(url, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception:
        logger.exception("REDIS_URL 已配置但连接失败，回退 SQLite 限流")
        _redis_client = False
        return None


def _env_int(name: str, default: int, min_value: int = 0) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        v = int(raw)
    except ValueError:
        v = default
    return max(min_value, v)


def upload_global_queue_max() -> int:
    return _env_int("UPLOAD_GLOBAL_QUEUE_BACKPRESS", 5000, 0)


def upload_max_concurrent_per_tenant() -> int:
    return _env_int("UPLOAD_MAX_CONCURRENT_TASKS_PER_TENANT", 300, 0)


def upload_max_creates_per_minute() -> int:
    return _env_int("UPLOAD_MAX_CREATES_PER_MINUTE", 600, 0)


def _minute_key_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M")


def count_global_queued_running(conn: Any) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM upload_tasks WHERE status IN ('queued', 'running')"
    ).fetchone()
    return int(row["c"]) if row else 0


def count_tenant_queued_running(conn: Any, tenant_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM upload_tasks WHERE tenant_id=? AND status IN ('queued', 'running')",
        (tenant_id,),
    ).fetchone()
    return int(row["c"]) if row else 0


def _get_minute_count_sqlite(conn: Any, tenant_id: str, client_id: str, minute_key: str) -> int:
    row = conn.execute(
        "SELECT created_count FROM upload_throttle_minute WHERE tenant_id=? AND client_id=? AND minute_key=?",
        (tenant_id, client_id, minute_key),
    ).fetchone()
    return int(row["created_count"]) if row else 0


def _get_minute_count_redis(tenant_id: str, client_id: str, minute_key: str) -> Optional[int]:
    r = get_redis_client()
    if not r:
        return None
    key = f"upload:throttle:{tenant_id}:{client_id}:{minute_key}"
    try:
        v = r.get(key)
        return int(v) if v is not None else 0
    except Exception:
        logger.exception("redis get throttle failed")
        return None


def _incr_minute_count_sqlite(conn: Any, tenant_id: str, client_id: str, minute_key: str, n: int) -> None:
    conn.execute(
        """
        INSERT INTO upload_throttle_minute(tenant_id, client_id, minute_key, created_count)
        VALUES(?,?,?,?)
        ON CONFLICT(tenant_id, client_id, minute_key)
        DO UPDATE SET created_count = created_count + excluded.created_count
        """,
        (tenant_id, client_id, minute_key, n),
    )


def _incr_minute_count_redis(tenant_id: str, client_id: str, minute_key: str, n: int) -> None:
    r = get_redis_client()
    if not r:
        return
    key = f"upload:throttle:{tenant_id}:{client_id}:{minute_key}"
    try:
        pipe = r.pipeline()
        pipe.incrby(key, n)
        pipe.expire(key, 120)
        pipe.execute()
    except Exception:
        logger.exception("redis incr throttle failed")


def enforce_upload_create_allowed(
    conn: Any,
    tenant_id: str,
    client_id: str,
    new_tasks: int,
    *,
    in_memory_workers: int = 0,
) -> None:
    """在创建任务前调用；失败抛出 HTTP 429。"""
    gmax = upload_global_queue_max()
    if gmax > 0:
        gq = count_global_queued_running(conn)
        if gq + new_tasks > gmax:
            raise HTTPException(
                status_code=429,
                detail=f"全站解析队列已满（queued+running={gq}），请稍后重试",
            )

    tmax = upload_max_concurrent_per_tenant()
    if tmax > 0:
        tq = count_tenant_queued_running(conn, tenant_id)
        if tq + new_tasks > tmax:
            raise HTTPException(
                status_code=429,
                detail=f"当前租户解析任务过多（{tq}），请稍后重试",
            )

    mmax = upload_max_creates_per_minute()
    if mmax > 0:
        mk = _minute_key_utc()
        cur_redis = _get_minute_count_redis(tenant_id, client_id, mk)
        cur = cur_redis if cur_redis is not None else _get_minute_count_sqlite(conn, tenant_id, client_id, mk)
        if cur + new_tasks > mmax:
            raise HTTPException(
                status_code=429,
                detail="本分钟创建任务次数过多，请稍后再试",
            )

    # in-memory worker 仅作观测提示，不作为硬拒绝（多进程时不可靠）
    _ = in_memory_workers


def record_upload_tasks_created(
    conn: Any,
    tenant_id: str,
    client_id: str,
    n: int,
) -> None:
    """成功创建任务后调用，计入每分钟计数。"""
    if n <= 0:
        return
    mk = _minute_key_utc()
    _incr_minute_count_redis(tenant_id, client_id, mk, n)
    if get_redis_client():
        return
    _incr_minute_count_sqlite(conn, tenant_id, client_id, mk, n)
    conn.commit()


def get_upload_queue_metrics(
    conn: Any,
    *,
    ingest_workers_in_memory: int,
    estimated_avg_task_sec: Optional[float] = None,
) -> Dict[str, Any]:
    gq = count_global_queued_running(conn)
    row = conn.execute(
        """
        SELECT status, COUNT(*) AS c FROM upload_tasks
        GROUP BY status
        """
    ).fetchall()
    by_status = {str(r["status"]): int(r["c"]) for r in row}
    gmax = upload_global_queue_max()
    tmax = upload_max_concurrent_per_tenant()
    queued_only = int(by_status.get("queued", 0))
    est_wait: Optional[float] = None
    if estimated_avg_task_sec is not None and estimated_avg_task_sec > 0 and queued_only > 0:
        est_wait = round(queued_only * float(estimated_avg_task_sec), 1)
    return {
        "global_queued_running": gq,
        "by_status": by_status,
        "queued_only": queued_only,
        "failed_total": int(by_status.get("failed", 0)),
        "ingest_workers_in_memory": int(ingest_workers_in_memory),
        "limits": {
            "global_queue_backpress": gmax,
            "max_concurrent_per_tenant": tmax,
            "max_creates_per_minute": upload_max_creates_per_minute(),
        },
        "backpressure": {
            "global_queue_utilization": (gq / gmax) if gmax > 0 else None,
            "tenant_concurrency_limit": tmax,
            "estimated_queue_wait_sec": est_wait,
        },
        "redis_throttle": bool(get_redis_client()),
    }


def log_ingestion_event(event: str, **fields: Any) -> None:
    parts = [f"{k}={fields[k]}" for k in sorted(fields.keys()) if fields[k] is not None]
    logger.info("upload_ingestion %s %s", event, " ".join(parts))
