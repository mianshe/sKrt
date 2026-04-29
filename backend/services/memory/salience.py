"""
SalienceScorer – evaluates how "memorable" a chunk/reasoning product is.

Four dimensions (expanded with CA1 prediction error):
  1. novelty          – how different from the existing knowledge base
  2. contradiction    – how much it conflicts with established knowledge
  3. user_feedback    – explicit user signals (upvote, correction, pin)
  4. mismatch         – prediction-error surprise (CA1-style)

A high salience score (> 0.85) triggers one-shot permanent storage
(memory becomes immune to decay, akin to flashbulb memory in humans).

Implements phase 6b (one-shot salience permanent storage) fused with
CA1 prediction-error dynamic salience.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from .mismatch_detector import MismatchDetector, MismatchResult

logger = logging.getLogger(__name__)

# ---------- configuration ----------

NOVELTY_THRESHOLD = 0.55       # cosine similarity below this → highly novel
CONTRADICTION_THRESHOLD = 0.72 # cosine with opposite-polarity statements
SALIENCE_ONE_SHOT = 0.85       # above this → permanent (immune to decay)
SALIENCE_HIGH = 0.65           # decay very slow
SALIENCE_MEDIUM = 0.40         # normal decay
SALIENCE_DEFAULT = 0.30        # below this → normal decay



@dataclass
class SalienceResult:
    score: float               # 0..1
    novelty: float             # 0..1  (higher = more novel)
    contradiction: float       # 0..1  (higher = more contradictory)
    user_boost: float          # 0..1  (user feedback)
    trigger_one_shot: bool     # score > SALIENCE_ONE_SHOT
    decay_class: str           # "permanent" | "slow" | "normal" | "fast"


class SalienceScorer:
    """Computes salience from novelty, contradiction, and user signals."""

    def __init__(
        self,
        db_path: Path,
        *,
        novelty_threshold: float = NOVELTY_THRESHOLD,
        contradiction_threshold: float = CONTRADICTION_THRESHOLD,
    ):
        self.db_path = Path(db_path)
        self.novelty_threshold = novelty_threshold
        self.contradiction_threshold = contradiction_threshold
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_salience (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id     TEXT    NOT NULL,
                    vector_id     INTEGER,
                    fragment_id   INTEGER,
                    score         REAL    NOT NULL DEFAULT 0.3,
                    novelty       REAL    NOT NULL DEFAULT 0.5,
                    contradiction REAL    NOT NULL DEFAULT 0.0,
                    user_boost    REAL    NOT NULL DEFAULT 0.0,
                    decay_class   TEXT    NOT NULL DEFAULT 'normal',
                    computed_at   REAL    NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_salience_tenant "
                "ON memory_salience(tenant_id)"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # scoring
    # ------------------------------------------------------------------

    async def score(
        self,
        tenant_id: str,
        chunk_embedding: List[float],
        *,
        existing_embeddings: Optional[List[List[float]]] = None,
        contradiction_embedding: Optional[List[float]] = None,
        user_boost: float = 0.0,
        vector_id: Optional[int] = None,
        fragment_id: Optional[int] = None,
    ) -> SalienceResult:
        # 1. novelty – how far from the nearest neighbour in existing KB
        novelty = await self._compute_novelty(
            tenant_id, chunk_embedding, existing_embeddings
        )

        # 2. contradiction – semantic opposition
        contradiction = self._compute_contradiction(
            chunk_embedding, contradiction_embedding
        )

        # 3. user feedback (clamped)
        user = max(0.0, min(1.0, user_boost))

        # 4. weighted aggregate
        #    novelty:  0.45 weight  (most important for long-term)
        #    contradiction: 0.35   (flashbulb trigger)
        #    user:     0.20        (explicit signal)
        score = novelty * 0.45 + contradiction * 0.35 + user * 0.20

        # classify
        one_shot = score >= SALIENCE_ONE_SHOT
        if one_shot:
            decay_class = "permanent"
        elif score >= SALIENCE_HIGH:
            decay_class = "slow"
        elif score >= SALIENCE_MEDIUM:
            decay_class = "normal"
        else:
            decay_class = "fast"

        result = SalienceResult(
            score=round(score, 4),
            novelty=round(novelty, 4),
            contradiction=round(contradiction, 4),
            user_boost=round(user, 4),
            trigger_one_shot=one_shot,
            decay_class=decay_class,
        )

        # persist score
        self._save_score(tenant_id, result, vector_id, fragment_id)

        return result

    async def score_with_mismatch(
        self,
        tenant_id: str,
        chunk_embedding: List[float],
        mismatch_detector: "MismatchDetector",
        *,
        predicted_embedding: Optional[List[float]] = None,
        prediction_confidence: float = 0.5,
        existing_embeddings: Optional[List[List[float]]] = None,
        contradiction_embedding: Optional[List[float]] = None,
        user_boost: float = 0.0,
        vector_id: Optional[int] = None,
        fragment_id: Optional[int] = None,
    ) -> SalienceResult:
        """
        Score salience with CA1 prediction-error fusion.

        Computes static salience first, then fuses it with the mismatch
        between predicted and actual embeddings for dynamic salience.
        """
        # 1. static salience (novelty + contradiction + user)
        static_result = await self.score(
            tenant_id=tenant_id,
            chunk_embedding=chunk_embedding,
            existing_embeddings=existing_embeddings,
            contradiction_embedding=contradiction_embedding,
            user_boost=user_boost,
            vector_id=vector_id,
            fragment_id=fragment_id,
        )

        # 2. compute mismatch if prediction embedding is provided
        if predicted_embedding is None:
            return static_result

        from .mismatch_detector import MismatchResult

        mismatch: MismatchResult = mismatch_detector.compute_mismatch(
            predicted_embedding=predicted_embedding,
            actual_embedding=chunk_embedding,
            prediction_confidence=prediction_confidence,
        )

        if mismatch.surprise_level == "expected":
            return static_result

        # 3. fuse: static (70%) + mismatch (30%)
        fused_score = mismatch_detector.fuse_salience(
            static_salience=static_result.score,
            mismatch=mismatch,
        )

        one_shot = fused_score >= SALIENCE_ONE_SHOT
        if one_shot:
            decay_class = "permanent"
        elif fused_score >= SALIENCE_HIGH:
            decay_class = "slow"
        elif fused_score >= SALIENCE_MEDIUM:
            decay_class = "normal"
        else:
            decay_class = "fast"

        dynamic_result = SalienceResult(
            score=round(fused_score, 4),
            novelty=static_result.novelty,
            contradiction=static_result.contradiction,
            user_boost=static_result.user_boost,
            trigger_one_shot=one_shot,
            decay_class=decay_class,
        )

        # persist dynamic score
        self._save_score(tenant_id, dynamic_result, vector_id, fragment_id)

        logger.info(
            "SalienceScorer: static=%.3f + mismatch=%.3f → dynamic=%.3f (%s → %s)",
            static_result.score,
            mismatch.salience_modifier,
            fused_score,
            static_result.decay_class,
            decay_class,
        )
        return dynamic_result

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    async def _compute_novelty(
        self,
        tenant_id: str,
        embedding: List[float],
        existing: Optional[List[List[float]]] = None,
    ) -> float:
        """
        Novelty = 1 - max_similarity_to_existing.
        If no existing vectors, novelty = 1.0 (completely new).
        """
        if existing is None:
            existing = self._load_existing_embeddings(tenant_id)

        if not existing:
            return 1.0

        from .attention_window import _cosine_similarity

        max_sim = max(
            _cosine_similarity(embedding, e) for e in existing
        )
        novelty = 1.0 - max_sim

        # boost novelty if max_sim is below novelty_threshold
        if max_sim < self.novelty_threshold:
            novelty = min(1.0, novelty * 1.3)

        return novelty

    def _compute_contradiction(
        self,
        embedding: List[float],
        contradiction_embedding: Optional[List[float]] = None,
    ) -> float:
        """
        Contradiction: if a "negated" or contradictory embedding exists,
        we detect opposition.  Without an explicit contradictory vector,
        fall back to checking if the chunk internally has conflicting
        signals (cosine with itself near 1.0 → not contradictory).
        """
        if contradiction_embedding is None:
            return 0.0

        from .attention_window import _cosine_similarity

        sim = _cosine_similarity(embedding, contradiction_embedding)
        # Opposite = high similarity to a known-contradictory statement
        # (contradiction_embedding is the "refuting" vector).
        # If sim is high, it means the chunk looks like the contradiction.
        # Real contradiction detection would require a dedicated NLI model;
        # here we use a heuristic: if sim > CONTRADICTION_THRESHOLD, it's
        # contradictory.
        if sim >= self.contradiction_threshold:
            return sim
        return 0.0

    def _load_existing_embeddings(
        self, tenant_id: str, sample_limit: int = 200
    ) -> List[List[float]]:
        """Load sample embeddings from vectors table for novelty check."""
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT embedding FROM vectors
                   WHERE tenant_id=? AND embedding IS NOT NULL
                   ORDER BY RANDOM() LIMIT ?""",
                (tenant_id, sample_limit),
            ).fetchall()

        result: List[List[float]] = []
        for r in rows:
            try:
                emb = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                if emb:
                    result.append(emb)
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    def _save_score(
        self,
        tenant_id: str,
        result: SalienceResult,
        vector_id: Optional[int],
        fragment_id: Optional[int],
    ) -> None:
        now = time.time()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO memory_salience
                    (tenant_id, vector_id, fragment_id, score,
                     novelty, contradiction, user_boost,
                     decay_class, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    vector_id,
                    fragment_id,
                    result.score,
                    result.novelty,
                    result.contradiction,
                    result.user_boost,
                    result.decay_class,
                    now,
                ),
            )
            conn.commit()

    def get_salience(
        self, tenant_id: str, vector_id: Optional[int] = None
    ) -> Optional[SalienceResult]:
        """Retrieve the latest salience score for a vector."""
        with sqlite3.connect(str(self.db_path)) as conn:
            if vector_id:
                row = conn.execute(
                    """SELECT score, novelty, contradiction, user_boost, decay_class
                       FROM memory_salience
                       WHERE tenant_id=? AND vector_id=?
                       ORDER BY computed_at DESC LIMIT 1""",
                    (tenant_id, vector_id),
                ).fetchone()
            else:
                return None

        if row is None:
            return None

        one_shot = row[0] >= SALIENCE_ONE_SHOT
        return SalienceResult(
            score=row[0],
            novelty=row[1],
            contradiction=row[2],
            user_boost=row[3],
            trigger_one_shot=one_shot,
            decay_class=row[4],
        )

    def mark_user_boost(
        self, tenant_id: str, vector_id: int, boost: float
    ) -> None:
        """Explicit user feedback: pin / upvote a memory."""
        result = self.get_salience(tenant_id, vector_id=vector_id)
        if result is None:
            logger.warning("SalienceScorer: no existing score for vector %d", vector_id)
            return

        new_score = min(1.0, result.score + boost)
        new_user = min(1.0, result.user_boost + boost)
        one_shot = new_score >= SALIENCE_ONE_SHOT
        decay_class = (
            "permanent" if one_shot
            else "slow" if new_score >= SALIENCE_HIGH
            else "normal" if new_score >= SALIENCE_MEDIUM
            else "fast"
        )

        now = time.time()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO memory_salience
                    (tenant_id, vector_id, score, novelty, contradiction,
                     user_boost, decay_class, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    vector_id,
                    new_score,
                    result.novelty,
                    result.contradiction,
                    new_user,
                    decay_class,
                    now,
                ),
            )
            conn.commit()

        logger.info(
            "SalienceScorer: user boosted vector %d → score=%.3f class=%s",
            vector_id, new_score, decay_class,
        )
