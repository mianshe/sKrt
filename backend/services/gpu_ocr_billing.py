"""外部 OCR 次数余额（SQLite 表 gpu_ocr_paid_pages_balance，列名历史原因仍为 pages_balance）。"""
from __future__ import annotations

from typing import Any

from backend.services import knowledge_store


def get_paid_calls_balance(tenant_id: str, client_id: str) -> int:
    conn: Any = knowledge_store.connect()
    try:
        row = conn.execute(
            "SELECT pages_balance FROM gpu_ocr_paid_pages_balance WHERE tenant_id=? AND client_id=?",
            (tenant_id, client_id),
        ).fetchone()
        return int(row["pages_balance"]) if row else 0
    finally:
        conn.close()


def add_paid_calls(tenant_id: str, client_id: str, delta_calls: int, reason: str) -> int:
    """增加或减少次数余额，返回最新余额。"""
    delta = int(delta_calls or 0)
    if delta == 0:
        return get_paid_calls_balance(tenant_id, client_id)
    conn: Any = knowledge_store.connect()
    try:
        conn.execute(
            """
            INSERT INTO gpu_ocr_paid_pages_balance(tenant_id, client_id, pages_balance)
            VALUES(?, ?, ?)
            ON CONFLICT(tenant_id, client_id) DO UPDATE
            SET pages_balance = pages_balance + excluded.pages_balance, updated_at=CURRENT_TIMESTAMP
            """,
            (tenant_id, client_id, delta),
        )
        conn.execute(
            "INSERT INTO gpu_ocr_paid_pages_ledger(tenant_id, client_id, delta_pages, reason) VALUES(?,?,?,?)",
            (tenant_id, client_id, delta, str(reason or "")),
        )
        row = conn.execute(
            "SELECT pages_balance FROM gpu_ocr_paid_pages_balance WHERE tenant_id=? AND client_id=?",
            (tenant_id, client_id),
        ).fetchone()
        conn.commit()
        return int(row["pages_balance"]) if row else 0
    finally:
        conn.close()
