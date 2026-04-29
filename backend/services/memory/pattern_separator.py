"""
PatternSeparator (DG) – Dentate Gyrus pattern separation.

In the hippocampus, the dentate gyrus maps semantically similar inputs
to orthogonalized representations so the brain can distinguish "today's
lunch" from "yesterday's lunch".  Without this, cosine-based novelty
detection is dull – two semantically close but factually different chunks
are misjudged as "not novel".

Implementation:
  A fixed (or evolvable) random projection matrix R of shape (D, D_padded)
  is applied to the embedding before storage.  A small amount of sparsity
  and a hashing step further decorrelate nearby vectors.

Usage:
  separator = PatternSeparator(dim=1536, sparsity=0.6, separation_strength=0.15)
  stored_vec = separator.separate(raw_embedding)
  # later, when searching:
  search_vec = separator.separate(query_embedding)
  # cosine over separated vectors now has higher discriminability.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------- configuration ----------

DEFAULT_SPARSITY = 0.60          # fraction of projection weights set to zero
DEFAULT_SEPARATION_STRENGTH = 0.15  # additive noise amplitude


class PatternSeparator:
    """
    DG-like pattern separation via random sparse projection.
    """

    def __init__(
        self,
        dim: int = 1536,
        *,
        sparsity: float = DEFAULT_SPARSITY,
        separation_strength: float = DEFAULT_SEPARATION_STRENGTH,
        seed: int = 42,
        state_path: Optional[Path] = None,
    ):
        self.dim = dim
        self.sparsity = sparsity
        self.separation_strength = separation_strength
        self.seed = seed
        self.state_path = state_path

        # projection matrix R: (dim, dim) – each column is a sparse random vector
        self._R: Optional[List[List[float]]] = None
        self._init_matrix()

    # ------------------------------------------------------------------
    # projection matrix
    # ------------------------------------------------------------------

    def _init_matrix(self) -> None:
        rng = random.Random(self.seed)
        R: List[List[float]] = []
        for _ in range(self.dim):
            col: List[float] = []
            for _ in range(self.dim):
                # sparse: only (1-sparsity) fraction non-zero
                if rng.random() < self.sparsity:
                    col.append(0.0)
                else:
                    col.append(rng.gauss(0.0, 1.0 / math.sqrt(self.dim)))
            # normalize column
            norm = math.sqrt(sum(x * x for x in col)) or 1.0
            col = [x / norm for x in col]
            R.append(col)
        self._R = R

    def _ensure_R(self) -> List[List[float]]:
        if self._R is None:
            self._init_matrix()
        assert self._R is not None
        return self._R

    # ------------------------------------------------------------------
    # separation
    # ------------------------------------------------------------------

    def separate(self, embedding: List[float]) -> List[float]:
        """
        Apply DG-style orthogonalizing projection + hashing noise.

        Returns a vector of same dimension, with increased pairwise
        discriminability.
        """
        if len(embedding) != self.dim:
            # pad or truncate
            if len(embedding) < self.dim:
                embedding = embedding + [0.0] * (self.dim - len(embedding))
            else:
                embedding = embedding[: self.dim]

        R = self._ensure_R()

        # sparse projection: v' = R^T · v
        projected = [0.0] * self.dim
        for i in range(self.dim):
            col = R[i]
            projected[i] = sum(embedding[j] * col[j] for j in range(self.dim))

        # re-normalize
        norm = math.sqrt(sum(x * x for x in projected)) or 1.0
        projected = [x / norm for x in projected]

        # hashing noise (content-addressable jitter)
        key = _hash_vector(embedding)
        rng = random.Random(key)
        noise = [rng.gauss(0.0, self.separation_strength) for _ in range(self.dim)]

        result = [projected[i] + noise[i] for i in range(self.dim)]

        # final normalize
        norm2 = math.sqrt(sum(x * x for x in result)) or 1.0
        return [x / norm2 for x in result]

    def separate_batch(self, embeddings: List[List[float]]) -> List[List[float]]:
        return [self.separate(e) for e in embeddings]

    # ------------------------------------------------------------------
    # discrimination check
    # ------------------------------------------------------------------

    def discrimination_gain(
        self,
        vec_a: List[float],
        vec_b: List[float],
        *,
        samples: int = 32,
    ) -> float:
        """
        Estimate how much the separation increases the contrast between
        two vectors.  Higher gain = better pattern separation.
        """
        sep_a = self.separate(vec_a)
        sep_b = self.separate(vec_b)

        sim_raw = _cosine(vec_a, vec_b)
        sim_sep = _cosine(sep_a, sep_b)

        return max(0.0, sim_raw - sim_sep)  # positive = separation lowered similarity


# ====================================================================
#  utilities
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


def _hash_vector(vec: List[float]) -> int:
    """Deterministic integer seed from a float vector."""
    h = hashlib.sha256()
    # quantize to 2 decimals to be robust
    quant = ",".join(f"{v:.2f}" for v in vec[:64])
    h.update(quant.encode())
    return int.from_bytes(h.digest()[:4], "big")
