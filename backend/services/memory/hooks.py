"""
MemoryHook – integrates the memory system into the GraphRuntime lifecycle.

This is the main integration point: after each agent execution, the hook
extracts intermediate reasoning products from GraphState and feeds them
into the attention window, fragment store, salience scorer, mismatch
detector, pattern completer, and hippocampal replay.

Call pattern:
    hook = MemoryHook(db_path=...)
    # called after each graph execution completes
    await hook.after_execution(state, session_id)

The hook owns the MemoryDecayScheduler and HippocampalReplay and starts them.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .attention_window import AttentionWindow
from .fragment_store import FragmentStore
from .salience import SalienceScorer
from .reencode import ReencodeBooster
from .decay import MemoryDecayScheduler
from .pattern_separator import PatternSeparator
from .mismatch_detector import MismatchDetector
from .hippocampal_replay import HippocampalReplay
from .pattern_completer import PatternCompleter

logger = logging.getLogger(__name__)


class MemoryHook:
    """After each Agent execution, persist reasoning products."""

    def __init__(
        self,
        db_path: Path,
        *,
        rag_engine: Optional[Any] = None,
        pattern_separator: Optional[PatternSeparator] = None,
        auto_start_decay: bool = False,
        auto_start_replay: bool = True,
        embedding_dim: int = 1536,
    ):
        self.db_path = Path(db_path)
        self._rag_engine = rag_engine

        # ---- sub-modules (stages 5-7) ----
        self.attention = AttentionWindow(db_path)
        self.fragments = FragmentStore(db_path)
        self.salience = SalienceScorer(db_path)
        self.reencode = ReencodeBooster(db_path)

        # ---- hippocampal subfields ----
        self.pattern_separator = pattern_separator or PatternSeparator(dim=embedding_dim)
        self.mismatch_detector = MismatchDetector()
        self.replay = HippocampalReplay(db_path)
        self.completer = PatternCompleter(db_path)

        # ---- background tasks ----
        self.decay = MemoryDecayScheduler(db_path)

        self._replay_auto_start = auto_start_replay  # deferred to lazy start
        if auto_start_decay:
            self.decay.start()

    def get_completer(self) -> PatternCompleter:
        """Return the PatternCompleter for CA3-style association spreading during retrieval."""
        return self.completer

    def get_separator(self) -> PatternSeparator:
        """Return the PatternSeparator for DG-style orthogonalization."""
        return self.pattern_separator

    # ------------------------------------------------------------------
    # main hook
    # ------------------------------------------------------------------


    async def _ensure_replay(self):
        """懒启动 Hippocampal Replay，确保在 running event loop 中执行。"""
        if self._replay_auto_start and not self.replay.is_running():
            try:
                self.replay.start()
            except Exception as e:
                self._log("replay", f"lazy start failed: {e}", "error")

    async def after_execution(
        self,
        state: Dict[str, Any],
        session_id: str = "default",
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Called after GraphRuntime finishes executing a chain.
        Extracts reasoning artifacts from state and persists them
        through the full hippocampal-subfield pipeline.
        """
        if tenant_id is None:
            tenant_id = state.get("tenant_id", "public")

        if session_id == "default":
            session_id = state.get("session_id", "default")

        results: Dict[str, Any] = {
            "attention_pushed": 0,
            "fragments_stored": 0,
            "salience_scored": 0,
            "mismatch_fused": 0,
            "reencode_boosted": 0,
            "patterns_completed": 0,
        }

        await self._ensure_replay()

        # ----------------------------------------------------------- ② DG: pattern-separate all embeddings
        # (applied transparently during _embed_text below)

        # ----------------------------------------------------------- ⑤a: attention window
        await self._persist_attention(state, tenant_id, session_id, results)

        # ----------------------------------------------------------- ⑤b: subconscious fragments
        await self._persist_fragments(state, tenant_id, session_id, results)

        # ----------------------------------------------------------- ⑥a: re-exposure → Hebbian re-encode
        await self._check_re_exposure(state, tenant_id, session_id, results)

        # ----------------------------------------------------------- ④ CA1: prediction-error salience
        await self._mismatch_salience(state, tenant_id, session_id, results)

        # ----------------------------------------------------------- ③ CA3: pattern completion (associative spread)
        await self._complete_patterns(state, tenant_id, session_id, results)

        return results

    # ------------------------------------------------------------------
    # sub-steps
    # ------------------------------------------------------------------

    async def _persist_attention(
        self,
        state: Dict[str, Any],
        tenant_id: str,
        session_id: str,
        results: Dict[str, Any],
    ) -> None:
        """Persist key reasoning products into attention window."""
        sources_to_persist = [
            ("answer", state.get("answer")),
            ("summary_chunk", state.get("summary_chunk")),
            ("map_result", state.get("map_result")),
        ]

        for source, text in sources_to_persist:
            if not text or not isinstance(text, str) or len(text.strip()) < 10:
                continue

            emb = await self._embed_text(text)
            if emb is None:
                continue

            content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

            self.attention.push(
                tenant_id=tenant_id,
                session_id=session_id,
                source=source,
                embedding=emb,
                content_hash=content_hash,
            )
            results["attention_pushed"] += 1

    async def _persist_fragments(
        self,
        state: Dict[str, Any],
        tenant_id: str,
        session_id: str,
        results: Dict[str, Any],
    ) -> None:
        """Save subconscious fragments: discarded reasoning branches."""
        agent_trace = state.get("agent_trace", {})
        internal_reasoning = agent_trace.get("internal_reasoning", [])

        if isinstance(internal_reasoning, list):
            for step in internal_reasoning:
                if not isinstance(step, dict):
                    continue
                counter = step.get("counterexample_check", "")
                consist = step.get("consistency_check", "")

                for frag_type, content in [
                    ("discarded_reasoning", counter),
                    ("discarded_reasoning", consist),
                ]:
                    if content and isinstance(content, str) and len(content) > 20:
                        emb = await self._embed_text(content[:4000])
                        self.fragments.store(
                            tenant_id=tenant_id,
                            session_id=session_id,
                            fragment_type=frag_type,
                            content=content[:4000],
                            embedding=emb,
                            metadata={"source": "internal_reasoning"},
                        )
                        results["fragments_stored"] += 1

        map_results = state.get("map_results", [])
        if isinstance(map_results, list):
            for mr in map_results:
                content = mr.get("summary", str(mr)) if isinstance(mr, dict) else str(mr)
                if content and len(content) > 20:
                    emb = await self._embed_text(content[:4000])
                    self.fragments.store(
                        tenant_id=tenant_id,
                        session_id=session_id,
                        fragment_type="map_intermediate",
                        content=content[:4000],
                        embedding=emb,
                    )
                    results["fragments_stored"] += 1

        filtered_chunks = state.get("retrieved_filtered", [])
        if isinstance(filtered_chunks, list):
            for chunk in filtered_chunks:
                content = chunk if isinstance(chunk, str) else str(chunk)
                if len(content) < 10:
                    continue
                emb = await self._embed_text(content[:4000])
                self.fragments.store(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    fragment_type="filtered_chunk",
                    content=content[:4000],
                    embedding=emb,
                )
                results["fragments_stored"] += 1

    async def _check_re_exposure(
        self,
        state: Dict[str, Any],
        tenant_id: str,
        session_id: str,
        results: Dict[str, Any],
    ) -> None:
        """Check if newly retrieved context overlaps attention window."""
        retrieved = state.get("retrieved", "")
        if not retrieved or not isinstance(retrieved, str) or len(retrieved) < 10:
            return

        emb = await self._embed_text(retrieved[:4000])
        if emb is None:
            return

        overlaps = self.attention.query_overlap(tenant_id, session_id, emb, top_k=3)
        if overlaps:
            logger.info(
                "MemoryHook: re-exposure detected – %d overlaps in attention window",
                len(overlaps),
            )
            best_entry, best_sim = overlaps[0]
            content_hash = best_entry.content_hash
            import sqlite3
            with sqlite3.connect(str(self.db_path)) as conn:
                rows = conn.execute(
                    "SELECT id FROM vectors WHERE content_hash=? AND tenant_id=? LIMIT 1",
                    (content_hash, tenant_id),
                ).fetchall()
                for r in rows:
                    vector_id = r[0]
                    self.reencode.boost_from_attention(
                        tenant_id=tenant_id,
                        vector_id=vector_id,
                        content_hash=content_hash,
                        activation_count=best_entry.activation_count,
                    )
                    results["reencode_boosted"] += 1

    async def _mismatch_salience(
        self,
        state: Dict[str, Any],
        tenant_id: str,
        session_id: str,
        results: Dict[str, Any],
    ) -> None:
        """
        CA1 prediction-error salience.
        Compares Agent's internal prediction vs actual retrieved context.
        """
        answer = state.get("answer", "")
        prediction_text = state.get("prediction", state.get("predicted_answer", ""))
        agent_trace = state.get("agent_trace", {})

        if isinstance(agent_trace, list):
            for step in reversed(agent_trace):
                if isinstance(step, dict) and "prediction" in step:
                    prediction_text = step["prediction"]
                    break

        if not answer or len(answer) < 10:
            return

        answer_emb = await self._embed_text(answer[:4000])
        if answer_emb is None:
            return

        # predicted embedding
        predicted_emb: Optional[List[float]] = None
        prediction_confidence: float = 0.5

        if prediction_text and isinstance(prediction_text, str) and len(prediction_text) > 10:
            predicted_emb = await self._embed_text(prediction_text[:4000])
            # confidence from trace if available
            prediction_confidence = state.get("prediction_confidence", 0.5)

        # salience with mismatch
        salience_result = await self.salience.score_with_mismatch(
            tenant_id=tenant_id,
            chunk_embedding=answer_emb,
            mismatch_detector=self.mismatch_detector,
            predicted_embedding=predicted_emb,
            prediction_confidence=prediction_confidence,
        )
        results["salience_scored"] = 1
        results["mismatch_fused"] = 1 if predicted_emb else 0

        if salience_result.trigger_one_shot:
            logger.info(
                "MemoryHook: CA1 ONE-SHOT triggered (score=%.3f class=%s)",
                salience_result.score,
                salience_result.decay_class,
            )
            content_hash = hashlib.sha256(answer.encode()).hexdigest()[:16]
            import sqlite3
            with sqlite3.connect(str(self.db_path)) as conn:
                rows = conn.execute(
                    "SELECT id FROM vectors WHERE content_hash=? AND tenant_id=? LIMIT 1",
                    (content_hash, tenant_id),
                ).fetchall()
                for r in rows:
                    self.reencode.boost_one_shot(
                        tenant_id=tenant_id,
                        vector_id=r[0],
                        salience_score=salience_result.score,
                    )

    async def _complete_patterns(
        self,
        state: Dict[str, Any],
        tenant_id: str,
        session_id: str,
        results: Dict[str, Any],
    ) -> None:
        """
        CA3 pattern completion: spread activation from retrieved chunks
        through association networks to assemble full context.
        """
        retrieved = state.get("retrieved", "")
        retrieved_list = state.get("retrieved_chunks", [])

        if not retrieved_list and retrieved:
            # chunk the retrieved text into pseudo-chunks for completion
            retrieved_list = [
                {"id": f"retrieved:{i}", "content": retrieved[i : i + 500]}
                for i in range(0, len(retrieved), 500)
            ][:3]

        if not retrieved_list:
            return

        completed = self.completer.complete(
            seed_chunks=retrieved_list,
            tenant_id=tenant_id,
            session_id=session_id,
            top_k=SPREAD_TOP_K,
        )
        results["patterns_completed"] = len(completed)

        # store completed patterns in state for downstream use
        state["completed_patterns"] = [
            {
                "seed_id": p.seed_chunk.get("id", ""),
                "associations": [
                    {"target": a.target_id, "type": a.edge_type, "weight": a.weight}
                    for a in p.associations
                ],
                "completed_text": p.completed_text,
            }
            for p in completed
        ]

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _embed_text(self, text: str) -> Optional[List[float]]:
        """
        Get embedding for text via RAG engine, then apply DG pattern separation.
        """
        raw_emb: Optional[List[float]] = None

        # try RAG engine first
        if self._rag_engine is not None and hasattr(self._rag_engine, "ai_router"):
            try:
                resp = await self._rag_engine.ai_router.embed(text[:4000])
                if isinstance(resp, dict) and "embedding" in resp:
                    emb = resp["embedding"]
                    if emb:
                        raw_emb = emb
            except Exception:
                logger.exception("MemoryHook: embedding failed, using hash fallback")

        # fallback: deterministic pseudo-embedding
        if raw_emb is None:
            h = hashlib.sha256(text.encode())
            raw_emb = []
            for i in range(0, 32, 4):
                val = int.from_bytes(h.digest()[i : i + 4], "big")
                raw_emb.append(val / (2**32))
            raw_emb = raw_emb[:128]
            # pad to dim
            if len(raw_emb) < self.pattern_separator.dim:
                raw_emb = raw_emb + [0.0] * (self.pattern_separator.dim - len(raw_emb))

        # ---- ② DG pattern separation ----
        separated = self.pattern_separator.separate(raw_emb)
        return separated

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Shutdown all background tasks."""
        self.decay.stop()
        self.replay.stop()
        logger.info("MemoryHook: stopped")

    # ------------------------------------------------------------------
    # external-facing API
    # ------------------------------------------------------------------

    def get_completer(self) -> PatternCompleter:
        """Expose PatternCompleter for use in RAG retrieval augmentation."""
        return self.completer

    def get_separator(self) -> PatternSeparator:
        """Expose PatternSeparator for pre-storage orthogonalization."""
        return self.pattern_separator

    def get_replay(self) -> HippocampalReplay:
        """Expose HippocampalReplay for introspection."""
        return self.replay


# re-export for convenience
SPREAD_TOP_K = 4
