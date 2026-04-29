"""
HippocampalReplay – offline sharp-wave-ripple memory consolidation.

The mammalian hippocampus replays waking experiences during sleep/rest
via sharp-wave ripple (SWR) events in CA3→CA1.  This replay is NOT
random – it prioritises high-salience, high-reward, and recently
activated memories, and actively strengthens their weights (LTP).

In our system, this replaces the passive decay model with an active
consolidation loop:

  1. Periodically (default every 30 min), sample memories from:
     - attention_window (recent working memory — highest priority)
     - high-salience fragments
     - vectors with activation_count > 0 but not yet permanent
  2. Re-encode sampled memories → bump decay_factor (simulates LTP)
  3. Apply mild decay to non-replayed memories

Implements phase 7+ (active consolidation vs passive forgetting).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------- configuration ----------

REPLAY_INTERVAL = 1800           # 30 min
REPLAY_SAMPLES_PER_CYCLE = 12   # how many memories to replay per cycle
REPLAY_BOOST = 0.15              # additive boost to decay_factor per replay
REPLAY_MAX_BOOST = 2.5           # cap on replay-boosted factor
SWR_DURATION_FACTOR = 0.1        # replay is "compressed" 10x relative to real-time

# weighting for selection
REPLAY_W_ATTENTION = 0.50       # weight for attention_window entries
REPLAY_W_FRAGMENT = 0.30        # weight for high-salience fragments
REPLAY_W_VECTOR = 0.20          # weight for activated vectors


@dataclass
class ReplayEvent:
    vector_id: int
    source: str               # "attention" | "fragment" | "vector"
    boost_before: float
    boost_after: float
    replayed_at: float
    content_hash: str


class HippocampalReplay:
    """
    SWR-style offline memory replay and consolidation.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        interval: float = REPLAY_INTERVAL,
        samples_per_cycle: int = REPLAY_SAMPLES_PER_CYCLE,
        boost: float = REPLAY_BOOST,
        max_boost: float = REPLAY_MAX_BOOST,
    ):
        self.db_path = Path(db_path)
        self.interval = interval
        self.samples_per_cycle = samples_per_cycle
        self.boost = boost
        self.max_boost = max_boost
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # replay history (in-memory only, for introspection)
        self.last_events: List[ReplayEvent] = []

    # ------------------------------------------------------------------
    # replay cycle
    # ------------------------------------------------------------------

    async def replay_cycle(self) -> List[ReplayEvent]:
        """
        Run one SWR replay cycle: select, re-encode, consolidate.

        Returns list of ReplayEvent for this cycle.
        """
        candidates: List[ReplayCandidate] = []
        candidates.extend(self._sample_attention_window())
        candidates.extend(self._sample_fragments())
        candidates.extend(self._sample_activated_vectors())

        if not candidates:
            logger.debug("HippocampalReplay: no candidates for replay")
            return []

        # sort by priority (highest first)
        candidates.sort(key=lambda c: c.priority, reverse=True)

        # take top N
        selected = candidates[: self.samples_per_cycle]

        events: List[ReplayEvent] = []
        for cand in selected:
            event = self._replay_one(cand)
            if event:
                events.append(event)

        # apply mild passive decay to non-replayed
        self._decay_non_replayed(set(c.vector_id for c in selected if c.vector_id))

        self.last_events = events
        logger.info(
            "HippocampalReplay: replayed %d memories (candidates=%d)",
            len(events),
            len(candidates),
        )
        return events

    # ------------------------------------------------------------------
    # sampling
    # ------------------------------------------------------------------

    def _sample_attention_window(self, top_k: int = 6) -> List[ReplayCandidate]:
        """Sample from the attention window — highest priority."""
        cutoff = time.time() - 3600  # last 1 hour
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT id, activation_count, content_hash, last_activated_at
                   FROM memory_attention_window
                   WHERE last_activated_at >= ?
                   ORDER BY activation_count DESC
                   LIMIT ?""",
                (cutoff, top_k),
            ).fetchall()

        return [
            ReplayCandidate(
                vector_id=None,
                source="attention",
                content_hash=r[2],
                activation_count=r[1],
                priority=REPLAY_W_ATTENTION * r[1],
                extra={"attention_id": r[0]},
            )
            for r in rows
        ]

    def _sample_fragments(self, top_k: int = 4) -> List[ReplayCandidate]:
        """Sample high-salience / resurrected fragments."""
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT id, resurrection_count, content, created_at
                   FROM memory_fragments
                   WHERE resurrection_count > 0
                      OR fragment_type = 'user_correction'
                   ORDER BY resurrection_count DESC, created_at DESC
                   LIMIT ?""",
                (top_k,),
            ).fetchall()

        return [
            ReplayCandidate(
                vector_id=None,
                source="fragment",
                content_hash=_hash_str(r[2]),
                activation_count=r[1] + 1,
                priority=REPLAY_W_FRAGMENT * (r[1] + 1),
                extra={"fragment_id": r[0]},
            )
            for r in rows
        ]

    def _sample_activated_vectors(self, top_k: int = 4) -> List[ReplayCandidate]:
        """Sample vectors with activation_count > 0."""
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT id, activation_count, decay_factor, salience_score, decay_class
                   FROM vectors
                   WHERE activation_count > 0
                     AND (decay_class IS NULL OR decay_class != 'permanent')
                   ORDER BY salience_score DESC, activation_count DESC
                   LIMIT ?""",
                (top_k,),
            ).fetchall()

        return [
            ReplayCandidate(
                vector_id=r[0],
                source="vector",
                content_hash=f"vector:{r[0]}",
                activation_count=r[1],
                priority=REPLAY_W_VECTOR * (r[3] or 0.3),
                extra={
                    "decay_factor": r[2] or 1.0,
                    "salience_score": r[3] or 0.3,
                    "decay_class": r[4] or "normal",
                },
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # re-encode one memory
    # ------------------------------------------------------------------

    def _replay_one(self, cand: ReplayCandidate) -> Optional[ReplayEvent]:
        """Re-encode a single memory, boosting its decay_factor."""
        now = time.time()
        if cand.source == "vector" and cand.vector_id:
            vector_id = cand.vector_id
            old_boost = cand.extra.get("decay_factor", 1.0)
            new_boost = min(self.max_boost, old_boost + self.boost)

            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    """UPDATE vectors
                       SET decay_factor = ?,
                           last_activated_at = ?
                       WHERE id = ?""",
                    (new_boost, now, vector_id),
                )
                conn.commit()

            return ReplayEvent(
                vector_id=vector_id,
                source=cand.source,
                boost_before=old_boost,
                boost_after=new_boost,
                replayed_at=now,
                content_hash=cand.content_hash,
            )

        # for attention / fragment sources: bump activation in vectors if linked
        elif cand.source == "attention":
            match_id = self._match_attention_to_vector(cand.content_hash)
            if match_id:
                old_boost = self._get_decay_factor(match_id)
                new_boost = min(self.max_boost, old_boost + self.boost)

                with sqlite3.connect(str(self.db_path)) as conn:
                    conn.execute(
                        """UPDATE vectors
                           SET decay_factor = ?, activation_count = activation_count + 1,
                               last_activated_at = ?
                           WHERE id = ?""",
                        (new_boost, now, match_id),
                    )
                    conn.commit()

                return ReplayEvent(
                    vector_id=match_id,
                    source=cand.source,
                    boost_before=old_boost,
                    boost_after=new_boost,
                    replayed_at=now,
                    content_hash=cand.content_hash,
                )

        return None

    def _match_attention_to_vector(self, content_hash: str) -> Optional[int]:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT id FROM vectors WHERE content_hash=? LIMIT 1",
                (content_hash,),
            ).fetchone()
        return row[0] if row else None

    def _get_decay_factor(self, vector_id: int) -> float:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT decay_factor FROM vectors WHERE id=?", (vector_id,)
            ).fetchone()
        return row[0] if row and row[0] else 1.0

    # ------------------------------------------------------------------
    # passive decay for non-replayed
    # ------------------------------------------------------------------

    def _decay_non_replayed(self, replayed_ids: Set[int]) -> None:
        """Apply mild decay to vectors NOT replayed this cycle."""
        now = time.time()
        with sqlite3.connect(str(self.db_path)) as conn:
            if replayed_ids:
                placeholders = ",".join("?" * len(replayed_ids))
                conn.execute(
                    f"""UPDATE vectors
                        SET decay_factor = MAX(0.5, decay_factor - 0.02)
                        WHERE activation_count > 0
                          AND id NOT IN ({placeholders})
                          AND (decay_class IS NULL OR decay_class NOT IN ('permanent', 'slow'))
                          AND (last_activated_at IS NULL OR last_activated_at < ?)""",
                    (*replayed_ids, now - 1800),
                )
            else:
                conn.execute(
                    """UPDATE vectors
                       SET decay_factor = MAX(0.5, decay_factor - 0.02)
                       WHERE activation_count > 0
                         AND (decay_class IS NULL OR decay_class NOT IN ('permanent', 'slow'))
                         AND (last_activated_at IS NULL OR last_activated_at < ?)""",
                    (now - 1800,),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # background runner
    # ------------------------------------------------------------------

    async def run_background(self) -> None:
        self._running = True
        logger.info("HippocampalReplay: started (interval=%ds)", self.interval)
        while self._running:
            try:
                await self.replay_cycle()
            except Exception:
                logger.exception("HippocampalReplay: error in replay cycle")
            await asyncio.sleep(self.interval)

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run_background())
        return self._task

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    def is_running(self) -> bool:
        return self._running


# ====================================================================
#  data model
# ====================================================================


@dataclass
class ReplayCandidate:
    vector_id: Optional[int]
    source: str
    content_hash: str
    activation_count: int
    priority: float
    extra: Dict = field(default_factory=dict)


def _hash_str(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:16]
