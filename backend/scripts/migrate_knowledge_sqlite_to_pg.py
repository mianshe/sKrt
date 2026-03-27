"""
将 data/knowledge.db（SQLite）迁移到 DATABASE_URL（PostgreSQL）。

前提：目标库已应用 backend/services/knowledge_app_schema.sql（或启动应用一次让 init 建表），
且各业务表为空；否则请先自行 TRUNCATE 相关表。

用法（在 xm1 根目录）:
  set DATABASE_URL=postgresql://...
  python -m backend.scripts.migrate_knowledge_sqlite_to_pg

迁移后设置 KNOWLEDGE_STORE=postgres 并重启 API/Worker。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2


def main() -> None:
    db_url = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip()
    if not db_url:
        print("请设置 DATABASE_URL", file=sys.stderr)
        sys.exit(1)
    sqlite_path = ROOT / "data" / "knowledge.db"
    if not sqlite_path.exists():
        print(f"未找到 SQLite 文件: {sqlite_path}", file=sys.stderr)
        sys.exit(1)

    sq = sqlite3.connect(str(sqlite_path))
    sq.row_factory = sqlite3.Row
    pg = psycopg2.connect(db_url)
    try:
        # schema 初始化会插入 id=1 的 rollup 占位行，避免与 SQLite 导入主键冲突
        with pg.cursor() as cur:
            cur.execute("DELETE FROM ingestion_timing_rollups")
        pg.commit()

        tables = [
            "documents",
            "vectors",
            "kg_relations",
            "chat_sessions",
            "gpu_ocr_daily_pages",
            "gpu_ocr_global_monthly_pages",
            "gpu_ocr_global_monthly_usage",
            "gpu_ocr_daily_usage",
            "gpu_ocr_paid_pages_balance",
            "gpu_ocr_paid_pages_ledger",
            "pay_orders",
            "pay_callbacks",
            "upload_tasks",
            "vector_ingest_checkpoints",
            "ocr_page_cache",
            "ingestion_timing_rollups",
            "document_summaries",
            "upload_throttle_minute",
        ]

        for table in tables:
            rows = sq.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                print(f"{table}: 0 rows")
                continue
            cols = list(rows[0].keys())
            placeholders = ",".join(["%s"] * len(cols))
            col_list = ",".join(cols)
            sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
            with pg.cursor() as cur:
                for r in rows:
                    cur.execute(sql, tuple(r[c] for c in cols))
            pg.commit()
            print(f"{table}: migrated {len(rows)} rows")

        serial_tables = [
            "documents",
            "vectors",
            "kg_relations",
            "chat_sessions",
            "gpu_ocr_daily_pages",
            "gpu_ocr_daily_usage",
            "gpu_ocr_paid_pages_balance",
            "gpu_ocr_paid_pages_ledger",
            "pay_orders",
            "pay_callbacks",
            "upload_tasks",
            "vector_ingest_checkpoints",
            "ocr_page_cache",
            "document_summaries",
        ]
        with pg.cursor() as cur:
            for seq_table in serial_tables:
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{seq_table}','id'), "
                    f"COALESCE((SELECT MAX(id) FROM {seq_table}), 1))"
                )
        pg.commit()
        print("序列已对齐。")
    finally:
        sq.close()
        pg.close()


if __name__ == "__main__":
    main()
