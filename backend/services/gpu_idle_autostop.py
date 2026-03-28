"""
After GPU-related ingestion reaches a terminal state, debounce then stop cloud GPU
instances if no upload_tasks remain with use_gpu_ocr=1 in a non-terminal status.

Requires GPU_AUTOSTART_ENABLED=1 and GPU_AUTOSTOP_IDLE_SECONDS (default 120, min 30).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from backend.services import knowledge_store
from backend.services.gpu_autostart_cloud import gpu_autostart_enabled, stop_gpu_instances

logger = logging.getLogger(__name__)

_pending_stop_task: Optional[asyncio.Task[None]] = None


def _idle_seconds() -> float:
    raw = (os.getenv("GPU_AUTOSTOP_IDLE_SECONDS") or "120").strip() or "120"
    try:
        v = int(raw, 10)
    except ValueError:
        v = 120
    return float(max(30, v))


def _task_row_use_gpu_ocr(task_id: int) -> bool:
    conn = knowledge_store.connect()
    try:
        row = conn.execute(
            "SELECT use_gpu_ocr FROM upload_tasks WHERE id = ?",
            (int(task_id),),
        ).fetchone()
        if not row:
            return False
        return int(row["use_gpu_ocr"] or 0) == 1
    finally:
        conn.close()


def _count_pending_gpu_ocr_tasks() -> int:
    conn = knowledge_store.connect()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM upload_tasks
            WHERE COALESCE(use_gpu_ocr, 0) = 1
              AND status NOT IN ('completed', 'failed')
            """
        ).fetchone()
        if not row:
            return 0
        return int(row["c"])
    finally:
        conn.close()


async def _run_idle_stop_after_delay() -> None:
    try:
        await asyncio.sleep(_idle_seconds())
        pending = await asyncio.to_thread(_count_pending_gpu_ocr_tasks)
        if pending > 0:
            logger.info("gpu_idle_autostop: skip stop, pending_gpu_tasks=%s", pending)
            return
        if not gpu_autostart_enabled():
            return
        result = await asyncio.to_thread(stop_gpu_instances)
        logger.info("gpu_idle_autostop: stop_gpu_instances ok %s", result)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("gpu_idle_autostop: delayed stop failed")


def schedule_gpu_idle_stop(*, task_id: Optional[int] = None, assume_gpu: bool = False) -> None:
    """
    Reset debounce timer; after idle seconds, stop cloud GPU if no pending GPU OCR tasks.

    task_id: local worker finished this task — only schedule if use_gpu_ocr=1.
    assume_gpu: True for RunPod terminal callbacks (task is always GPU path).
    """
    if not gpu_autostart_enabled():
        return
    if not assume_gpu:
        if task_id is None:
            return
        if not _task_row_use_gpu_ocr(int(task_id)):
            return

    global _pending_stop_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("gpu_idle_autostop: no running event loop, skip schedule")
        return

    if _pending_stop_task and not _pending_stop_task.done():
        _pending_stop_task.cancel()

    _pending_stop_task = loop.create_task(_run_idle_stop_after_delay())
