"""
MemoryDecayScheduler – Ebbinghaus-based forgetting curve for long-term memory.

Three decay classes with different forgetting rates:
  - permanent   → never decays (salience one-shot or user-pinned)
  - slow        → slower than Ebbinghaus (high salience, frequently re-activated)
  - normal      → standard Ebbinghaus (default)
  - fast        → accelerated decay (low salience / never re-activated)

Decay is implemented as a reduction of an effective "weight" stored on the
vectors row (decay_factor + last_activated_at).  Background tasks call
prune_expired() to actually DELETE vectors whose weight drops below a
minimum threshold.

Implements phase 7 (long-term memory decay with full exemption logic).
"""

from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------- Ebbinghaus parameters ----------

# Ebbinghaus forgetting curve: R = e^(-t/S)
# where S = relative strength (higher S → slower forgetting)
# We map decay classes to "strength" S values

STRENGTH_PERMANENT = float("inf")  # never forget
STRENGTH_SLOW = 345600.0           # ~4 days half-life at default boost
STRENGTH_NORMAL = 86400.0          # 24 hours half-life at weight=1.0
STRENGTH_FAST = 21600.0            # 6 hours half-life

# Minimum weight before a record is eligible for deletion
MIN_RETENTION_WEIGHT = 0.05

# Background prune interval
DEFAULT_PRUNE_INTERVAL = 1800  # 30 minutes


class MemoryDecayScheduler:
    """Applies Ebbinghaus decay and prunes expired memories."""

    def __init__(
        self,
        db_path: Path,
        *,
        prune_interval: float = DEFAULT_PRUNE_INTERVAL,
    ):
        self.db_path = Path(db_path)
        self.prune_interval = prune_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_decay_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id  TEXT    NOT NULL,
                    vector_id  INTEGER,
                    old_weight REAL    NOT NULL,
                    new_weight REAL    NOT NULL,
                    decay_class TEXT,
                    computed_at REAL  NOT NULL
                )
                """
            )
            conn.commit()

    # ------------------------------------------------------------------
    # decay computation
    # ------------------------------------------------------------------

    def compute_retention(
        self,
        vector_id: int,
        created_at: Optional[float] = None,
        last_activated_at: Optional[float] = None,
        decay_class: str = "normal",
        decay_factor: float = 1.0,
        salience_score: float = 0.3,
    ) -> float:
        """Compute current retention weight (0..∞, capped)."""
        if decay_class == "permanent":
            return float("inf")

        # elapsed time since last activation (or creation)
        now = time.time()
        t = now - (last_activated_at or created_at or now)
        t = max(0.0, t)

        # choose base strength
        strength_map = {
            "slow": STRENGTH_SLOW,
            "normal": STRENGTH_NORMAL,
            "fast": STRENGTH_FAST,
        }
        S = strength_map.get(decay_class, STRENGTH_NORMAL)

        # effective strength = base * decay_factor (boost)
        S_eff = S * max(1.0, decay_factor)

        # Ebbinghaus: R = e^(-t / S_eff)
        retention = math.exp(-t / S_eff)
        return max(0.0, retention)

    def update_vector_retention(self) -> Dict[str, int]:
        """Recompute and update retention weights for ALL vectors.
        
        Returns counts: {updated, pruned, permanent_skipped}
        """
        now = time.time()
        updated = 0
        pruned = 0
        permanent_skipped = 0

        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT id, tenant_id, created_at, last_activated_at,
                          decay_class, decay_factor, salience_score
                   FROM vectors
                   WHERE embedding IS NOT NULL"""
            ).fetchall()

            for row in rows:
                vector_id, tenant_id, created_at, last_activated_at, \
                    decay_class, decay_factor, salience_score = row

                if decay_class == "permanent":
                    permanent_skipped += 1
                    continue

                retention = self.compute_retention(
                    vector_id=vector_id,
                    created_at=created_at,
                    last_activated_at=last_activated_at,
                    decay_class=decay_class or "normal",
                    decay_factor=decay_factor or 1.0,
                    salience_score=salience_score or 0.3,
                )

                if retention < MIN_RETENTION_WEIGHT:
                    # prune
                    conn.execute(
                        "INSERT INTO memory_decay_log "
                        "(tenant_id, vector_id, old_weight, new_weight, decay_class, computed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            tenant_id, vector_id,
                            decay_factor or 1.0, 0.0,
                            decay_class or "normal", now,
                        ),
                    )
                    conn.execute("DELETE FROM vectors WHERE id=?", (vector_id,))
                    pruned += 1
                else:
                    conn.execute(
                        "UPDATE vectors SET decay_factor=? WHERE id=?",
                        (retention, vector_id),
                    )
                    updated += 1

            conn.commit()

        logger.info(
            "MemoryDecayScheduler: updated=%d pruned=%d permanent=%d",
            updated, pruned, permanent_skipped,
        )
        return {
            "updated": updated,
            "pruned": pruned,
            "permanent_skipped": permanent_skipped,
        }

    def prune_by_relevance(
        self, tenant_id: str, min_uses: int = 0, min_score: float = 0.0
    ) -> int:
        """Selective prune: remove vectors below usage and score thresholds."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                """DELETE FROM vectors
                   WHERE tenant_id=?
                     AND (activation_count IS NULL OR activation_count <= ?)
                     AND (salience_score IS NULL OR salience_score <= ?)
                     AND (decay_class IS NULL OR decay_class != 'permanent')
                     AND (decay_class IS NULL OR decay_class != 'slow')""",
                (tenant_id, min_uses, min_score),
            )
            n = cur.rowcount
            conn.commit()
        if n:
            logger.info(f"MemoryDecayScheduler: relevance-pruned {n} vectors")
        return n

    # ------------------------------------------------------------------
    # background scheduler
    # ------------------------------------------------------------------

    async def run_background(self) -> None:
        """Run periodically as a background asyncio task."""
        self._running = True
        logger.info(
            "MemoryDecayScheduler: started (prune_interval=%ds)",
            self.prune_interval,
        )
        while self._running:
            try:
                stats = self.update_vector_retention()
                logger.debug(
                    "Decay tick: updated=%d pruned=%d",
                    stats["updated"], stats["pruned"],
                )
            except Exception:
                logger.exception("MemoryDecayScheduler: error in tick")
            await asyncio.sleep(self.prune_interval)

    def start(self) -> asyncio.Task:
        """Start background decay task."""
        self._task = asyncio.create_task(self.run_background())
        return self._task

    def stop(self) -> None:
        """Stop background decay task."""
        self._running = False
        if self._task:
            self._task.cancel()

    def is_running(self) -> bool:
        return self._running
