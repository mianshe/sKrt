---
# AttentionWindow continuation – class implementation

class AttentionWindow:
    """Sliding attention window stored in SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._ensure_table()

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_attention_window (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id     TEXT    NOT NULL,
                    session_id    TEXT    NOT NULL DEFAULT 'default',
                    source        TEXT    NOT NULL DEFAULT 'unknown',
                    chunk_id      TEXT,
                    content_hash  TEXT    NOT NULL,
                    embedding     TEXT    NOT NULL,
                    activation_count INTEGER NOT NULL DEFAULT 1,
                    created_at    REAL    NOT NULL,
                    last_activated_at REAL NOT NULL,
                    decayed_at    REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_attn_win_tenant_session "
                "ON memory_attention_window(tenant_id, session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_attn_win_content_hash "
                "ON memory_attention_window(content_hash)"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # core API
    # ------------------------------------------------------------------

    def push(
        self,
        tenant_id: str,
        session_id: str,
        source: str,
        embedding: List[float],
        content_hash: str = "",
        chunk_id: Optional[str] = None,
    ) -> AttentionWindowEntry:
        """Insert a new entry; enforce window size (FIFO eviction)."""
        now = time.time()
        now_db = now  # store as float epoch

        with sqlite3.connect(str(self.db_path)) as conn:
            # check for semantic re-activation first
            reactivated = self._try_reactivate(
                conn, tenant_id, session_id, embedding, now_db
            )
            if reactivated is not None:
                return reactivated

            # insert new entry
            cur = conn.execute(
                """
                INSERT INTO memory_attention_window
                    (tenant_id, session_id, source, chunk_id, content_hash,
                     embedding, activation_count, created_at, last_activated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    source,
                    chunk_id,
                    content_hash,
                    json.dumps(embedding),
                    now_db,
                    now_db,
                ),
            )
            new_id = cur.lastrowid
            # enforce window size
            conn.execute(
                """
                DELETE FROM memory_attention_window
                WHERE id NOT IN (
                    SELECT id FROM memory_attention_window
                    WHERE tenant_id=? AND session_id=?
                    ORDER BY last_activated_at DESC LIMIT ?
                ) AND tenant_id=? AND session_id=?
                """,
                (tenant_id, session_id, WINDOW_SIZE, tenant_id, session_id),
            )
            conn.commit()

        return AttentionWindowEntry(
            id=new_id,
            tenant_id=tenant_id,
            session_id=session_id,
            source=source,
            chunk_id=chunk_id,
            content_hash=content_hash,
            embedding=embedding,
            activation_count=1,
            created_at=now,
            last_activated_at=now,
            decayed_at=None,
        )

    def query_overlap(
        self,
        tenant_id: str,
        session_id: str,
        embedding: List[float],
        top_k: int = 3,
    ) -> List[Tuple[AttentionWindowEntry, float]]:
        """Return top-k window entries with cosine similarity > threshold."""
        entries = self._load_window(tenant_id, session_id)

        scored: List[Tuple[AttentionWindowEntry, float]] = []
        for entry in entries:
            sim = _cosine_similarity(embedding, entry.embedding)
            if sim >= COSINE_REACTIVATION_THRESHOLD:
                scored.append((entry, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def get_activation_count(
        self, tenant_id: str, session_id: str, content_hash: str
    ) -> int:
        """Get total activation count for a content hash."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                """SELECT SUM(activation_count)
                   FROM memory_attention_window
                   WHERE tenant_id=? AND session_id=? AND content_hash=?""",
                (tenant_id, session_id, content_hash),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else 0

    def get_active_embeddings(
        self, tenant_id: str, session_id: str
    ) -> List[List[float]]:
        """Return all current window embeddings (for downstream use)."""
        entries = self._load_window(tenant_id, session_id)
        return [e.embedding for e in entries]

    def prune_stale(self) -> int:
        """Remove entries beyond TTL. Returns count removed."""
        cutoff = time.time() - WINDOW_TTL_SECONDS
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                "DELETE FROM memory_attention_window WHERE last_activated_at < ?",
                (cutoff,),
            )
            n = cur.rowcount
            conn.commit()
        if n:
            logger.info(f"AttentionWindow: pruned {n} stale entries (TTL={WINDOW_TTL_SECONDS}s)")
        return n

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _try_reactivate(
        self,
        conn: sqlite3.Connection,
        tenant_id: str,
        session_id: str,
        embedding: List[float],
        now: float,
    ) -> Optional[AttentionWindowEntry]:
        """If new embedding overlaps an existing one, bump activation & timestamp."""
        entries = self._load_window(tenant_id, session_id)
        best_sim = 0.0
        best_entry: Optional[AttentionWindowEntry] = None

        for entry in entries:
            sim = _cosine_similarity(embedding, entry.embedding)
            if sim >= COSINE_REACTIVATION_THRESHOLD and sim > best_sim:
                best_sim = sim
                best_entry = entry

        if best_entry is None:
            return None

        new_count = best_entry.activation_count + 1
        conn.execute(
            """UPDATE memory_attention_window
               SET activation_count=?, last_activated_at=?
               WHERE id=?""",
            (new_count, now, best_entry.id),
        )
        conn.commit()

        logger.debug(
            "AttentionWindow: reactivated entry %d (sim=%.3f, count=%d)",
            best_entry.id, best_sim, new_count,
        )
        best_entry.activation_count = new_count
        best_entry.last_activated_at = now
        return best_entry

    def _load_window(
        self,
        tenant_id: str,
        session_id: str,
    ) -> List[AttentionWindowEntry]:
        cutoff = time.time() - WINDOW_TTL_SECONDS
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT id, tenant_id, session_id, source, chunk_id,
                          content_hash, embedding, activation_count,
                          created_at, last_activated_at, decayed_at
                   FROM memory_attention_window
                   WHERE tenant_id=? AND session_id=?
                     AND last_activated_at >= ?
                   ORDER BY last_activated_at DESC""",
                (tenant_id, session_id, cutoff),
            ).fetchall()

        return [
            AttentionWindowEntry(
                id=r[0],
                tenant_id=r[1],
                session_id=r[2],
                source=r[3],
                chunk_id=r[4],
                content_hash=r[5],
                embedding=json.loads(r[6]) if isinstance(r[6], str) else r[6],
                activation_count=r[7],
                created_at=r[8],
                last_activated_at=r[9],
                decayed_at=r[10],
            )
            for r in rows
        ]

    def _reactivation_boost_factor(self, activation_count: int) -> float:
        """Compute boost weight from activation count (power-law scaling)."""
        if activation_count <= 1:
            return 1.0
        return 1.0 + math.log2(activation_count) * (REACTIVATION_BOOST_FACTOR - 1.0)


# ====================================================================
#  utility
# ====================================================================


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
