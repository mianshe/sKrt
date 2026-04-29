"""
ReencodeBooster – Hebbian weight-lift for frequently activated memories.

When the AttentionWindow detects a semantic re-exposure (phase 6a – 
re-activation), this module computes a boost factor and applies it
to the corresponding vector in the long-term store (vectors table).

The boost is additive: the vector's embedding is NOT modified; instead
the boost is recorded as metadata columns on the vectors row and used
by the decay scheduler to slow down forgetting.

Implements phase 6a (re-exposure reinforcement / Hebbian re-encode).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------- configuration ----------

DEFAULT_BOOST_FACTOR = 1.5        # multiplicative factor for activation_count → weight
MAX_BOOST_WEIGHT = 3.0            # cap on cumulative boost
BOOST_DECAY_RATE = 0.0001         # per-hour decay of boost when not activated
RE_ENCODE_COOLDOWN_SECONDS = 300  # 5 min between re-encodes of same vector


@dataclass
class ReencodeRecord:
    vector_id: int
    boost_weight: float
    activation_count: int
    reencoded_at: float
    source: str  # "attention_window" | "salience_one_shot" | "manual"


class ReencodeBooster:
    """Applies Hebbian weight lift to vectors in the long-term store."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._ensure_table()
        self._ensure_vectors_columns()

    def _ensure_table(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_reencode_log (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id        TEXT    NOT NULL,
                    vector_id        INTEGER NOT NULL,
                    boost_weight     REAL    NOT NULL DEFAULT 1.0,
                    activation_count INTEGER NOT NULL DEFAULT 0,
                    reencoded_at     REAL    NOT NULL,
                    source           TEXT    NOT NULL DEFAULT 'attention_window'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reencode_vector "
                "ON memory_reencode_log(vector_id)"
            )
            conn.commit()

    def _ensure_vectors_columns(self) -> None:
        """Add boost-related columns to vectors table if missing."""
        cols_to_add = [
            ("activation_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_activated_at", "REAL"),
            ("decay_factor", "REAL NOT NULL DEFAULT 1.0"),
            ("salience_score", "REAL NOT NULL DEFAULT 0.3"),
            ("decay_class", "TEXT NOT NULL DEFAULT 'normal'"),
        ]
        with sqlite3.connect(str(self.db_path)) as conn:
            existing = {
                r[1]
                for r in conn.execute("PRAGMA table_info(vectors)").fetchall()
            }
            for col_name, col_def in cols_to_add:
                if col_name not in existing:
                    try:
                        conn.execute(
                            f"ALTER TABLE vectors ADD COLUMN {col_name} {col_def}"
                        )
                        logger.info("ReencodeBooster: added column vectors.%s", col_name)
                    except sqlite3.OperationalError:
                        pass  # already exists
            conn.commit()

    # ------------------------------------------------------------------
    # boost API
    # ------------------------------------------------------------------

    def boost_from_attention(
        self,
        tenant_id: str,
        vector_id: int,
        content_hash: str,
        activation_count: int,
    ) -> float:
        """Apply Hebbian boost triggered by attention window re-exposure."""
        if self._in_cooldown(vector_id):
            logger.debug("ReencodeBooster: vector %d in cooldown, skipping", vector_id)
            return self._current_weight(vector_id)

        # power-law boost: weight = 1 + log2(activation_count) * (factor - 1)
        boost = 1.0
        if activation_count > 1:
            import math
            boost = 1.0 + math.log2(activation_count) * (DEFAULT_BOOST_FACTOR - 1.0)
        boost = min(boost, MAX_BOOST_WEIGHT)

        now = time.time()
        with sqlite3.connect(str(self.db_path)) as conn:
            # update vectors table
            conn.execute(
                """UPDATE vectors
                   SET activation_count = ?,
                       last_activated_at = ?,
                       decay_factor = MAX(decay_factor, ?)
                   WHERE id = ?""",
                (activation_count, now, boost, vector_id),
            )
            # log the re-encode
            conn.execute(
                """INSERT INTO memory_reencode_log
                    (tenant_id, vector_id, boost_weight, activation_count,
                     reencoded_at, source)
                   VALUES (?, ?, ?, ?, ?, 'attention_window')""",
                (tenant_id, vector_id, boost, activation_count, now),
            )
            conn.commit()

        logger.info(
            "ReencodeBooster: vector %d boosted to %.3f (activations=%d)",
            vector_id, boost, activation_count,
        )
        return boost

    def boost_one_shot(
        self,
        tenant_id: str,
        vector_id: int,
        salience_score: float,
    ) -> float:
        """Apply one-shot permanent boost (salience > threshold)."""
        # one-shot maps salience_score directly to boost (0.85+ → 3.0 max)
        boost = min(MAX_BOOST_WEIGHT, salience_score * MAX_BOOST_WEIGHT)

        now = time.time()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """UPDATE vectors
                   SET activation_count = activation_count + 1,
                       last_activated_at = ?,
                       decay_factor = MAX(decay_factor, ?),
                       salience_score = MAX(salience_score, ?),
                       decay_class = 'permanent'
                   WHERE id = ?""",
                (now, boost, salience_score, vector_id),
            )
            conn.execute(
                """INSERT INTO memory_reencode_log
                    (tenant_id, vector_id, boost_weight, activation_count,
                     reencoded_at, source)
                   VALUES (?, ?, ?, 1, ?, 'salience_one_shot')""",
                (tenant_id, vector_id, boost, now),
            )
            conn.commit()

        logger.info(
            "ReencodeBooster: one-shot vector %d → permanent (salience=%.3f)",
            vector_id, salience_score,
        )
        return boost

    def get_boost_history(
        self, vector_id: int, limit: int = 10
    ) -> List[ReencodeRecord]:
        """Return re-encode history for a vector."""
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT vector_id, boost_weight, activation_count,
                          reencoded_at, source
                   FROM memory_reencode_log
                   WHERE vector_id=?
                   ORDER BY reencoded_at DESC LIMIT ?""",
                (vector_id, limit),
            ).fetchall()

        return [
            ReencodeRecord(
                vector_id=r[0],
                boost_weight=r[1],
                activation_count=r[2],
                reencoded_at=r[3],
                source=r[4],
            )
            for r in rows
        ]

    def apply_boost_decay(self) -> int:
        """Decay boost weights over time for non-activated vectors.

        Returns number of vectors decayed.
        """
        now = time.time()
        with sqlite3.connect(str(self.db_path)) as conn:
            # decay_factor slowly drifts toward 1.0 when not recently activated
            cur = conn.execute(
                """UPDATE vectors
                   SET decay_factor = MAX(1.0, decay_factor - ?)
                   WHERE decay_factor > 1.0
                     AND (last_activated_at IS NULL
                          OR last_activated_at < ?)
                     AND decay_class != 'permanent'
                     AND decay_class != 'slow'""",
                (BOOST_DECAY_RATE, now - RE_ENCODE_COOLDOWN_SECONDS * 2),
            )
            n = cur.rowcount
            conn.commit()
        if n:
            logger.debug("ReencodeBooster: decayed boost on %d vectors", n)
        return n

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _in_cooldown(self, vector_id: int) -> bool:
        """Check if vector was recently re-encoded."""
        cutoff = time.time() - RE_ENCODE_COOLDOWN_SECONDS
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                """SELECT MAX(reencoded_at) FROM memory_reencode_log
                   WHERE vector_id=?""",
                (vector_id,),
            ).fetchone()
        if row and row[0] and row[0] > cutoff:
            return True
        return False

    def _current_weight(self, vector_id: int) -> float:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT decay_factor FROM vectors WHERE id=?", (vector_id,)
            ).fetchone()
        return row[0] if row and row[0] else 1.0
