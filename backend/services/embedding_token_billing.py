from __future__ import annotations

from typing import Any, Dict, List

from backend.services import knowledge_store


def get_token_balance(tenant_id: str, client_id: str) -> int:
    conn: Any = knowledge_store.connect()
    try:
        row = conn.execute(
            "SELECT tokens_balance FROM embedding_token_balance WHERE tenant_id=? AND client_id=?",
            (tenant_id, client_id),
        ).fetchone()
        return int(row["tokens_balance"]) if row else 0
    finally:
        conn.close()


def add_tokens(tenant_id: str, client_id: str, delta_tokens: int, reason: str) -> int:
    delta = int(delta_tokens or 0)
    if delta == 0:
        return get_token_balance(tenant_id, client_id)
    conn: Any = knowledge_store.connect()
    try:
        conn.execute(
            """
            INSERT INTO embedding_token_balance(tenant_id, client_id, tokens_balance)
            VALUES(?, ?, ?)
            ON CONFLICT(tenant_id, client_id) DO UPDATE
            SET tokens_balance = tokens_balance + excluded.tokens_balance, updated_at=CURRENT_TIMESTAMP
            """,
            (tenant_id, client_id, delta),
        )
        conn.execute(
            "INSERT INTO embedding_token_ledger(tenant_id, client_id, delta_tokens, reason) VALUES(?,?,?,?)",
            (tenant_id, client_id, delta, str(reason or "")),
        )
        row = conn.execute(
            "SELECT tokens_balance FROM embedding_token_balance WHERE tenant_id=? AND client_id=?",
            (tenant_id, client_id),
        ).fetchone()
        conn.commit()
        return int(row["tokens_balance"]) if row else 0
    finally:
        conn.close()


def recent_ledger(tenant_id: str, client_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    n = max(1, min(100, int(limit or 20)))
    conn: Any = knowledge_store.connect()
    try:
        rows = conn.execute(
            """
            SELECT delta_tokens, reason, created_at
            FROM embedding_token_ledger
            WHERE tenant_id=? AND client_id=?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (tenant_id, client_id, n),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
