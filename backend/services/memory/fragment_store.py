"""
FragmentStore – persistent storage for subconscious fragments.

Subconscious fragments are intermediate reasoning products that were
computed during the Agent's execution but were NOT selected as the
final output.  Examples:
  - Map-reduce intermediate map results
  - Retrieved chunks that passed similarity but were filtered out
  - Alternative reasoning branches (counterexample_check failures, etc.)
  - Internal reasoning claims that were discarded

These fragments are stored offline (not in the hot attention window)
and can later be resurrected if new evidence corroborates them.

Implements phase 5 (subconscious fragment persistence).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------- configuration ----------

FRAGMENT_TTL_SECONDS = 86400 * 7   # 7 days default
MAX_FRAGMENTS_PER_SESSION = 200
FRAGMENT_TYPES = frozenset({
    "map_intermediate",        # map-reduce map step output
    "filtered_chunk",          # retrieved but not used
    "discarded_reasoning",     # counterexample / consistency failure
    "alternative_branch",      # alternative reasoning path
    "attention_overflow",      # evicted from attention window
    "user_correction",         # user feedback / correction
})


@dataclass
class Fragment:
    id: int
    tenant_id: str
    session_id: str
    fragment_type: str
    source_ref: str          # e.g. chunk_id, reasoning_step_id
    content: str
    embedding: Optional[List[float]]
    metadata: Dict[str, object] = field(default_factory=dict)
    created_at: float = 0.0
    resurrection_count: int = 0
    last_resurrected_at: Optional[float] = None


class FragmentStore:
    """Persistent SQLite store for subconscious reasoning fragments."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_fragments (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id     TEXT    NOT NULL,
                    session_id    TEXT    NOT NULL DEFAULT 'default',
                    fragment_type TEXT    NOT NULL,
                    source_ref    TEXT    NOT NULL DEFAULT '',
                    content       TEXT    NOT NULL,
                    embedding     TEXT,
                    metadata      TEXT    DEFAULT '{}',
                    created_at    REAL    NOT NULL,
                    resurrection_count INTEGER NOT NULL DEFAULT 0,
                    last_resurrected_at REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_frag_tenant_session "
                "ON memory_fragments(tenant_id, session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_frag_type "
                "ON memory_fragments(fragment_type)"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def store(
        self,
        tenant_id: str,
        session_id: str,
        fragment_type: str,
        content: str,
        source_ref: str = "",
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, object]] = None,
    ) -> Fragment:
        if fragment_type not in FRAGMENT_TYPES:
            raise ValueError(f"Unknown fragment_type: {fragment_type}")

        now = time.time()

        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                """
                INSERT INTO memory_fragments
                    (tenant_id, session_id, fragment_type, source_ref,
                     content, embedding, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    fragment_type,
                    source_ref,
                    content,
                    json.dumps(embedding) if embedding else None,
                    json.dumps(metadata or {}),
                    now,
                ),
            )
            fragment_id = cur.lastrowid

            # enforce per-session cap (FIFO)
            conn.execute(
                """
                DELETE FROM memory_fragments
                WHERE id NOT IN (
                    SELECT id FROM memory_fragments
                    WHERE tenant_id=? AND session_id=?
                    ORDER BY created_at DESC LIMIT ?
                ) AND tenant_id=? AND session_id=?
                """,
                (
                    tenant_id,
                    session_id,
                    MAX_FRAGMENTS_PER_SESSION,
                    tenant_id,
                    session_id,
                ),
            )
            conn.commit()

        return Fragment(
            id=fragment_id,
            tenant_id=tenant_id,
            session_id=session_id,
            fragment_type=fragment_type,
            source_ref=source_ref,
            content=content,
            embedding=embedding,
            metadata=metadata or {},
            created_at=now,
        )

    def store_batch(
        self,
        fragments: List[Dict[str, object]],
        tenant_id: str,
        session_id: str,
    ) -> List[Fragment]:
        """Bulk store many fragments."""
        results: List[Fragment] = []
        for frag in fragments:
            f = self.store(
                tenant_id=tenant_id,
                session_id=session_id,
                fragment_type=str(frag.get("type", "filtered_chunk")),
                content=str(frag.get("content", "")),
                source_ref=str(frag.get("source_ref", "")),
                embedding=frag.get("embedding"),  # type: ignore[arg-type]
                metadata=frag.get("metadata"),   # type: ignore[arg-type]
            )
            results.append(f)
        return results

    def resurrect(
        self, fragment_id: int
    ) -> Optional[Fragment]:
        """Mark a fragment as resurrected (brought back into attention)."""
        now = time.time()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """UPDATE memory_fragments
                   SET resurrection_count = resurrection_count + 1,
                       last_resurrected_at = ?
                   WHERE id=?""",
                (now, fragment_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM memory_fragments WHERE id=?", (fragment_id,)
            ).fetchone()

        if row is None:
            return None
        return self._row_to_fragment(row)

    def list_by_type(
        self,
        tenant_id: str,
        session_id: str,
        fragment_type: str,
        limit: int = 20,
    ) -> List[Fragment]:
        """List fragments of a given type, newest first."""
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT * FROM memory_fragments
                   WHERE tenant_id=? AND session_id=? AND fragment_type=?
                   ORDER BY created_at DESC LIMIT ?""",
                (tenant_id, session_id, fragment_type, limit),
            ).fetchall()
        return [self._row_to_fragment(r) for r in rows]

    def search_similar(
        self,
        tenant_id: str,
        embedding: List[float],
        top_k: int = 5,
    ) -> List[Fragment]:
        """Find fragments with embeddings similar to the given vector."""
        from .attention_window import _cosine_similarity

        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT * FROM memory_fragments
                   WHERE tenant_id=? AND embedding IS NOT NULL
                   ORDER BY created_at DESC LIMIT 500""",
                (tenant_id,),
            ).fetchall()

        scored: List[tuple[Fragment, float]] = []
        for row in rows:
            frag = self._row_to_fragment(row)
            if frag.embedding:
                sim = _cosine_similarity(embedding, frag.embedding)
                scored.append((frag, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [f for f, _ in scored[:top_k]]

    def prune_expired(self, ttl_seconds: int = FRAGMENT_TTL_SECONDS) -> int:
        """Delete fragments older than TTL (unless resurrected)."""
        cutoff = time.time() - ttl_seconds
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                """DELETE FROM memory_fragments
                   WHERE created_at < ?
                     AND resurrection_count = 0""",
                (cutoff,),
            )
            n = cur.rowcount
            conn.commit()
        if n:
            logger.info(f"FragmentStore: pruned {n} expired fragments")
        return n

    def _row_to_fragment(self, row: tuple) -> Fragment:
        return Fragment(
            id=row[0],
            tenant_id=row[1],
            session_id=row[2],
            fragment_type=row[3],
            source_ref=row[4],
            content=row[5],
            embedding=json.loads(row[6]) if row[6] else None,
            metadata=json.loads(row[7]) if row[7] else {},
            created_at=row[8],
            resurrection_count=row[9],
            last_resurrected_at=row[10],
        )
