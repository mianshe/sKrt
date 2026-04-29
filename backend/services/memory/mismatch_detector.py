"""
MismatchDetector (CA1) – prediction-error-based salience.

The hippocampal CA1 region doesn't just tag "novel" items.  It first
generates a prediction of what should be there, then computes the
deviation between prediction and actual input.  Large mismatch →
high salience even for familiar-looking inputs.

Implementation:
  1. After internal_reasoning_step, the Agent produces a `predicted_answer`.
  2. The actual retrieved context produces a `real_answer`.
  3. We embed both, compute cosine distance, and amplify the salience
     proportionally to the mismatch magnitude.

This turns salience from a passive "is it novel?" into an active
"did it surprise the model?".

Fuses with: SalienceScorer
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Tuple

from .salience import SALIENCE_ONE_SHOT, SALIENCE_HIGH, SALIENCE_MEDIUM

logger = logging.getLogger(__name__)

# ---------- configuration ----------

MISMATCH_WEIGHT = 0.30          # weight of mismatch in final salience
MISMATCH_BOOST_FLOOR = 0.10     # minimum mismatch boost (always slightly surprised)
MISMATCH_SURPRISE_SPIKE = 0.75  # mismatch above this → "flashbulb" level
PREDICTION_CONFIDENCE_FLOOR = 0.05  # minimum weight for low-confidence predictions


class MismatchDetector:
    """
    CA1-style prediction error detector.

    Call `.compute_mismatch(predicted_vec, actual_vec, confidence)` to get
    a mismatch score and the recommended salience modifier.
    """

    def __init__(
        self,
        *,
        mismatch_weight: float = MISMATCH_WEIGHT,
        surprise_spike: float = MISMATCH_SURPRISE_SPIKE,
    ):
        self.mismatch_weight = mismatch_weight
        self.surprise_spike = surprise_spike

    def compute_mismatch(
        self,
        predicted_embedding: List[float],
        actual_embedding: List[float],
        prediction_confidence: float = 0.5,
    ) -> MismatchResult:
        """
        Compute prediction error from two embeddings.

        Args:
            predicted_embedding: Agent's "expected" output embedding.
            actual_embedding:   The real retrieved/observed embedding.
            prediction_confidence: 0..1 how confident the model was in its prediction.
        """
        # cosine distance = 1 - similarity
        sim = _cosine(predicted_embedding, actual_embedding)
        distance = 1.0 - sim

        # Effective mismatch = distance weighted by confidence.
        # High-confidence predictions that are wrong → larger surprise.
        # Low-confidence predictions that are wrong → expected, less surprise.
        confidence = max(PREDICTION_CONFIDENCE_FLOOR, prediction_confidence)
        effective_mismatch = distance * confidence
        effective_mismatch = max(MISMATCH_BOOST_FLOOR, effective_mismatch)

        # Classify surprise level
        if effective_mismatch >= self.surprise_spike:
            surprise_level = "flashbulb"
            salience_modifier = min(0.50, effective_mismatch * self.mismatch_weight)
        elif effective_mismatch >= 0.4:
            surprise_level = "high"
            salience_modifier = effective_mismatch * self.mismatch_weight * 0.7
        elif effective_mismatch >= 0.2:
            surprise_level = "moderate"
            salience_modifier = effective_mismatch * self.mismatch_weight * 0.4
        else:
            surprise_level = "expected"
            salience_modifier = 0.0

        return MismatchResult(
            distance=round(distance, 4),
            distance_raw=round(1.0 - sim, 4),
            confidence=round(confidence, 4),
            effective_mismatch=round(effective_mismatch, 4),
            surprise_level=surprise_level,
            salience_modifier=round(salience_modifier, 4),
        )

    def fuse_salience(
        self,
        static_salience: float,
        mismatch: MismatchResult,
    ) -> float:
        """
        Combine static salience (novelty + contradiction + user_boost)
        with mismatch-based surprise salience.

        Returns new 0..1 salience score.
        """
        # dynamic salience = (1 - mismatch_weight) * static + mismatch_weight * mismatch_mod
        fused = (
            (1.0 - self.mismatch_weight) * static_salience
            + self.mismatch_weight * mismatch.salience_modifier
        )
        # mismatch can only increase, never decrease
        fused = max(static_salience, fused)
        return min(1.0, fused)


class MismatchResult:
    __slots__ = (
        "distance",
        "distance_raw",
        "confidence",
        "effective_mismatch",
        "surprise_level",
        "salience_modifier",
    )

    def __init__(
        self,
        distance: float,
        distance_raw: float,
        confidence: float,
        effective_mismatch: float,
        surprise_level: str,
        salience_modifier: float,
    ):
        self.distance = distance
        self.distance_raw = distance_raw
        self.confidence = confidence
        self.effective_mismatch = effective_mismatch
        self.surprise_level = surprise_level
        self.salience_modifier = salience_modifier

    def __repr__(self) -> str:
        return (
            f"MismatchResult(distance={self.distance}, "
            f"level={self.surprise_level}, "
            f"modifier={self.salience_modifier})"
        )


# ====================================================================
#  utility
# ====================================================================


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
