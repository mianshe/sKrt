"""
知识库双后端：默认 SQLite（knowledge.db），KNOWLEDGE_STORE=postgres 时使用 DATABASE_URL。

生产多副本/多 Worker 应使用 postgres，避免共享 SQLite 文件。
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional, Tuple, Union

logger = logging.getLogger(__name__)

_sqlite_path: Optional[str] = None
_sqlite_busy_timeout_ms: int = 8000
_sqlite_wal: bool = True
_pg_url: str = ""

_SCHEMA_PATH = Path(__file__).resolve().parent / "knowledge_app_schema.sql"


def configure(*, sqlite_path: str, busy_timeout_ms: int = 8000, wal_enabled: bool = True, database_url: str = "") -> None:
    global _sqlite_path, _sqlite_busy_timeout_ms, _sqlite_wal, _pg_url
    _sqlite_path = sqlite_path
    _sqlite_busy_timeout_ms = int(busy_timeout_ms)
    _sqlite_wal = bool(wal_enabled)
    _pg_url = (database_url or "").strip()


def use_postgres() -> bool:
    return (os.getenv("KNOWLEDGE_STORE", "").strip().lower() == "postgres") and bool(_pg_url)


def _pg_connect():
    import psycopg2

    return psycopg2.connect(_pg_url)


def adapt_placeholders(sql: str) -> str:
    """SQLite ? -> PostgreSQL %s。"""
    if sql.count("?") == 0:
        return sql
    return re.sub(r"\?", "%s", sql)


class PgCompatCursor:
    def __init__(self, cur: Any, lastrowid: Optional[int] = None) -> None:
        self._cur = cur
        self.lastrowid = lastrowid

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> Any:
        return self._cur.fetchall()


class PgCompatConnection:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def execute(self, sql: str, params: Optional[Tuple[Any, ...]] = None) -> PgCompatCursor:
        import psycopg2.extras

        sql = adapt_placeholders(sql)
        cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        return PgCompatCursor(cur)

    def commit(self) -> None:
        self._raw.commit()

    def close(self) -> None:
        self._raw.close()


def connect() -> Union[sqlite3.Connection, PgCompatConnection]:
    if use_postgres():
        return PgCompatConnection(_pg_connect())
    assert _sqlite_path is not None
    conn = sqlite3.connect(_sqlite_path, timeout=_sqlite_busy_timeout_ms / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_sqlite_busy_timeout_ms}")
    if _sqlite_wal:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_application_schema() -> None:
    """在 PostgreSQL 上创建知识库表。"""
    from .pipeline import postgres_store as pg_store

    if not _pg_url:
        raise RuntimeError("DATABASE_URL 未配置，无法初始化 PostgreSQL 知识库")
    sql_text = _SCHEMA_PATH.read_text(encoding="utf-8")
    stmts = pg_store._split_postgresql_statements(sql_text)
    conn = _pg_connect()
    try:
        for stmt in stmts:
            if not stmt.strip():
                continue
            with conn.cursor() as cur:
                cur.execute(stmt)
        conn.commit()
        logger.info("knowledge_app_schema applied on PostgreSQL")
    finally:
        conn.close()


def insert_returning_id(conn: Any, sql: str, params: Tuple[Any, ...]) -> int:
    """INSERT 后取 id：PostgreSQL 用 RETURNING id，SQLite 用 lastrowid。"""
    if use_postgres():
        s = adapt_placeholders(sql.strip().rstrip(";"))
        if "RETURNING" not in s.upper():
            s = s + " RETURNING id"
        cur = conn.execute(s, params)
        row = cur.fetchone()
        if not row:
            raise RuntimeError("insert_returning_id: empty result")
        return int(row["id"])
    cur = conn.execute(sql, params)
    return int(cur.lastrowid)


def health_database_label() -> str:
    if use_postgres():
        return "postgresql"
    return str(_sqlite_path or "sqlite")

