"""
RQ 解析 Worker（独立进程）。与 API 进程共享 SQLite 数据库路径与 uploads 目录挂载。

示例（PowerShell）：
  set REDIS_URL=redis://127.0.0.1:6379/0
  set PYTHONPATH=D:\\xm\\xm1
  cd D:\\xm\\xm1
  python -m backend.workers.ingestion_rq_worker
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(root))
    os.chdir(root)

    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        logger.error("REDIS_URL 未设置")
        sys.exit(1)

    from redis import Redis
    from rq import Queue, Worker

    import backend.services.ingestion_rq  # noqa: F401 — 确保 job 可反序列化

    redis_conn = Redis.from_url(url)
    q = os.getenv("INGESTION_RQ_QUEUE", "ingest").strip() or "ingest"
    raw = (os.getenv("INGESTION_RQ_QUEUES") or "").strip()
    if raw:
        if raw.startswith("["):
            parsed = json.loads(raw)
            queues = list(parsed) if isinstance(parsed, list) else [str(parsed)]
        else:
            queues = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        queues = [q]
    listen = [Queue(name, connection=redis_conn) for name in queues]
    worker = Worker(listen, connection=redis_conn)
    logger.info("starting RQ worker queues=%s", queues)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
