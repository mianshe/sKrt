import asyncio
import functools
import hashlib
import json
import os
import random
import re
import time
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.runtime_config import RuntimeConfig
from backend.services import knowledge_store
from backend.services.knowledge_store import insert_returning_id
from backend.services.document_parser import DocumentParser, ParsedDocument
from backend.services.free_ai_router import FreeAIRouter
from backend.services.pipeline import postgres_store
from backend.services.supabase_storage import SupabaseStorageConfig, download_to_file, parse_supabase_uri
from backend.services.r2_storage import R2StorageConfig, download_to_file as r2_download_to_file, parse_r2_uri
from backend.services.upload_load_control import log_ingestion_event
from backend.services import gpu_ocr_billing
from backend.services import ocr_token_billing

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except Exception:  # pragma: no cover - import fallback path
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter  # type: ignore
    except Exception:  # pragma: no cover - langchain may be optional in dev
        RecursiveCharacterTextSplitter = None  # type: ignore


@dataclass
class IngestionChunk:
    chunk_id: str
    section_path: str
    content: str
    chunk_hash: str
    page_num: int = 0
    chunk_type: str = "knowledge"


class UploadIngestionService:
    """
    Async ingestion service with chunk-level checkpoint and resume support.
    """

    def __init__(
        self,
        db_path: str,
        upload_dir: str,
        ai_router: FreeAIRouter,
        agent_chains: Optional[Any] = None,
        runtime_config: Optional[RuntimeConfig] = None,
        parser: Optional[DocumentParser] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        embed_batch_size: Optional[int] = None,
        max_retries: Optional[int] = None,
        base_retry_delay: Optional[float] = None,
        busy_timeout_ms: Optional[int] = None,
    ) -> None:
        self.runtime_config = runtime_config or RuntimeConfig.from_env()
        self.db_path = db_path
        self.upload_dir = upload_dir
        self.ai_router = ai_router
        self.agent_chains = agent_chains
        self.parser = parser or DocumentParser()
        default_chunk_size = self.runtime_config.llama_index.splitter_chunk_size
        default_chunk_overlap = self.runtime_config.llama_index.splitter_chunk_overlap
        default_batch_size = self.runtime_config.ingestion.embed_batch_size
        default_retries = self.runtime_config.ingestion.max_retries
        default_delay = self.runtime_config.ingestion.base_retry_delay_seconds
        default_busy_timeout = self.runtime_config.sqlite.busy_timeout_ms

        self.chunk_size = max(100, chunk_size if chunk_size is not None else default_chunk_size)
        self.chunk_overlap = max(0, chunk_overlap if chunk_overlap is not None else default_chunk_overlap)
        self.embed_batch_size = max(1, embed_batch_size if embed_batch_size is not None else default_batch_size)
        self.max_retries = max(1, max_retries if max_retries is not None else default_retries)
        self.base_retry_delay = max(0.1, base_retry_delay if base_retry_delay is not None else default_delay)
        self.busy_timeout_ms = max(1000, busy_timeout_ms if busy_timeout_ms is not None else default_busy_timeout)
        self.summary_enabled = self._as_bool(os.getenv("UPLOAD_SUMMARY_ENABLED", "1"), True)
        self.summary_granularity = (os.getenv("UPLOAD_SUMMARY_GRANULARITY", "detailed").strip().lower() or "detailed")
        self.summary_version = os.getenv("UPLOAD_SUMMARY_VERSION", "v1").strip() or "v1"
        self.summary_test_overwrite = self._as_bool(os.getenv("UPLOAD_SUMMARY_TEST_OVERWRITE", "1"), True)
        self.summary_test_dir = Path(self.upload_dir).parent.parent / "test" / "upload_summaries"
        self.summary_sections_limit = self._as_int(os.getenv("UPLOAD_SUMMARY_SECTIONS_LIMIT", "240"), 240, 20)
        self.summary_key_points_limit = self._as_int(os.getenv("UPLOAD_SUMMARY_KEY_POINTS_LIMIT", "12"), 12, 4)
        self.summary_keywords_limit = self._as_int(os.getenv("UPLOAD_SUMMARY_KEYWORDS_LIMIT", "18"), 18, 6)
        self.summary_evidence_limit = self._as_int(os.getenv("UPLOAD_SUMMARY_EVIDENCE_LIMIT", "6"), 6, 2)
        self.summary_conclusions_limit = self._as_int(os.getenv("UPLOAD_SUMMARY_CONCLUSIONS_LIMIT", "10"), 10, 3)
        self.summary_sentence_char_limit = self._as_int(os.getenv("UPLOAD_SUMMARY_SENTENCE_CHAR_LIMIT", "420"), 420, 160)
        self.postgres_url = (self.runtime_config.postgres.database_url or "").strip()
        self.staging_chunk_size = self._as_int(os.getenv("INGEST_SCAN_CHUNK_SIZE", str(self.chunk_size)), self.chunk_size, 120)
        self.staging_chunk_overlap = self._as_int(
            os.getenv("INGEST_SCAN_CHUNK_OVERLAP", str(self.chunk_overlap)), self.chunk_overlap, 0
        )
        self.staging_batch_size = self._as_int(
            os.getenv("INGEST_STAGE_BATCH_SIZE", str(self.embed_batch_size)), self.embed_batch_size, 1
        )

    def init_schema(self) -> None:
        if knowledge_store.use_postgres():
            return
        conn = self._conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS upload_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL DEFAULT 'public',
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    discipline TEXT NOT NULL,
                    document_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    phase TEXT NOT NULL DEFAULT 'queued',
                    document_id INTEGER,
                    total_chunks INTEGER NOT NULL DEFAULT 0,
                    processed_chunks INTEGER NOT NULL DEFAULT 0,
                    retries INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_ingest_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL DEFAULT 'public',
                    task_id INTEGER NOT NULL,
                    document_id INTEGER NOT NULL,
                    chunk_id TEXT NOT NULL,
                    chunk_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    vector_id INTEGER,
                    last_error TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(task_id, chunk_hash),
                    FOREIGN KEY(task_id) REFERENCES upload_tasks(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_doc_chunk ON vectors(document_id, chunk_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkpoints_task_status ON vector_ingest_checkpoints(task_id, status)"
            )
            columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(upload_tasks)").fetchall()]
            if "phase" not in columns:
                conn.execute("ALTER TABLE upload_tasks ADD COLUMN phase TEXT NOT NULL DEFAULT 'queued'")
            if "tenant_id" not in columns:
                conn.execute("ALTER TABLE upload_tasks ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'public'")
            columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(upload_tasks)").fetchall()]
            for col, ddl in (
                ("file_size_bytes", "ALTER TABLE upload_tasks ADD COLUMN file_size_bytes INTEGER NOT NULL DEFAULT 0"),
                ("page_count", "ALTER TABLE upload_tasks ADD COLUMN page_count INTEGER NOT NULL DEFAULT 0"),
                ("use_gpu_ocr", "ALTER TABLE upload_tasks ADD COLUMN use_gpu_ocr INTEGER NOT NULL DEFAULT 0"),
                ("ocr_mode", "ALTER TABLE upload_tasks ADD COLUMN ocr_mode TEXT NOT NULL DEFAULT 'standard'"),
                ("ocr_billing_client_id", "ALTER TABLE upload_tasks ADD COLUMN ocr_billing_client_id TEXT"),
                ("ocr_billing_exempt", "ALTER TABLE upload_tasks ADD COLUMN ocr_billing_exempt INTEGER NOT NULL DEFAULT 0"),
                ("ocr_provider", "ALTER TABLE upload_tasks ADD COLUMN ocr_provider TEXT"),
                ("ocr_call_units", "ALTER TABLE upload_tasks ADD COLUMN ocr_call_units INTEGER NOT NULL DEFAULT 0"),
                ("ocr_billable_tokens", "ALTER TABLE upload_tasks ADD COLUMN ocr_billable_tokens INTEGER NOT NULL DEFAULT 0"),
                ("extract_started_at", "ALTER TABLE upload_tasks ADD COLUMN extract_started_at TEXT"),
                ("extract_finished_at", "ALTER TABLE upload_tasks ADD COLUMN extract_finished_at TEXT"),
                ("index_started_at", "ALTER TABLE upload_tasks ADD COLUMN index_started_at TEXT"),
                ("index_finished_at", "ALTER TABLE upload_tasks ADD COLUMN index_finished_at TEXT"),
                ("extract_duration_sec", "ALTER TABLE upload_tasks ADD COLUMN extract_duration_sec REAL"),
                ("index_duration_sec", "ALTER TABLE upload_tasks ADD COLUMN index_duration_sec REAL"),
            ):
                if col not in columns:
                    conn.execute(ddl)
                    columns.append(col)
            cp_columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(vector_ingest_checkpoints)").fetchall()]
            if "tenant_id" not in cp_columns:
                conn.execute("ALTER TABLE vector_ingest_checkpoints ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'public'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_upload_tasks_tenant ON upload_tasks(tenant_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_checkpoints_tenant_task ON vector_ingest_checkpoints(tenant_id, task_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_timing_rollups (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    task_count INTEGER NOT NULL DEFAULT 0,
                    sum_extract_sec REAL NOT NULL DEFAULT 0,
                    sum_index_sec REAL NOT NULL DEFAULT 0,
                    sum_file_mb REAL NOT NULL DEFAULT 0,
                    sum_page_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL DEFAULT 'public',
                    document_id INTEGER NOT NULL UNIQUE,
                    granularity TEXT NOT NULL,
                    version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    artifact_path TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(document_id) REFERENCES documents(id)
                )
                """
            )
            ds_columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(document_summaries)").fetchall()]
            if "tenant_id" not in ds_columns:
                conn.execute("ALTER TABLE document_summaries ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'public'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_document_summaries_tenant_doc ON document_summaries(tenant_id, document_id)")
            # OCR 逐页缓存表（页面级别断点续传）
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ocr_page_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    page_num INTEGER NOT NULL,
                    ocr_text TEXT NOT NULL DEFAULT '',
                    engine TEXT NOT NULL DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(task_id, page_num)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ocr_page_cache ON ocr_page_cache(task_id, page_num)")
            # 上传限流：每分钟创建任务计数（无 Redis 时）
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS upload_throttle_minute (
                    tenant_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    minute_key TEXT NOT NULL,
                    created_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant_id, client_id, minute_key)
                )
                """
            )
            # vectors 表迁移：page_num + chunk_type 列
            vec_cols = [str(row["name"]) for row in conn.execute("PRAGMA table_info(vectors)").fetchall()]
            if "page_num" not in vec_cols:
                conn.execute("ALTER TABLE vectors ADD COLUMN page_num INTEGER NOT NULL DEFAULT 0")
            if "chunk_type" not in vec_cols:
                conn.execute("ALTER TABLE vectors ADD COLUMN chunk_type TEXT NOT NULL DEFAULT 'knowledge'")
            row = conn.execute("SELECT id FROM ingestion_timing_rollups WHERE id = 1").fetchone()
            if not row:
                conn.execute("INSERT INTO ingestion_timing_rollups (id, task_count) VALUES (1, 0)")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_task(
        self,
        filename: str,
        discipline: str,
        document_type: str,
        tenant_id: str,
        *,
        storage_basename: Optional[str] = None,
        ocr_mode: str = "standard",
        ocr_billing_client_id: Optional[str] = None,
        ocr_billing_exempt: bool = False,
    ) -> Dict[str, Any]:
        """filename: 展示用原始文件名；storage_basename: 磁盘上的唯一文件名（默认同 filename，易同名覆盖）。"""
        disk_name = storage_basename if storage_basename is not None else filename
        file_path = str(Path(self.upload_dir) / disk_name)
        if not Path(file_path).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        try:
            file_size_bytes = int(os.path.getsize(file_path))
        except OSError:
            file_size_bytes = 0

        conn = self._conn()
        try:
            task_id = insert_returning_id(
                conn,
                """
                INSERT INTO upload_tasks (tenant_id, filename, file_path, discipline, document_type, status, phase, file_size_bytes,
                    ocr_mode, ocr_billing_client_id, ocr_billing_exempt)
                VALUES (?, ?, ?, ?, ?, 'queued', 'queued', ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    filename,
                    file_path,
                    discipline,
                    document_type,
                    file_size_bytes,
                    str(ocr_mode or "standard"),
                    ocr_billing_client_id,
                    1 if ocr_billing_exempt else 0,
                ),
            )
            conn.commit()
            return self.get_task(task_id, tenant_id=tenant_id)
        finally:
            conn.close()

    def create_task_placeholder(
        self,
        filename: str,
        discipline: str,
        document_type: str,
        tenant_id: str,
        *,
        storage_basename: str,
        ocr_mode: str = "standard",
        ocr_billing_client_id: Optional[str] = None,
        ocr_billing_exempt: bool = False,
    ) -> Dict[str, Any]:
        """预签名直传：先占位 0 字节本地文件再建任务，客户端 PUT 完成后更新为 r2://。"""
        disk_name = storage_basename
        file_path = Path(self.upload_dir) / disk_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch(exist_ok=True)
        return self.create_task(
            filename=filename,
            discipline=discipline,
            document_type=document_type,
            tenant_id=tenant_id,
            storage_basename=storage_basename,
            ocr_mode=ocr_mode,
            ocr_billing_client_id=ocr_billing_client_id,
            ocr_billing_exempt=ocr_billing_exempt,
        )

    def get_task(self, task_id: int, tenant_id: str) -> Dict[str, Any]:
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT id, tenant_id, filename, file_path, discipline, document_type, status, phase, document_id,
                       total_chunks, processed_chunks, retries, error_message, created_at, updated_at,
                       file_size_bytes, page_count, use_gpu_ocr, ocr_mode, ocr_billing_client_id, ocr_billing_exempt,
                       ocr_provider, ocr_call_units, ocr_billable_tokens,
                       extract_started_at, extract_finished_at, index_started_at, index_finished_at,
                       extract_duration_sec, index_duration_sec
                FROM upload_tasks
                WHERE id = ? AND tenant_id = ?
                """,
                (task_id, tenant_id),
            ).fetchone()
            if not row:
                raise ValueError(f"任务不存在: {task_id}")
            return dict(row)
        finally:
            conn.close()

    def _get_task_any(self, task_id: int) -> Dict[str, Any]:
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT id, tenant_id, filename, file_path, discipline, document_type, status, phase, document_id,
                       total_chunks, processed_chunks, retries, error_message, created_at, updated_at,
                       file_size_bytes, page_count, use_gpu_ocr, ocr_mode, ocr_billing_client_id, ocr_billing_exempt,
                       ocr_provider, ocr_call_units, ocr_billable_tokens,
                       extract_started_at, extract_finished_at, index_started_at, index_finished_at,
                       extract_duration_sec, index_duration_sec
                FROM upload_tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"任务不存在: {task_id}")
            return dict(row)
        finally:
            conn.close()

    def list_tasks(self, limit: int = 50, tenant_id: str = "public") -> List[Dict[str, Any]]:
        cap = max(1, min(int(limit), 200))
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT id, tenant_id, filename, file_path, discipline, document_type, status, phase, document_id,
                       total_chunks, processed_chunks, retries, error_message, created_at, updated_at,
                       file_size_bytes, page_count, use_gpu_ocr, ocr_mode, ocr_provider, ocr_call_units, ocr_billable_tokens,
                       extract_started_at, extract_finished_at, index_started_at, index_finished_at,
                       extract_duration_sec, index_duration_sec
                FROM upload_tasks
                WHERE tenant_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (tenant_id, cap),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    _TASK_TS_COLUMNS = frozenset(
        {"extract_started_at", "extract_finished_at", "index_started_at", "index_finished_at"}
    )

    @staticmethod
    def _parse_iso_ts(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            s = str(value).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        except Exception:
            return None

    def _set_task_timestamp(self, task_id: int, column: str, value: str) -> None:
        if column not in self._TASK_TS_COLUMNS:
            raise ValueError(f"invalid timestamp column: {column}")
        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE upload_tasks SET {column} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (value, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _set_task_timestamp_if_missing(self, task_id: int, column: str, value: str) -> None:
        task = self._get_task_any(task_id)
        if task.get(column):
            return
        self._set_task_timestamp(task_id, column, value)

    def _reset_ingestion_timestamps(self, task_id: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE upload_tasks
                SET extract_started_at = NULL,
                    extract_finished_at = NULL,
                    index_started_at = NULL,
                    index_finished_at = NULL,
                    extract_duration_sec = NULL,
                    index_duration_sec = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def _ensure_task_file_size(self, task_id: int) -> None:
        task = self._get_task_any(task_id)
        if int(task.get("file_size_bytes") or 0) > 0:
            return
        fp = task.get("file_path")
        if not fp:
            return
        if (
            str(fp).startswith("r2://")
            or str(fp).startswith("supabase://")
            or str(fp).startswith("http://")
            or str(fp).startswith("https://")
        ):
            return
        try:
            sz = int(os.path.getsize(str(fp)))
        except OSError:
            return
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE upload_tasks SET file_size_bytes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (sz, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def update_task_file_path(self, task_id: int, file_path: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE upload_tasks SET file_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (str(file_path), int(task_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def update_task_file_size_bytes(self, task_id: int, size: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE upload_tasks SET file_size_bytes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (max(0, int(size)), int(task_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def update_task_use_gpu_ocr(self, task_id: int, use_gpu_ocr: bool) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE upload_tasks SET use_gpu_ocr = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (1 if use_gpu_ocr else 0, int(task_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def update_task_ocr_mode(self, task_id: int, ocr_mode: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE upload_tasks SET ocr_mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (str(ocr_mode or "standard"), int(task_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def update_task_ocr_usage(self, task_id: int, provider: str, call_units: int, billable_tokens: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE upload_tasks
                SET ocr_provider = ?, ocr_call_units = ?, ocr_billable_tokens = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(provider or ""), max(0, int(call_units)), max(0, int(billable_tokens)), int(task_id)),
            )
            conn.commit()
        finally:
            conn.close()

    async def _resolve_local_file_for_task(self, task_id: int, task: Dict[str, Any]) -> str:
        fp = str(task.get("file_path") or "")
        if not fp:
            raise FileNotFoundError("任务缺少 file_path")

        if not (fp.startswith("supabase://") or fp.startswith("http://") or fp.startswith("https://")):
            if not Path(fp).exists():
                raise FileNotFoundError(f"文件不存在: {fp}")
            return fp

        r2 = parse_r2_uri(fp)
        if r2:
            cfg = R2StorageConfig.from_env()
            if not cfg:
                raise RuntimeError("检测到 r2:// file_path，但未配置 R2_ENDPOINT / R2_BUCKET / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY")
            bucket, key = r2
            cache_dir = Path(self.upload_dir) / "_cache"
            dest = cache_dir / f"task_{int(task_id)}_{Path(key).name}"
            if dest.exists() and dest.stat().st_size > 0:
                return str(dest)
            r2_download_to_file(cfg, bucket=bucket, key=key, dest_path=dest)
            return str(dest)

        sb = parse_supabase_uri(fp)
        if sb:
            cfg = SupabaseStorageConfig.from_env()
            if not cfg:
                raise RuntimeError(
                    "检测到 supabase:// file_path，但未配置 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_STORAGE_BUCKET"
                )
            bucket, key = sb
            cache_dir = Path(self.upload_dir) / "_cache"
            dest = cache_dir / f"task_{int(task_id)}_{Path(key).name}"
            if dest.exists() and dest.stat().st_size > 0:
                return str(dest)
            await download_to_file(cfg, bucket=bucket, key=key, dest_path=dest)
            return str(dest)

        # 纯 URL（不一定是 supabase public url）：下载后解析
        cache_dir = Path(self.upload_dir) / "_cache"
        dest = cache_dir / f"task_{int(task_id)}_{Path(fp).name or 'remote'}"
        if dest.exists() and dest.stat().st_size > 0:
            return str(dest)
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.get(fp)
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
        return str(dest)

    def _update_task_page_count_from_parsed(self, task_id: int, parsed: ParsedDocument) -> None:
        raw = parsed.metadata.get("pdf_page_count")
        if not isinstance(raw, int) or raw <= 0:
            return
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE upload_tasks SET page_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (raw, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _store_phase_durations(self, task_id: int) -> None:
        task = self._get_task_any(task_id)
        es = self._parse_iso_ts(task.get("extract_started_at"))
        ef = self._parse_iso_ts(task.get("extract_finished_at"))
        ist = self._parse_iso_ts(task.get("index_started_at"))
        iend = self._parse_iso_ts(task.get("index_finished_at"))
        ex_sec: Optional[float] = None
        in_sec: Optional[float] = None
        if es and ef:
            ex_sec = max(0.0, (ef - es).total_seconds())
        if ist and iend:
            in_sec = max(0.0, (iend - ist).total_seconds())
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE upload_tasks
                SET extract_duration_sec = ?, index_duration_sec = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (ex_sec, in_sec, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _close_timestamps_on_failure(self, task_id: int) -> None:
        task = self._get_task_any(task_id)
        now = self._now_iso()
        if task.get("extract_started_at") and not task.get("extract_finished_at"):
            self._set_task_timestamp(task_id, "extract_finished_at", now)
        if task.get("index_started_at") and not task.get("index_finished_at"):
            self._set_task_timestamp(task_id, "index_finished_at", now)
        self._store_phase_durations(task_id)

    def append_rollup_for_completed_task(self, task_id: int) -> None:
        task = self._get_task_any(task_id)
        if str(task.get("status")) != "completed":
            return
        ex = task.get("extract_duration_sec")
        ix = task.get("index_duration_sec")
        if ex is None:
            return
        try:
            ex_f = float(ex)
            ix_f = float(ix) if ix is not None else 0.0
        except (TypeError, ValueError):
            return
        mb = float(int(task.get("file_size_bytes") or 0)) / (1024.0 * 1024.0)
        pc = int(task.get("page_count") or 0)
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE ingestion_timing_rollups
                SET task_count = task_count + 1,
                    sum_extract_sec = sum_extract_sec + ?,
                    sum_index_sec = sum_index_sec + ?,
                    sum_file_mb = sum_file_mb + ?,
                    sum_page_count = sum_page_count + ?
                WHERE id = 1
                """,
                (ex_f, ix_f, mb, pc),
            )
            conn.commit()
        finally:
            conn.close()

    def get_rollup_metrics(self) -> Dict[str, Any]:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM ingestion_timing_rollups WHERE id = 1").fetchone()
            if not row:
                return {
                    "rollup_task_count": 0,
                    "avg_extract_sec_per_mb": None,
                    "avg_extract_sec_per_page": None,
                    "sum_extract_sec": 0.0,
                    "sum_index_sec": 0.0,
                }
            d = dict(row)
            n = int(d.get("task_count") or 0)
            sum_ex = float(d.get("sum_extract_sec") or 0)
            sum_mb = float(d.get("sum_file_mb") or 0)
            sum_pc = int(d.get("sum_page_count") or 0)
            return {
                "rollup_task_count": n,
                "avg_extract_sec_per_mb": (sum_ex / sum_mb) if sum_mb > 0 else None,
                "avg_extract_sec_per_page": (sum_ex / sum_pc) if sum_pc > 0 else None,
                "sum_extract_sec": sum_ex,
                "sum_index_sec": float(d.get("sum_index_sec") or 0),
            }
        finally:
            conn.close()

    def compute_task_progress(self, task: Dict[str, Any]) -> Dict[str, int]:
        """双进度：文本提取（解析+分块准备）与知识库索引（向量化写入）。"""
        phase = str(task.get("phase") or "queued")
        status = str(task.get("status") or "queued")
        total_chunks = int(task.get("total_chunks", 0) or 0)
        processed_chunks = int(task.get("processed_chunks", 0) or 0)
        index_pct = int((processed_chunks / total_chunks) * 100) if total_chunks > 0 else 0
        if status == "completed":
            index_pct = 100

        extract_pct = 0
        if status == "completed" or phase in {"completed", "indexing"}:
            extract_pct = 100
        elif phase == "splitting":
            extract_pct = 95
        elif phase == "parsing":
            es = self._parse_iso_ts(task.get("extract_started_at"))
            if es:
                now = datetime.now(timezone.utc)
                if es.tzinfo is None:
                    es = es.replace(tzinfo=timezone.utc)
                elapsed = max(0.0, (now - es).total_seconds())
                extract_pct = min(70, int(70 * min(1.0, elapsed / 600.0)))
            else:
                extract_pct = 50
        elif phase == "queued":
            extract_pct = 0
        elif phase == "failed":
            extract_pct = 100 if task.get("extract_finished_at") else 50

        overall = int(round(0.35 * extract_pct + 0.65 * index_pct))
        return {
            "extract_progress_percent": extract_pct,
            "index_progress_percent": index_pct,
            "overall_progress_percent": overall,
        }

    @staticmethod
    def task_timing_snapshot(task: Dict[str, Any]) -> Dict[str, Any]:
        fs = int(task.get("file_size_bytes") or 0)
        pc = int(task.get("page_count") or 0)
        mb = fs / (1024.0 * 1024.0) if fs > 0 else 0.0
        ex = task.get("extract_duration_sec")
        ix = task.get("index_duration_sec")
        sec_per_mb: Optional[float] = None
        sec_per_page: Optional[float] = None
        try:
            if ex is not None and mb > 0:
                sec_per_mb = float(ex) / mb
            if ex is not None and pc > 0:
                sec_per_page = float(ex) / float(pc)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        return {
            "file_size_bytes": fs,
            "page_count": pc,
            "extract_duration_sec": ex,
            "index_duration_sec": ix,
            "sec_per_mb_extract": sec_per_mb,
            "sec_per_page_extract": sec_per_page,
        }

    async def run_task(self, task_id: int, tenant_id: str) -> Dict[str, Any]:
        task = self.get_task(task_id, tenant_id=tenant_id)
        if task["status"] == "completed":
            document_id = int(task.get("document_id") or 0)
            if document_id > 0 and not self.get_summary_by_document_id(document_id, tenant_id=tenant_id):
                self._backfill_summary_from_stored_chunks(document_id=document_id, task=task)
            return task
        if bool(self.runtime_config.capacity.pause_on_hard_limit):
            free_bytes = int(shutil.disk_usage(Path(self.upload_dir)).free)
            hard = int(self.runtime_config.capacity.disk_hard_limit_bytes)
            if hard > 0 and free_bytes <= hard:
                self._update_task_status(
                    task_id,
                    "failed",
                    phase="paused_capacity",
                    error_message=f"容量保护触发，剩余空间不足（free={free_bytes} <= hard={hard}）",
                )
                return self.get_task(task_id, tenant_id=tenant_id)

        self._ensure_task_file_size(task_id)
        self._reset_ingestion_timestamps(task_id)
        try:
            log_ingestion_event(
                "task_run_start",
                task_id=task_id,
                tenant_id=tenant_id,
                status=str(task.get("status")),
                phase=str(task.get("phase")),
            )
            # 单次 parse：此前 _prepare_document_record 与 _build_chunks 各 parse 一次，
            # 扫描 PDF 会重复跑 PaddleOCR，易 OOM/原生崩溃导致进程退出。
            self._set_task_timestamp(task_id, "extract_started_at", self._now_iso())
            self._update_task_status(task_id, "running", phase="parsing", error_message=None)
            # 注入 OCR 页级缓存（供断点续传）
            self.parser.set_ocr_cache(task_id)
            local_fp = await self._resolve_local_file_for_task(task_id, task)
            fs = int(task.get("file_size_bytes") or 0)
            _ = fs  # file_size_bytes kept for stats/debug; 外部 OCR 额度由上游扫描判定与扣减
            ocr_mode = str(task.get("ocr_mode") or "standard").strip().lower() or "standard"
            if ocr_mode == "complex_layout":
                ocr_override = "glm-ocr"
            elif ocr_mode == "standard":
                ocr_override = "local"
            else:
                ocr_override = None
            parse_fn = functools.partial(
                self.parser.parse, local_fp, task["document_type"], ocr_engine_override=ocr_override
            )
            parsed = await asyncio.get_running_loop().run_in_executor(None, parse_fn)

            try:
                bill = int(parsed.metadata.get("ocr_billable_api_calls") or 0)
            except (TypeError, ValueError):
                bill = 0
            try:
                bill_tokens = int(parsed.metadata.get("ocr_billable_tokens") or 0)
            except (TypeError, ValueError):
                bill_tokens = 0
            try:
                call_units = int(parsed.metadata.get("ocr_call_billing_units") or 0)
            except (TypeError, ValueError):
                call_units = 0
            provider = str(parsed.metadata.get("ocr_provider") or parsed.metadata.get("ocr_engine") or "").strip()
            self.update_task_ocr_usage(task_id, provider=provider, call_units=call_units + bill, billable_tokens=bill_tokens)

            use_gpu_flag = bool(int(task.get("use_gpu_ocr") or 0))
            exempt = bool(int(task.get("ocr_billing_exempt") or 0))
            bill_cid = (str(task.get("ocr_billing_client_id") or "").strip() or str(tenant_id))
            if use_gpu_flag and not exempt and bill > 0:
                bal = gpu_ocr_billing.get_paid_calls_balance(str(tenant_id), bill_cid)
                if bal < bill:
                    raise RuntimeError(f"外部 OCR 次数不足（解析需扣 {bill} 次，当前余额 {bal}）")
                gpu_ocr_billing.add_paid_calls(str(tenant_id), bill_cid, -bill, "consume_ocr_actual_api_calls")
            if ocr_mode == "standard" and not exempt and call_units > 0:
                bal = gpu_ocr_billing.get_paid_calls_balance(str(tenant_id), bill_cid)
                if bal < call_units:
                    raise RuntimeError(f"普通 OCR 次数不足（解析需扣 {call_units} 次，当前余额 {bal}）")
                gpu_ocr_billing.add_paid_calls(
                    str(tenant_id), bill_cid, -call_units, f"consume_standard_ocr_calls:{Path(local_fp).name}"
                )
            if ocr_mode == "complex_layout" and not exempt and bill_tokens > 0:
                bal = ocr_token_billing.get_token_balance(str(tenant_id), bill_cid)
                if bal < bill_tokens:
                    raise RuntimeError(f"复杂版式 OCR token 不足（解析需扣 {bill_tokens}，当前余额 {bal}）")
                ocr_token_billing.add_tokens(
                    str(tenant_id), bill_cid, -bill_tokens, f"consume_glm_ocr_tokens:{Path(local_fp).name}"
                )

            self._update_task_page_count_from_parsed(task_id, parsed)

            document_id = task.get("document_id")
            if not document_id:
                document_id = self._prepare_document_record_from_parsed(task, parsed)
                self._update_task_document(task_id, int(document_id))

            self._update_task_status(task_id, "running", phase="splitting", error_message=None)
            chunks = await self._build_chunks_from_parsed(parsed, task["document_type"])
            self._push_scan_chunks_to_pg_staging(
                tenant_id=tenant_id, task_id=task_id, document_id=int(document_id), chunks=chunks
            )
            self._update_total_chunks(task_id, len(chunks))

            now = self._now_iso()
            self._set_task_timestamp(task_id, "extract_finished_at", now)
            self._set_task_timestamp(task_id, "index_started_at", now)
            self._update_task_status(task_id, "running", phase="indexing", error_message=None)
            await self._embed_and_write_with_checkpoint(
                task_id=task_id, document_id=int(document_id), chunks=chunks, tenant_id=tenant_id
            )
            self._generate_and_store_summary(
                document_id=int(document_id),
                task=task,
                parsed=parsed,
                chunks=chunks,
            )

            self._set_task_timestamp(task_id, "index_finished_at", self._now_iso())
            self._store_phase_durations(task_id)
            self._update_task_status(task_id, "completed", phase="completed", error_message=None)
            self.append_rollup_for_completed_task(task_id)
            log_ingestion_event("task_run_ok", task_id=task_id, tenant_id=tenant_id)
        except Exception as exc:
            log_ingestion_event(
                "task_run_failed",
                task_id=task_id,
                tenant_id=tenant_id,
                error=str(exc)[:1200],
            )
            retries = int(task.get("retries", 0)) + 1
            self._close_timestamps_on_failure(task_id)
            self._update_task_status(task_id, "failed", phase="failed", error_message=str(exc), retries=retries)
            raise
        return self.get_task(task_id, tenant_id=tenant_id)

    async def resume_task(self, task_id: int, tenant_id: str) -> Dict[str, Any]:
        task = self.get_task(task_id, tenant_id=tenant_id)
        if task["status"] not in {"failed", "queued", "running"}:
            return task
        self._update_task_status(task_id, "queued", phase="queued", error_message=None)
        return await self.run_task(task_id, tenant_id=tenant_id)

    async def _embed_and_write_with_checkpoint(
        self, task_id: int, document_id: int, chunks: List[IngestionChunk], tenant_id: str
    ) -> None:
        if not chunks:
            self._update_progress(task_id, 0)
            return

        chunk_batches = self._chunk_batches(chunks, self.embed_batch_size)
        processed = self._count_done_checkpoints(task_id)
        self._update_progress(task_id, processed)

        for batch in chunk_batches:
            for chunk in batch:
                checkpoint = self._get_checkpoint(task_id, chunk.chunk_hash)
                if checkpoint and checkpoint.get("status") == "done":
                    continue
                await self._process_chunk(task_id=task_id, document_id=document_id, chunk=chunk, tenant_id=tenant_id)

            processed = self._count_done_checkpoints(task_id)
            self._update_progress(task_id, processed)

    async def _process_chunk(self, task_id: int, document_id: int, chunk: IngestionChunk, tenant_id: str) -> None:
        self._upsert_checkpoint(
            tenant_id=tenant_id,
            task_id=task_id,
            document_id=document_id,
            chunk_id=chunk.chunk_id,
            chunk_hash=chunk.chunk_hash,
            status="running",
            error_message=None,
        )
        attempt = 0
        last_error = ""
        while attempt < self.max_retries:
            attempt += 1
            self._increase_checkpoint_attempt(task_id, chunk.chunk_hash)
            try:
                embedding_resp = await self.ai_router.embed(chunk.content)
                embedding = embedding_resp.get("embedding", [])
                if not embedding:
                    raise RuntimeError("empty embedding")
                vector_id = self._write_vector_if_missing(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    chunk_id=chunk.chunk_id,
                    content=chunk.content,
                    section_path=chunk.section_path,
                    embedding=embedding,
                    page_num=chunk.page_num,
                    chunk_type=chunk.chunk_type,
                )
                self._push_vector_chunk_to_pg_staging(
                    tenant_id=tenant_id,
                    task_id=task_id,
                    document_id=document_id,
                    chunk=chunk,
                    embedding=embedding,
                    vector_id=vector_id,
                )
                self._upsert_checkpoint(
                    tenant_id=tenant_id,
                    task_id=task_id,
                    document_id=document_id,
                    chunk_id=chunk.chunk_id,
                    chunk_hash=chunk.chunk_hash,
                    status="done",
                    vector_id=vector_id,
                    error_message=None,
                )
                return
            except Exception as exc:
                last_error = str(exc)
                self._upsert_checkpoint(
                    tenant_id=tenant_id,
                    task_id=task_id,
                    document_id=document_id,
                    chunk_id=chunk.chunk_id,
                    chunk_hash=chunk.chunk_hash,
                    status="failed",
                    error_message=last_error,
                )
                if attempt >= self.max_retries:
                    break
                self._sleep_with_backoff(attempt)
        raise RuntimeError(f"chunk={chunk.chunk_id} ingestion failed after retries: {last_error}")

    def _prepare_document_record_from_parsed(self, task: Dict[str, Any], parsed: ParsedDocument) -> int:
        merged_meta = dict(parsed.metadata)
        merged_meta["discipline"] = task["discipline"]
        merged_meta["embedding_model"] = self.ai_router.get_active_embedding_model_id()
        tenant_id = str(task.get("tenant_id", "public") or "public")
        conn = self._conn()
        try:
            doc_id = insert_returning_id(
                conn,
                """
                INSERT INTO documents (tenant_id, filename, title, discipline, document_type, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    task["filename"],
                    parsed.metadata.get("title", task["filename"]),
                    task["discipline"],
                    task["document_type"],
                    json.dumps(merged_meta, ensure_ascii=False),
                ),
            )
            conn.commit()
            return doc_id
        finally:
            conn.close()

    async def _build_chunks_from_parsed(self, parsed: ParsedDocument, document_type: str) -> List[IngestionChunk]:
        from backend.services.chunker import DocumentChunker
        text = parsed.text or ""
        title = str(parsed.metadata.get("title", ""))
        # 如果文本含 [[PAGE:N]] 标记，优先用 DocumentChunker（保留页码信息）
        if "[[PAGE:" in text:
            chunker = DocumentChunker(chunk_size=self.chunk_size, overlap=self.chunk_overlap)
            raw_chunks = chunker.chunk_document(text, document_type, title)
            chunks: List[IngestionChunk] = []
            for index, payload in enumerate(raw_chunks, start=1):
                content = (payload.get("content") or "").strip()
                if not content:
                    continue
                pg = int(payload.get("page_num", 0) or 0)
                chunk_hash = hashlib.sha256(
                    f"{document_type}|{payload.get('section_path', '')}|{index}|{content}".encode("utf-8")
                ).hexdigest()
                chunks.append(
                    IngestionChunk(
                        chunk_id=f"lc-{index}",
                        section_path=payload.get("section_path", f"section/{index}"),
                        content=content,
                        chunk_hash=chunk_hash,
                        page_num=pg,
                        chunk_type=payload.get("chunk_type", "knowledge"),
                    )
                )
            return chunks
        # 无页标记：原有路径
        sections = await self._split_sections(text, document_type)
        chunks = []
        for index, payload in enumerate(sections, start=1):
            content = payload["content"].strip()
            if not content:
                continue
            chunk_hash = hashlib.sha256(
                f"{document_type}|{payload['section_path']}|{index}|{content}".encode("utf-8")
            ).hexdigest()
            ct = DocumentChunker._classify_chunk(content, payload["section_path"], document_type)
            chunks.append(
                IngestionChunk(
                    chunk_id=f"lc-{index}",
                    section_path=payload["section_path"],
                    content=content,
                    chunk_hash=chunk_hash,
                    chunk_type=ct,
                )
            )
        return chunks

    async def _split_sections(self, text: str, document_type: str) -> List[Dict[str, str]]:
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []
        if self.agent_chains is not None:
            try:
                sections = await self.agent_chains.run_ingestion_graph(
                    text=normalized,
                    document_type=document_type,
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                )
                if isinstance(sections, list) and sections:
                    return sections
            except Exception:
                pass

        if RecursiveCharacterTextSplitter is None:
            return self._fallback_split(normalized, document_type)

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", ".", " ", ""],
            length_function=len,
            is_separator_regex=False,
        )
        contents = splitter.split_text(normalized)
        output: List[Dict[str, str]] = []
        for idx, content in enumerate(contents, start=1):
            content = content.strip()
            if not content:
                continue
            output.append({"section_path": f"{document_type}/chunk/{idx}", "content": content})
        return output

    def _fallback_split(self, text: str, document_type: str) -> List[Dict[str, str]]:
        pieces: List[Dict[str, str]] = []
        start = 0
        idx = 1
        while start < len(text):
            end = min(len(text), start + self.chunk_size)
            window = text[start:end]
            split_at = max(window.rfind("\n"), window.rfind("。"), window.rfind("."))
            if split_at > int(self.chunk_size * 0.6):
                end = start + split_at + 1
                window = text[start:end]
            cleaned = window.strip()
            if cleaned:
                pieces.append({"section_path": f"{document_type}/chunk/{idx}", "content": cleaned})
                idx += 1
            if end >= len(text):
                break
            start = max(0, end - self.chunk_overlap)
        return pieces

    def _write_vector_if_missing(
        self,
        tenant_id: str,
        document_id: int,
        chunk_id: str,
        content: str,
        section_path: str,
        embedding: List[float],
        page_num: int = 0,
        chunk_type: str = "knowledge",
    ) -> int:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT id FROM vectors WHERE tenant_id = ? AND document_id = ? AND chunk_id = ? LIMIT 1",
                (tenant_id, document_id, chunk_id),
            ).fetchone()
            if row:
                return int(row["id"])
            vid = insert_returning_id(
                conn,
                """
                INSERT INTO vectors (tenant_id, document_id, chunk_id, content, section_path, embedding, page_num, chunk_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tenant_id, document_id, chunk_id, content, section_path, json.dumps(embedding), page_num, chunk_type),
            )
            conn.commit()
            return vid
        finally:
            conn.close()

    def _get_checkpoint(self, task_id: int, chunk_hash: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT id, status, attempt_count, vector_id, last_error
                FROM vector_ingest_checkpoints
                WHERE task_id = ? AND chunk_hash = ?
                """,
                (task_id, chunk_hash),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _upsert_checkpoint(
        self,
        tenant_id: str,
        task_id: int,
        document_id: int,
        chunk_id: str,
        chunk_hash: str,
        status: str,
        vector_id: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO vector_ingest_checkpoints (
                    tenant_id, task_id, document_id, chunk_id, chunk_hash, status, vector_id, last_error, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(task_id, chunk_hash) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    chunk_id = excluded.chunk_id,
                    status = excluded.status,
                    vector_id = excluded.vector_id,
                    last_error = excluded.last_error,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (tenant_id, task_id, document_id, chunk_id, chunk_hash, status, vector_id, error_message),
            )
            conn.commit()
        finally:
            conn.close()

    def _increase_checkpoint_attempt(self, task_id: int, chunk_hash: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE vector_ingest_checkpoints
                SET attempt_count = attempt_count + 1, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND chunk_hash = ?
                """,
                (task_id, chunk_hash),
            )
            conn.commit()
        finally:
            conn.close()

    def _count_done_checkpoints(self, task_id: int) -> int:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COUNT(1) AS count FROM vector_ingest_checkpoints WHERE task_id = ? AND status = 'done'",
                (task_id,),
            ).fetchone()
            return int(row["count"]) if row else 0
        finally:
            conn.close()

    def _update_task_status(
        self,
        task_id: int,
        status: str,
        phase: str,
        error_message: Optional[str],
        retries: Optional[int] = None,
    ) -> None:
        conn = self._conn()
        try:
            if retries is None:
                conn.execute(
                    """
                    UPDATE upload_tasks
                    SET status = ?, phase = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (status, phase, error_message, task_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE upload_tasks
                    SET status = ?, phase = ?, error_message = ?, retries = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (status, phase, error_message, retries, task_id),
                )
            conn.commit()
        finally:
            conn.close()

    def _update_progress(self, task_id: int, processed_chunks: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE upload_tasks
                SET processed_chunks = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (processed_chunks, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _update_total_chunks(self, task_id: int, total_chunks: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE upload_tasks
                SET total_chunks = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (total_chunks, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _update_task_document(self, task_id: int, document_id: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE upload_tasks
                SET document_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (document_id, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _chunk_batches(self, values: List[IngestionChunk], batch_size: int) -> List[List[IngestionChunk]]:
        return [values[i : i + batch_size] for i in range(0, len(values), batch_size)]

    def _sleep_with_backoff(self, attempt: int) -> None:
        jitter = random.uniform(0, self.base_retry_delay * 0.2)
        delay = (self.base_retry_delay * (2 ** (attempt - 1))) + jitter
        time.sleep(delay)

    def get_summary_by_document_id(self, document_id: int, tenant_id: str = "public") -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT document_id, granularity, version, payload_json, artifact_path, created_at, updated_at
                FROM document_summaries
                WHERE document_id = ? AND tenant_id = ?
                """,
                (document_id, tenant_id),
            ).fetchone()
            if not row:
                return None
            payload = FreeAIRouter.safe_json_loads(str(row["payload_json"]), {})
            return {
                "document_id": int(row["document_id"]),
                "granularity": row["granularity"],
                "version": row["version"],
                "payload": payload if isinstance(payload, dict) else {},
                "artifact_path": row["artifact_path"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        finally:
            conn.close()

    def _generate_and_store_summary(
        self,
        document_id: int,
        task: Dict[str, Any],
        parsed: ParsedDocument,
        chunks: List[IngestionChunk],
    ) -> None:
        if not self.summary_enabled:
            return
        summary = self._build_detailed_summary(parsed=parsed, chunks=chunks, task=task)
        artifact_path = self._write_summary_artifact(
            document_id=document_id,
            filename=str(task.get("filename") or f"doc-{document_id}"),
            payload=summary,
        )
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO document_summaries (tenant_id, document_id, granularity, version, payload_json, artifact_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(document_id) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    granularity = excluded.granularity,
                    version = excluded.version,
                    payload_json = excluded.payload_json,
                    artifact_path = excluded.artifact_path,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    str(task.get("tenant_id", "public") or "public"),
                    document_id,
                    self.summary_granularity,
                    self.summary_version,
                    json.dumps(summary, ensure_ascii=False),
                    artifact_path,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _backfill_summary_from_stored_chunks(self, document_id: int, task: Dict[str, Any]) -> None:
        if not self.summary_enabled:
            return
        conn = self._conn()
        try:
            doc_row = conn.execute(
                "SELECT title, metadata FROM documents WHERE id = ? AND tenant_id = ?",
                (document_id, str(task.get("tenant_id", "public") or "public")),
            ).fetchone()
            vec_rows = conn.execute(
                """
                SELECT chunk_id, section_path, content
                FROM vectors
                WHERE document_id = ? AND tenant_id = ?
                ORDER BY id ASC
                """,
                (document_id, str(task.get("tenant_id", "public") or "public")),
            ).fetchall()
        finally:
            conn.close()
        if not doc_row or not vec_rows:
            return
        meta = FreeAIRouter.safe_json_loads(str(doc_row["metadata"]), {})
        chunks: List[IngestionChunk] = []
        for row in vec_rows:
            content = str(row["content"] or "").strip()
            if not content:
                continue
            section_path = str(row["section_path"] or "unknown")
            chunk_id = str(row["chunk_id"] or "")
            chunk_hash = hashlib.sha256(f"{section_path}|{chunk_id}|{content}".encode("utf-8")).hexdigest()
            chunks.append(
                IngestionChunk(
                    chunk_id=chunk_id,
                    section_path=section_path,
                    content=content,
                    chunk_hash=chunk_hash,
                )
            )
        if not chunks:
            return
        parsed = ParsedDocument(
            text="\n".join(c.content for c in chunks),
            metadata={
                "title": str(doc_row["title"] or task.get("filename") or ""),
                "filename": str(task.get("filename") or ""),
                "document_type": str(task.get("document_type") or "academic"),
                "discipline": str(task.get("discipline") or "all"),
                "pdf_page_count": int(task.get("page_count") or 0),
                **(meta if isinstance(meta, dict) else {}),
            },
        )
        self._generate_and_store_summary(document_id=document_id, task=task, parsed=parsed, chunks=chunks)

    def _write_summary_artifact(self, document_id: int, filename: str, payload: Dict[str, Any]) -> str:
        slug = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "-", filename).strip("-") or "document"
        artifact = self.summary_test_dir / f"{document_id}_{slug}.summary.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if artifact.exists() and not self.summary_test_overwrite:
            return str(artifact)
        with open(artifact, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return str(artifact)

    def _build_detailed_summary(
        self, parsed: ParsedDocument, chunks: List[IngestionChunk], task: Dict[str, Any]
    ) -> Dict[str, Any]:
        sections: Dict[str, List[IngestionChunk]] = {}
        for chunk in chunks:
            key = str(chunk.section_path or "unknown")
            sections.setdefault(key, []).append(chunk)

        section_summaries: List[Dict[str, Any]] = []
        global_keywords: Dict[str, int] = {}
        global_principles: List[str] = []
        global_why: List[str] = []
        global_how: List[str] = []
        for section, section_chunks in sections.items():
            merged = "\n".join(c.content for c in section_chunks if c.content).strip()
            if not merged:
                continue
            points = self._extract_key_points(
                merged,
                limit=self.summary_key_points_limit,
                max_chars=self.summary_sentence_char_limit,
            )
            kws = self._extract_keywords(merged, limit=self.summary_keywords_limit)
            for kw in kws:
                global_keywords[kw] = global_keywords.get(kw, 0) + 1
            evidence = [p[: min(self.summary_sentence_char_limit, 320)] for p in points[: self.summary_evidence_limit]]
            principles = self._extract_by_category(merged, "principle")
            why_items = self._extract_by_category(merged, "why")
            how_items = self._extract_by_category(merged, "how")
            global_principles.extend(principles)
            global_why.extend(why_items)
            global_how.extend(how_items)
            section_summaries.append(
                {
                    "section_path": section,
                    "chunk_count": len(section_chunks),
                    "key_points": points,
                    "keywords": kws,
                    "evidence_sentences": evidence,
                    "principles": principles,
                    "why": why_items,
                    "how": how_items,
                }
            )

        ranked_keywords = sorted(global_keywords.items(), key=lambda x: x[1], reverse=True)
        conclusion_pool = self._extract_key_points(
            parsed.text,
            limit=max(self.summary_conclusions_limit, 8),
            max_chars=self.summary_sentence_char_limit,
        )
        return {
            "schema": "upload_summary_detailed_v1",
            "granularity": self.summary_granularity,
            "version": self.summary_version,
            "task_id": int(task.get("id") or 0),
            "filename": str(task.get("filename") or parsed.metadata.get("filename") or ""),
            "title": str(parsed.metadata.get("title") or task.get("filename") or ""),
            "document_type": str(task.get("document_type") or parsed.metadata.get("document_type") or ""),
            "discipline": str(task.get("discipline") or parsed.metadata.get("discipline") or "all"),
            "page_count": int(parsed.metadata.get("pdf_page_count") or task.get("page_count") or 0),
            "chunk_count": len(chunks),
            "section_count": len(section_summaries),
            "top_keywords": [k for k, _ in ranked_keywords[: max(self.summary_keywords_limit, 20)]],
            "sections": section_summaries[: self.summary_sections_limit],
            "conclusions": conclusion_pool[: self.summary_conclusions_limit],
            "principles": self._dedup_short(global_principles, 20),
            "why": self._dedup_short(global_why, 15),
            "how": self._dedup_short(global_how, 15),
            "generated_at": self._now_iso(),
        }

    def _extract_key_points(self, text: str, limit: int = 5, max_chars: int = 280) -> List[str]:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [ln.strip(" \t-•*#") for ln in normalized.split("\n")]
        candidates = [ln for ln in lines if len(ln) >= 20]
        if not candidates:
            sentences = [s.strip() for s in re.split(r"[。！？.!?\n]+", normalized) if len(s.strip()) >= 20]
            candidates = sentences
        dedup: List[str] = []
        seen: set = set()
        for item in candidates:
            key = item[:120]
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item[: max(120, int(max_chars))])
            if len(dedup) >= limit:
                break
        return dedup

    def _extract_keywords(self, text: str, limit: int = 10) -> List[str]:
        tokens = re.findall(r"[A-Za-z]{4,}|[\u4e00-\u9fff]{2,8}", str(text or ""))
        stop = {"以及", "相关", "进行", "通过", "需要", "可以", "一个", "我们", "他们", "这个", "that", "with", "from"}
        freq: Dict[str, int] = {}
        for t in tokens:
            if t in stop:
                continue
            freq[t] = freq.get(t, 0) + 1
        ranked = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        return [k for k, _ in ranked[:limit]]

    # ── 分类提取：原理 / 为什么 / 怎么做 ──────────────────────────────
    _CATEGORY_PATTERNS: Dict[str, List[Any]] = {
        "principle": [
            re.compile(r"(?:原理|定理|定律|公理|法则|规律|机制|本质)[是为：:](.{10,200})", re.DOTALL),
            re.compile(r"(.{4,60}(?:原理|定理|定律|公理|法则|规律))", re.DOTALL),
            re.compile(r"(?:^|\n)\s*(?:定义[：:])\s*(.{10,200})", re.MULTILINE),
        ],
        "why": [
            re.compile(r"(?:原因|因为|由于|之所以|目的|意义)[是为在：:](.{10,200})", re.DOTALL),
            re.compile(r"(?:为什么|为何|何以).{0,4}(.{10,200})", re.DOTALL),
            re.compile(r"(.{4,60}(?:的原因|的目的|的意义|的作用))", re.DOTALL),
        ],
        "how": [
            re.compile(r"(?:方法|步骤|流程|过程|做法|途径|措施)[是为：:](.{10,200})", re.DOTALL),
            re.compile(r"(?:如何|怎样|怎么).{0,4}(.{10,200})", re.DOTALL),
            re.compile(r"(?:首先|第一步|步骤\s*\d)(.{10,200})", re.DOTALL),
        ],
    }

    def _extract_by_category(self, text: str, category: str, limit: int = 8) -> List[str]:
        """按类别（principle/why/how）从文本中提取结构化条目。"""
        patterns = self._CATEGORY_PATTERNS.get(category, [])
        results: List[str] = []
        seen: set = set()
        for pat in patterns:
            for m in pat.finditer(text):
                snippet = m.group(1).strip() if m.lastindex else m.group(0).strip()
                snippet = re.sub(r"\s+", " ", snippet)[:280]
                key = snippet[:80]
                if key in seen or len(snippet) < 10:
                    continue
                seen.add(key)
                results.append(snippet)
                if len(results) >= limit:
                    return results
        return results

    @staticmethod
    def _dedup_short(items: List[str], limit: int) -> List[str]:
        seen: set = set()
        out: List[str] = []
        for item in items:
            key = item[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _as_bool(value: str, default: bool) -> bool:
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _as_int(value: Optional[str], default: int, minimum: int) -> int:
        try:
            parsed = int(str(value).strip())
        except Exception:
            parsed = default
        return max(minimum, parsed)

    def _pg_conn(self, tenant_id: str = "public", user_id: str = "ingest-worker", roles: Optional[List[str]] = None):
        if not self.postgres_url:
            return None
        try:
            conn = postgres_store.connect(self.postgres_url)
            postgres_store.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=roles or [])
            return conn
        except Exception:
            return None

    def _push_scan_chunks_to_pg_staging(
        self, tenant_id: str, task_id: int, document_id: int, chunks: List[IngestionChunk]
    ) -> None:
        conn = self._pg_conn(tenant_id=tenant_id, user_id="ingest-worker", roles=["tenant_member"])
        if conn is None or not chunks:
            return
        try:
            with conn.cursor() as cur:
                for idx, chunk in enumerate(chunks, start=1):
                    cur.execute(
                        """
                        INSERT INTO staging_scan_chunks (
                            tenant_id, task_id, sqlite_document_id, chunk_seq, chunk_id, section_path, content, chunk_hash, status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued')
                        ON CONFLICT (tenant_id, task_id, chunk_hash) DO UPDATE
                        SET chunk_seq = EXCLUDED.chunk_seq,
                            section_path = EXCLUDED.section_path,
                            content = EXCLUDED.content,
                            status = 'queued',
                            updated_at = NOW()
                        """,
                        (
                            tenant_id,
                            task_id,
                            document_id,
                            idx,
                            chunk.chunk_id,
                            chunk.section_path,
                            chunk.content,
                            chunk.chunk_hash,
                        ),
                    )
            conn.commit()
        finally:
            conn.close()

    def _push_vector_chunk_to_pg_staging(
        self,
        tenant_id: str,
        task_id: int,
        document_id: int,
        chunk: IngestionChunk,
        embedding: List[float],
        vector_id: int,
    ) -> None:
        conn = self._pg_conn(tenant_id=tenant_id, user_id="ingest-worker", roles=["tenant_member"])
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE staging_scan_chunks
                    SET status = 'done', updated_at = NOW()
                    WHERE tenant_id = %s AND task_id = %s AND chunk_hash = %s
                    """,
                    (tenant_id, task_id, chunk.chunk_hash),
                )
                cur.execute(
                    """
                    INSERT INTO staging_vector_chunks (
                        tenant_id, task_id, sqlite_document_id, sqlite_vector_id, chunk_id, section_path,
                        chunk_hash, embedding_json, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, 'done')
                    ON CONFLICT (tenant_id, task_id, chunk_hash) DO UPDATE
                    SET sqlite_vector_id = EXCLUDED.sqlite_vector_id,
                        section_path = EXCLUDED.section_path,
                        embedding_json = EXCLUDED.embedding_json,
                        status = 'done',
                        updated_at = NOW()
                    """,
                    (
                        tenant_id,
                        task_id,
                        document_id,
                        vector_id,
                        chunk.chunk_id,
                        chunk.section_path,
                        chunk.chunk_hash,
                        json.dumps(embedding, ensure_ascii=False),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> Any:
        return knowledge_store.connect()
