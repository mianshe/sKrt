"""
PatternCompleter (CA3) – associative retrieval from partial cues.

The hippocampal CA3 region is a recurrent auto-associative network.
Given a partial cue ("大象") it can complete the full memory pattern
("长鼻子" → "不会跳" → "非洲象") by spreading activation along stored
associations.

Implementation:
  1. Retrieve top-k chunks via RAG (cosine search)
  2. For each retrieved chunk, spread activation through:
     a. Attention window — find temporally co-active entries
     b. Fragment store — resurrect semantically similar fragments
     c. Knowledge graph relations — traverse entity→entity edges
  3. Assemble the completed context and pass downstream

This turns RAG from "discrete chunk list" into "associative network".
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .attention_window import _cosine_similarity

logger = logging.getLogger(__name__)

# ---------- configuration ----------

SPREAD_DEPTH = 2                # how many hops of association to follow
SPREAD_TOP_K = 4                # max associations per hop
SPREAD_SIM_THRESHOLD = 0.72     # minimum cosine similarity to spread
TEMPORAL_WINDOW_SECONDS = 300   # temporal co-occurrence window


@dataclass
class AssociationEdge:
    source_id: str              # chunk_id, fragment_id, or entity_id
    target_id: str
    weight: float               # association strength 0..1
    edge_type: str              # "semantic" | "temporal" | "kg_relation"


@dataclass
class CompletedPattern:
    seed_chunk: Dict[str, Any]
    associations: List[AssociationEdge]
    completed_text: str         # assembled context


class PatternCompleter:
    """
    CA3-style pattern completion via association spreading.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    # ------------------------------------------------------------------
    # main API
    # ------------------------------------------------------------------

    def complete(
        self,
        seed_chunks: List[Dict[str, Any]],
        seed_embedding: Optional[List[float]] = None,
        tenant_id: str = "public",
        session_id: str = "default",
        top_k: int = 5,
    ) -> List[CompletedPattern]:
        """
        Given top-k retrieved chunks, spread activation and complete patterns.

        Returns one CompletedPattern per seed chunk.
        """
        results: List[CompletedPattern] = []
        visited: Set[str] = set()

        for chunk in seed_chunks:
            chunk_id = str(chunk.get("id", chunk.get("chunk_id", "")))
            chunk_text = str(chunk.get("content", chunk.get("text", "")))
            chunk_emb = chunk.get("embedding")

            if not chunk_id or not chunk_text:
                continue

            if chunk_id in visited:
                continue
            visited.add(chunk_id)

            # spread from this seed
            associations = self._spread(
                seed_id=chunk_id,
                seed_embedding=chunk_emb or seed_embedding,
                tenant_id=tenant_id,
                session_id=session_id,
                depth=SPREAD_DEPTH,
            )

            # assemble completed text
            parts = [chunk_text]
            for assoc in associations:
                # fetch the target text
                target_text = self._resolve_target_text(assoc.target_id, assoc.edge_type)
                if target_text:
                    parts.append(target_text)

            completed_text = "\n\n".join(parts)

            results.append(
                CompletedPattern(
                    seed_chunk=chunk,
                    associations=associations,
                    completed_text=completed_text,
                )
            )

        return results

    # ------------------------------------------------------------------
    # activation spreading
    # ------------------------------------------------------------------

    def _spread(
        self,
        seed_id: str,
        seed_embedding: Optional[List[float]],
        tenant_id: str,
        session_id: str,
        depth: int,
    ) -> List[AssociationEdge]:
        """
        BFS-style activation spreading from a seed through:
          1. Attention window (temporal co-occurrence)
          2. Fragment store (semantic similarity)
          3. Knowledge graph (entity relations)
        """
        edges: List[AssociationEdge] = []
        visited: Set[str] = {seed_id}
        frontier: List[Tuple[str, Optional[List[float]], int]] = [
            (seed_id, seed_embedding, 0)
        ]

        for _ in range(depth):
            next_frontier: List[Tuple[str, Optional[List[float]], int]] = []
            for current_id, current_emb, current_depth in frontier:
                new_edges = []

                # 1. temporal associations (attention window)
                temporal = self._spread_temporal(current_id, tenant_id, session_id)
                new_edges.extend(temporal)

                # 2. semantic associations (fragment store)
                if current_emb:
                    semantic = self._spread_semantic(
                        current_emb, tenant_id, session_id, visited
                    )
                    new_edges.extend(semantic)

                # 3. knowledge graph associations
                kg_edges = self._spread_kg(current_id, tenant_id)
                new_edges.extend(kg_edges)

                # add unique edges and enqueue targets for next hop
                for e in new_edges:
                    if e not in edges:
                        edges.append(e)
                    target = e.target_id
                    if target not in visited:
                        visited.add(target)
                        next_frontier.append((target, None, current_depth + 1))

            frontier = next_frontier[:SPREAD_TOP_K]

        return edges[:SPREAD_TOP_K * SPREAD_DEPTH]

    def _spread_temporal(
        self,
        seed_id: str,
        tenant_id: str,
        session_id: str,
    ) -> List[AssociationEdge]:
        """Find entries in attention window that were active near the same time."""
        with sqlite3.connect(str(self.db_path)) as conn:
            # find the seed's timestamp
            row = conn.execute(
                """SELECT last_activated_at FROM memory_attention_window
                   WHERE tenant_id=? AND session_id=? AND (
                       chunk_id=? OR content_hash=? OR id=?
                   ) LIMIT 1""",
                (tenant_id, session_id, seed_id, seed_id, _safe_int(seed_id)),
            ).fetchone()

            if not row or not row[0]:
                return []

            seed_time = row[0]
            t_min = seed_time - TEMPORAL_WINDOW_SECONDS
            t_max = seed_time + TEMPORAL_WINDOW_SECONDS

            rows = conn.execute(
                """SELECT id, content_hash, activation_count,
                          ABS(last_activated_at - ?) as time_diff
                   FROM memory_attention_window
                   WHERE tenant_id=? AND session_id=?
                     AND last_activated_at BETWEEN ? AND ?
                     AND content_hash != ?
                   ORDER BY time_diff ASC
                   LIMIT ?""",
                (seed_time, tenant_id, session_id, t_min, t_max, seed_id, SPREAD_TOP_K),
            ).fetchall()

        edges: List[AssociationEdge] = []
        for r in rows:
            time_weight = max(0.0, 1.0 - r[3] / TEMPORAL_WINDOW_SECONDS)
            edges.append(
                AssociationEdge(
                    source_id=seed_id,
                    target_id=r[1],  # content_hash as ID
                    weight=time_weight,
                    edge_type="temporal",
                )
            )
        return edges

    def _spread_semantic(
        self,
        embedding: List[float],
        tenant_id: str,
        session_id: str,
        visited: Set[str],
    ) -> List[AssociationEdge]:
        """Find semantically similar fragments in the fragment store."""
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT id, source_ref, embedding, content
                   FROM memory_fragments
                   WHERE tenant_id=? AND embedding IS NOT NULL
                   ORDER BY created_at DESC
                   LIMIT 200""",
                (tenant_id,),
            ).fetchall()

        scored: List[Tuple[AssociationEdge, float]] = []
        for r in rows:
            frag_id = f"fragment:{r[0]}"
            if frag_id in visited:
                continue
            try:
                frag_emb = json.loads(r[2]) if isinstance(r[2], str) else r[2]
            except (json.JSONDecodeError, TypeError):
                continue
            if not frag_emb:
                continue

            sim = _cosine_similarity(embedding, frag_emb)
            if sim >= SPREAD_SIM_THRESHOLD:
                scored.append(
                    (
                        AssociationEdge(
                            source_id="seed",
                            target_id=frag_id,
                            weight=sim,
                            edge_type="semantic",
                        ),
                        sim,
                    )
                )

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:SPREAD_TOP_K]]

    def _spread_kg(
        self,
        seed_id: str,
        tenant_id: str,
    ) -> List[AssociationEdge]:
        """Find related entities/relations via knowledge graph."""
        with sqlite3.connect(str(self.db_path)) as conn:
            # check if kg_relations table exists
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='kg_relations'"
            ).fetchone()
            if not exists:
                return []

            # find relations where the entity matches
            rows = conn.execute(
                """SELECT source_id, target_id, relation_type, weight
                   FROM kg_relations
                   WHERE tenant_id=?
                     AND (source_id LIKE ? OR target_id LIKE ?)
                   ORDER BY weight DESC
                   LIMIT ?""",
                (tenant_id, f"%{seed_id}%", f"%{seed_id}%", SPREAD_TOP_K),
            ).fetchall()

        edges: List[AssociationEdge] = []
        for r in rows:
            target = r[1] if r[0] in seed_id else r[0]
            edges.append(
                AssociationEdge(
                    source_id=seed_id,
                    target_id=target,
                    weight=r[3] if r[3] else 0.5,
                    edge_type="kg_relation",
                )
            )
        return edges

    def _resolve_target_text(self, target_id: str, edge_type: str) -> Optional[str]:
        """Fetch the actual text content for a target ID."""
        with sqlite3.connect(str(self.db_path)) as conn:
            if edge_type == "semantic" and target_id.startswith("fragment:"):
                fid = target_id.split(":", 1)[1]
                row = conn.execute(
                    "SELECT content FROM memory_fragments WHERE id=?",
                    (_safe_int(fid),),
                ).fetchone()
                return row[0][:500] if row else None

            if edge_type in ("temporal",):
                # content_hash → find in vectors or attention_window
                row = conn.execute(
                    """SELECT content FROM vectors WHERE content_hash=? LIMIT 1""",
                    (target_id,),
                ).fetchone()
                if row:
                    return row[0][:500] if row[0] else None

                # try attention_window
                row2 = conn.execute(
                    """SELECT chunk_id FROM memory_attention_window
                       WHERE content_hash=? LIMIT 1""",
                    (target_id,),
                ).fetchone()
                if row2:
                    return f"[AttentionWindow: {row2[0]}]"

        return None


def _safe_int(s: str) -> Optional[int]:
    try:
        return int(s)
    except (ValueError, TypeError):
        return None
