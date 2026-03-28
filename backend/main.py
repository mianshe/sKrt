from __future__ import annotations

import json
import logging
import os
import sqlite3
import asyncio
import hashlib
import hmac
import base64
import secrets
import re
import uuid
import shutil
import time
from decimal import Decimal, ROUND_HALF_UP
from contextlib import asynccontextmanager
from datetime import date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, NotRequired, Optional, TypedDict, Tuple
from urllib.parse import urlencode, quote_plus
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, File, HTTPException, UploadFile, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.runtime_config import RuntimeConfig
from backend.services.chunker import DocumentChunker
from backend.services.document_parser import DocumentParser
from backend.services.exam_processor import ExamProcessor
from backend.services.free_ai_router import FreeAIRouter
from backend.services.supabase_storage import SupabaseStorageConfig, upload_file
from backend.services.r2_storage import (
    R2StorageConfig,
    download_to_file as r2_download_to_file,
    generate_presigned_put_url,
    head_object as r2_head_object,
    parse_r2_uri,
    r2_uri,
    upload_file as r2_upload_file,
)
from backend.services.graphs import AgentChains
from backend.services.kg_builder import KGBuilder
from backend.services.rag_engine import RAGEngine
from backend.services.pipeline import DeepPipelineService
from backend.services.pipeline import postgres_store as pg_store
from backend.services.security_context import IdentityContext, JwtValidator, to_identity_context
from backend.services.email_sender import send_plain_email
from backend.services import knowledge_store
from backend.services.knowledge_store import insert_returning_id
from backend.services.upload_ingestion_service import UploadIngestionService
from backend.services.payments.easypay_provider import EasyPayProvider
from backend.services.runpod_client import runpod_enabled, submit_ingestion_job
from backend.services.gpu_autostart_cloud import (
    gpu_autostart_enabled,
    start_gpu_instances,
    stop_gpu_instances,
)
from backend.services.gpu_idle_autostop import schedule_gpu_idle_stop

from backend.services.upload_load_control import (
    enforce_upload_create_allowed,
    get_upload_queue_metrics,
    log_ingestion_event,
    record_upload_tasks_created,
)

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
CHUNK_TEMP_DIR = DATA_DIR / "chunk_uploads"
DB_PATH = DATA_DIR / "knowledge.db"
RUNTIME_CONFIG = RuntimeConfig.from_env()
knowledge_store.configure(
    sqlite_path=str(DB_PATH),
    busy_timeout_ms=RUNTIME_CONFIG.sqlite.busy_timeout_ms,
    wal_enabled=RUNTIME_CONFIG.sqlite.wal_enabled,
    database_url=RUNTIME_CONFIG.postgres.database_url,
)
_cleanup_task: Optional[asyncio.Task[Any]] = None


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=3000)
    discipline: str = "all"
    mode: str = "free"
    session_id: Optional[str] = None


class ChatMemoryClearRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=120)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    discipline: str = "all"


class ExamRequest(BaseModel):
    exam_text: str = Field(min_length=1)
    discipline: str = "all"


class SummaryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=3000)
    discipline: str = "all"
    document_id: Optional[int] = Field(default=None, ge=1)
    summary_compact_level: Optional[int] = None
    summary_mode: Optional[str] = None


class ReportRequest(BaseModel):
    query: str = Field(min_length=1, max_length=3000)
    discipline: str = "all"
    document_id: Optional[int] = Field(default=None, ge=1)
    summary_compact_level: Optional[int] = None
    report_mode: Optional[str] = None


class DeepReportStartRequest(BaseModel):
    """四库树状深度流水线：需配置 DATABASE_URL（PostgreSQL）。"""

    document_id: int = Field(ge=1)
    discipline: str = "all"
    config: Optional[Dict[str, Any]] = None


class ChunkInitRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    total_size: int = Field(gt=0)
    total_chunks: int = Field(gt=0)
    discipline: str = "all"
    document_type: str = "academic"
    purpose: str = Field(default="docs", pattern="^(docs|exam)$")
    use_gpu_ocr: Optional[bool] = None


class ChunkCompleteRequest(BaseModel):
    discipline: str = "all"
    document_type: str = "academic"
    purpose: str = Field(default="docs", pattern="^(docs|exam)$")
    use_gpu_ocr: Optional[bool] = None
    external_ocr_confirmed: bool = False


class UploadPresignInitRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    discipline: str = "general"
    document_type: Optional[str] = None
    use_gpu_ocr: bool = False


class UploadPresignCompleteRequest(BaseModel):
    object_key: str = Field(min_length=1, max_length=1024)
    use_gpu_ocr: bool = False
    external_ocr_confirmed: bool = False


class ChunkUploadMeta(TypedDict):
    upload_id: str
    filename: str
    total_size: int
    total_chunks: int
    purpose: str
    discipline: str
    document_type: str
    use_gpu_ocr: bool
    received_chunks: int
    temp_dir: str
    tenant_id: str
    awaiting_external_ocr_confirm: NotRequired[bool]
    pending_final_path: NotRequired[str]
    pending_storage_basename: NotRequired[str]
    pending_target_filename: NotRequired[str]
    file_path_override: NotRequired[str]


class PayOrderCreateRequest(BaseModel):
    pack_key: str = Field(pattern="^(A|B|C)$")
    channel: str = Field(default="wechat_native", pattern="^(wechat_native|alipay_qr)$")


class PayOrderRefundRequest(BaseModel):
    key: Optional[str] = None


class RunpodIngestionCallbackRequest(BaseModel):
    task_id: int = Field(ge=1)
    tenant_id: str = "public"
    status: str = Field(pattern="^(running|completed|failed)$")
    error_message: Optional[str] = None
    runpod_job_id: Optional[str] = None
    signature: str = Field(min_length=8)


class RequestIdentity(TypedDict, total=False):
    tenant_id: str
    user_id: str
    roles: List[str]
    permissions: List[str]
    auth_source: str


def _normalize_tenant_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = re.sub(r"[^0-9A-Za-z_\-\.]+", "-", raw).strip("-")
    return normalized[:64]


def _normalize_user_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = re.sub(r"[^0-9A-Za-z_\-\.@]+", "-", raw).strip("-")
    return normalized[:128]


_jwt_validator: Optional[JwtValidator] = None
if RUNTIME_CONFIG.auth.enabled:
    try:
        _jwt_validator = JwtValidator(
            issuer=RUNTIME_CONFIG.auth.issuer,
            audience=RUNTIME_CONFIG.auth.audience,
            jwks_url=RUNTIME_CONFIG.auth.jwks_url,
            leeway_seconds=RUNTIME_CONFIG.auth.leeway_seconds,
        )
    except Exception:
        logger.exception("JWT validator init failed; falling back to header tenant mode")
        _jwt_validator = None


def _extract_bearer_token(request: Request) -> str:
    auth = str(request.headers.get("Authorization", "")).strip()
    if not auth.lower().startswith("bearer "):
        return ""
    return auth[7:].strip()


def _lookup_membership(tenant_id: str, user_id: str) -> Dict[str, Any]:
    if not RUNTIME_CONFIG.postgres.enabled:
        return {"tenant_id": tenant_id, "user_id": user_id, "roles": ["tenant_admin"], "permissions": ["*"], "status": "active"}
    conn = None
    try:
        conn = pg_store.connect(RUNTIME_CONFIG.postgres.database_url)
        pg_store.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=[])
        row = pg_store.check_tenant_membership(conn, tenant_id=tenant_id, user_id=user_id)
        return row
    except Exception:
        logger.exception("membership lookup failed tenant=%s user=%s", tenant_id, user_id)
        return {}
    finally:
        if conn:
            conn.close()


def _get_request_identity(request: Request) -> RequestIdentity:
    if hasattr(request.state, "identity") and isinstance(request.state.identity, dict):
        return request.state.identity
    tenant_id = ""
    user_id = ""
    roles: List[str] = []
    permissions: List[str] = []
    auth_source = "header"
    if RUNTIME_CONFIG.auth.enabled and _jwt_validator is not None:
        token = _extract_bearer_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="缺少 Bearer token")
        try:
            claims = _jwt_validator.validate(token)
            identity: IdentityContext = to_identity_context(
                claims,
                tenant_claim_key=RUNTIME_CONFIG.auth.tenant_claim_key,
                roles_claim_key=RUNTIME_CONFIG.auth.roles_claim_key,
                permissions_claim_key=RUNTIME_CONFIG.auth.permissions_claim_key,
            )
            tenant_id = _normalize_tenant_id(identity.tenant_id)
            user_id = _normalize_user_id(identity.user_id)
            roles = list(identity.roles)
            permissions = list(identity.permissions)
            auth_source = "jwt"
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"token 校验失败: {exc}") from exc
        if not tenant_id or not user_id:
            raise HTTPException(status_code=401, detail="token 缺少 tenant_id 或 sub")
        if RUNTIME_CONFIG.auth.require_membership_check:
            m = _lookup_membership(tenant_id, user_id)
            if not m or str(m.get("status", "")).lower() != "active":
                raise HTTPException(status_code=403, detail="租户成员校验失败")
            mr = m.get("roles", [])
            mp = m.get("permissions", [])
            roles = [str(x).strip() for x in (mr or []) if str(x).strip()] or roles
            permissions = [str(x).strip() for x in (mp or []) if str(x).strip()] or permissions
    else:
        header_key = (RUNTIME_CONFIG.tenant.header_name or "X-Tenant-Id").strip()
        raw = request.headers.get(header_key, "")
        tenant_id = _normalize_tenant_id(raw)
        if not tenant_id:
            if RUNTIME_CONFIG.tenant.require_header:
                raise HTTPException(status_code=400, detail=f"缺少有效租户头: {header_key}")
            # 若启用 IP 隔离，使用客户端 IP 作为租户 ID，实现多用户数据隔离
            ip_as_default = (os.getenv("TENANT_IP_AS_DEFAULT", "1") or "1").strip() == "1"
            if ip_as_default and request.client and request.client.host:
                ip_raw = re.sub(r"[^0-9A-Za-z\.\:]", "-", request.client.host)
                tenant_id = _normalize_tenant_id(f"ip-{ip_raw}") or "public"
            else:
                tenant_id = _normalize_tenant_id(RUNTIME_CONFIG.tenant.default_tenant_id) or "public"
        user_id = _normalize_user_id(request.headers.get("X-User-Id", "anonymous")) or "anonymous"
        roles = [str(x).strip() for x in request.headers.get("X-Roles", "tenant_admin").split(",") if str(x).strip()]
        permissions = [str(x).strip() for x in request.headers.get("X-Permissions", "*").split(",") if str(x).strip()]
    out: RequestIdentity = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "roles": roles,
        "permissions": permissions,
        "auth_source": auth_source,
    }
    request.state.identity = out
    return out


def _has_role(identity: RequestIdentity, role: str) -> bool:
    return role in set(identity.get("roles", []))


def _has_permission(identity: RequestIdentity, perm: str) -> bool:
    perms = set(identity.get("permissions", []))
    return "*" in perms or perm in perms


def _require_permission(identity: RequestIdentity, permission: str) -> None:
    if _has_permission(identity, permission):
        return
    if permission.startswith("tenant.") and _has_role(identity, "tenant_admin"):
        return
    raise HTTPException(status_code=403, detail=f"权限不足: {permission}")


def _capacity_snapshot() -> Dict[str, Any]:
    usage = shutil.disk_usage(DATA_DIR)
    free_bytes = int(usage.free)
    soft = int(RUNTIME_CONFIG.capacity.disk_soft_limit_bytes)
    hard = int(RUNTIME_CONFIG.capacity.disk_hard_limit_bytes)
    return {
        "free_bytes": free_bytes,
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "soft_limit_bytes": soft,
        "hard_limit_bytes": hard,
        "soft_exceeded": bool(soft > 0 and free_bytes <= soft),
        "hard_exceeded": bool(hard > 0 and free_bytes <= hard),
    }


def _ensure_capacity_for_write() -> None:
    snap = _capacity_snapshot()
    if snap["hard_exceeded"]:
        raise HTTPException(status_code=507, detail="服务存储容量不足，已暂停写入任务，请清理或扩容后重试。")


def _chat_memory_ttl_seconds() -> int:
    try:
        return max(0, int((os.getenv("CHAT_MEMORY_TTL_SECONDS") or "0").strip()))
    except Exception:
        return 0


def _chat_memory_recent_limit() -> int:
    try:
        return max(1, min(12, int((os.getenv("CHAT_MEMORY_RECENT_LIMIT") or "6").strip())))
    except Exception:
        return 6


def _load_chat_memory_context(tenant_id: str, user_id: str, session_id: str) -> List[Dict[str, Any]]:
    # 优先 PostgreSQL；若不可用，降级到 SQLite 本地工作记忆
    if RUNTIME_CONFIG.postgres.enabled:
        conn = None
        try:
            conn = pg_store.connect(RUNTIME_CONFIG.postgres.database_url)
            pg_store.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=[])
            return pg_store.list_recent_chat_turns(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                limit=_chat_memory_recent_limit(),
            )
        except Exception:
            logger.exception("load chat work memory failed tenant=%s", tenant_id)
            return []
        finally:
            if conn:
                conn.close()
    # SQLite 降级路径
    try:
        limit = _chat_memory_recent_limit()
        sq = sqlite3.connect(DB_PATH, timeout=RUNTIME_CONFIG.sqlite.busy_timeout_ms / 1000.0)
        sq.row_factory = sqlite3.Row
        try:
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
            rows = sq.execute(
                """
                SELECT question, answer, sources_json, created_at FROM chat_sessions
                WHERE tenant_id=? AND session_id=?
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC LIMIT ?
                """,
                (tenant_id, session_id, now_iso, limit),
            ).fetchall()
            return [
                {"question": r["question"], "answer": r["answer"], "sources": json.loads(r["sources_json"] or "[]")}
                for r in reversed(rows)
            ]
        finally:
            sq.close()
    except Exception:
        logger.exception("sqlite load chat memory failed tenant=%s", tenant_id)
        return []


def _append_chat_memory(
    tenant_id: str,
    user_id: str,
    session_id: str,
    question: str,
    answer: str,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> None:
    # 优先 PostgreSQL；若不可用，降级到 SQLite 本地工作记忆
    if RUNTIME_CONFIG.postgres.enabled:
        conn = None
        try:
            conn = pg_store.connect(RUNTIME_CONFIG.postgres.database_url)
            pg_store.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=[])
            pg_store.insert_chat_turn(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                question=question,
                answer=answer,
                source_json=sources or [],
                expire_seconds=_chat_memory_ttl_seconds(),
            )
        except Exception:
            logger.exception("append chat work memory failed tenant=%s", tenant_id)
        finally:
            if conn:
                conn.close()
        return
    # SQLite 降级路径
    try:
        ttl = _chat_memory_ttl_seconds()
        expires_at = None
        if ttl > 0:
            expires_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + ttl))
        sq = sqlite3.connect(DB_PATH, timeout=RUNTIME_CONFIG.sqlite.busy_timeout_ms / 1000.0)
        try:
            sq.execute(
                """
                INSERT INTO chat_sessions(tenant_id, user_id, session_id, question, answer, sources_json, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tenant_id, user_id, session_id, question, answer, json.dumps(sources or []), expires_at),
            )
            sq.commit()
        finally:
            sq.close()
    except Exception:
        logger.exception("sqlite append chat memory failed tenant=%s", tenant_id)


def _audit_log(
    request: Request,
    identity: RequestIdentity,
    action: str,
    resource_type: str,
    resource_id: str,
    result: str,
    reason: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    if not RUNTIME_CONFIG.postgres.enabled:
        return
    conn = None
    try:
        conn = pg_store.connect(RUNTIME_CONFIG.postgres.database_url)
        pg_store.set_request_context(
            conn,
            tenant_id=str(identity.get("tenant_id", "public")),
            user_id=str(identity.get("user_id", "anonymous")),
            roles=list(identity.get("roles", [])),
        )
        pg_store.insert_audit_log(
            conn,
            tenant_id=str(identity.get("tenant_id", "public")),
            user_id=str(identity.get("user_id", "anonymous")),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            ip_address=str(request.client.host if request.client else ""),
            user_agent=str(request.headers.get("user-agent", "")),
            reason=reason,
            details=details or {},
        )
    except Exception:
        logger.exception("audit log write failed action=%s", action)
    finally:
        if conn:
            conn.close()


def _security_event(
    request: Request,
    identity: Optional[RequestIdentity],
    event_type: str,
    severity: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    if not RUNTIME_CONFIG.postgres.enabled:
        return
    tenant_id = str((identity or {}).get("tenant_id", "public"))
    user_id = str((identity or {}).get("user_id", "anonymous"))
    conn = None
    try:
        conn = pg_store.connect(RUNTIME_CONFIG.postgres.database_url)
        pg_store.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=list((identity or {}).get("roles", [])))
        pg_store.insert_security_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
            severity=severity,
            message=message,
            ip_address=str(request.client.host if request.client else ""),
            user_agent=str(request.headers.get("user-agent", "")),
            details=details or {},
        )
    except Exception:
        logger.exception("security event write failed type=%s", event_type)
    finally:
        if conn:
            conn.close()


def _tenant_quota_snapshot(identity: RequestIdentity) -> Dict[str, Any]:
    tenant_id = str(identity.get("tenant_id", "public"))
    quotas = {
        "max_documents": int(os.getenv("TENANT_DEFAULT_MAX_DOCUMENTS", "1000") or 1000),
        "max_vectors": int(os.getenv("TENANT_DEFAULT_MAX_VECTORS", "1000000") or 1000000),
        "max_storage_bytes": int(os.getenv("TENANT_DEFAULT_MAX_STORAGE_BYTES", str(5 * 1024 * 1024 * 1024)) or 5 * 1024 * 1024 * 1024),
    }
    if RUNTIME_CONFIG.postgres.enabled:
        conn = None
        try:
            conn = pg_store.connect(RUNTIME_CONFIG.postgres.database_url)
            pg_store.set_request_context(conn, tenant_id=tenant_id, user_id=str(identity.get("user_id", "anonymous")), roles=list(identity.get("roles", [])))
            row = pg_store.get_tenant_quota(conn, tenant_id=tenant_id)
            if row:
                quotas["max_documents"] = int(row.get("max_documents") or quotas["max_documents"])
                quotas["max_vectors"] = int(row.get("max_vectors") or quotas["max_vectors"])
                quotas["max_storage_bytes"] = int(row.get("max_storage_bytes") or quotas["max_storage_bytes"])
        except Exception:
            logger.exception("tenant quota lookup failed tenant=%s", tenant_id)
        finally:
            if conn:
                conn.close()
    conn_sql = _conn()
    try:
        docs = int(
            conn_sql.execute("SELECT COUNT(1) AS c FROM documents WHERE tenant_id = ?", (tenant_id,)).fetchone()["c"] or 0
        )
        vecs = int(
            conn_sql.execute("SELECT COUNT(1) AS c FROM vectors WHERE tenant_id = ?", (tenant_id,)).fetchone()["c"] or 0
        )
    finally:
        conn_sql.close()
    usage = _capacity_snapshot()
    return {
        **quotas,
        "doc_count": docs,
        "vector_count": vecs,
        "used_storage_bytes": int(usage["used_bytes"]),
        "tenant_id": tenant_id,
    }


def _enforce_tenant_quota(identity: RequestIdentity) -> None:
    quota = _tenant_quota_snapshot(identity)
    if quota["doc_count"] >= quota["max_documents"]:
        raise HTTPException(status_code=429, detail="租户文档配额已满，请联系管理员扩容。")
    if quota["vector_count"] >= quota["max_vectors"]:
        raise HTTPException(status_code=429, detail="租户向量配额已满，请联系管理员扩容。")


def _init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CHUNK_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    if knowledge_store.use_postgres():
        knowledge_store.init_application_schema()
        return
    conn = sqlite3.connect(DB_PATH, timeout=RUNTIME_CONFIG.sqlite.busy_timeout_ms / 1000.0)
    try:
        conn.execute(f"PRAGMA busy_timeout = {RUNTIME_CONFIG.sqlite.busy_timeout_ms}")
        if RUNTIME_CONFIG.sqlite.wal_enabled:
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'public',
                filename TEXT NOT NULL,
                title TEXT NOT NULL,
                discipline TEXT NOT NULL,
                document_type TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vectors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'public',
                document_id INTEGER NOT NULL,
                chunk_id TEXT NOT NULL,
                content TEXT NOT NULL,
                section_path TEXT NOT NULL,
                embedding TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kg_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'public',
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                explanation TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        doc_cols = [str(row[1]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()]
        if "tenant_id" not in doc_cols:
            conn.execute("ALTER TABLE documents ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'public'")
        vec_cols = [str(row[1]) for row in conn.execute("PRAGMA table_info(vectors)").fetchall()]
        if "tenant_id" not in vec_cols:
            conn.execute("ALTER TABLE vectors ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'public'")
        kg_cols = [str(row[1]) for row in conn.execute("PRAGMA table_info(kg_relations)").fetchall()]
        if "tenant_id" not in kg_cols:
            conn.execute("ALTER TABLE kg_relations ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'public'")
        # 聊天工作记忆表（SQLite 本地存储，无需 PostgreSQL）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'public',
                user_id TEXT NOT NULL DEFAULT 'anonymous',
                session_id TEXT NOT NULL DEFAULT 'default',
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                sources_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT DEFAULT NULL
            )
            """
        )
        # OCR 逐页缓存表（供 upload_ingestion_service 写入，断点续传）
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
        # vectors 表迁移：补充 page_num 和 chunk_type 列
        vec_cols2 = [str(row[1]) for row in conn.execute("PRAGMA table_info(vectors)").fetchall()]
        if "page_num" not in vec_cols2:
            conn.execute("ALTER TABLE vectors ADD COLUMN page_num INTEGER NOT NULL DEFAULT 0")
        if "chunk_type" not in vec_cols2:
            conn.execute("ALTER TABLE vectors ADD COLUMN chunk_type TEXT NOT NULL DEFAULT 'knowledge'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_tenant_doc ON vectors(tenant_id, document_id, chunk_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_relations_tenant ON kg_relations(tenant_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_lookup ON chat_sessions(tenant_id, session_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ocr_page_cache ON ocr_page_cache(task_id, page_num)")
        # GPU OCR 按页额度（每日 / 全站月）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_ocr_daily_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'public',
                client_id TEXT NOT NULL,
                day TEXT NOT NULL,
                pages_used INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tenant_id, client_id, day)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gpu_ocr_daily_pages_lookup ON gpu_ocr_daily_pages(tenant_id, client_id, day)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_ocr_global_monthly_pages (
                month_key TEXT PRIMARY KEY,
                pages_used INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_ocr_daily_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'public',
                client_id TEXT NOT NULL,
                day TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tenant_id, client_id, day)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gpu_ocr_usage_lookup ON gpu_ocr_daily_usage(tenant_id, client_id, day)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_ocr_global_monthly_usage (
                month_key TEXT PRIMARY KEY,
                used INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # GPU OCR 付费/赠送页余额（按 tenant_id + client_id）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_ocr_paid_pages_balance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'public',
                client_id TEXT NOT NULL,
                pages_balance INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tenant_id, client_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gpu_ocr_paid_pages_balance_lookup ON gpu_ocr_paid_pages_balance(tenant_id, client_id)"
        )
        # GPU OCR 付费/赠送页流水（审计）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_ocr_paid_pages_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'public',
                client_id TEXT NOT NULL,
                delta_pages INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gpu_ocr_paid_pages_ledger_lookup ON gpu_ocr_paid_pages_ledger(tenant_id, client_id, created_at)"
        )
        # PayJS 订单表
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pay_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL DEFAULT 'public',
                client_id TEXT NOT NULL,
                pack_key TEXT NOT NULL,
                pages INTEGER NOT NULL DEFAULT 0,
                amount_cny REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                provider TEXT NOT NULL DEFAULT 'payjs',
                channel TEXT NOT NULL DEFAULT 'wechat_native',
                payjs_order_id TEXT DEFAULT NULL,
                payjs_transaction_id TEXT DEFAULT NULL,
                credited_pages INTEGER NOT NULL DEFAULT 0,
                reverted_pages INTEGER NOT NULL DEFAULT 0,
                paid_at TEXT DEFAULT NULL,
                refund_status TEXT NOT NULL DEFAULT 'none',
                refunded_at TEXT DEFAULT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_orders_lookup ON pay_orders(order_no, tenant_id, client_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_orders_status ON pay_orders(status, created_at)")
        # PayJS 回调审计日志
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pay_callbacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL DEFAULT 'payjs',
                event_type TEXT NOT NULL DEFAULT 'notify',
                order_no TEXT NOT NULL DEFAULT '',
                payload_text TEXT NOT NULL DEFAULT '',
                sign_ok INTEGER NOT NULL DEFAULT 0,
                handled INTEGER NOT NULL DEFAULT 0,
                result_text TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_callbacks_lookup ON pay_callbacks(order_no, created_at)")
        conn.commit()
    finally:
        conn.close()


def _enforce_production_security_baseline() -> None:
    env_name = (os.getenv("APP_ENV", "dev") or "dev").strip().lower()
    if env_name not in {"prod", "production"}:
        return
    if not RUNTIME_CONFIG.auth.enabled:
        raise RuntimeError("生产环境必须开启 AUTH_JWT_ENABLED=1")
    if not RUNTIME_CONFIG.postgres.enabled:
        raise RuntimeError("生产环境必须配置 DATABASE_URL 并启用 PostgreSQL")
    if not RUNTIME_CONFIG.auth.require_membership_check:
        raise RuntimeError("生产环境必须开启 AUTH_REQUIRE_MEMBERSHIP_CHECK=1")


def _cleanup_enabled() -> bool:
    return (os.getenv("CLEANUP_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, min_value: int = 0, max_value: int = 10**9) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        val = int(raw)
    except ValueError:
        val = default
    return max(min_value, min(max_value, val))


def _cleanup_interval_seconds() -> int:
    hours = _env_int("CLEANUP_INTERVAL_HOURS", 168, min_value=1, max_value=24 * 365)
    return hours * 3600


def _safe_under(parent: Path, child: Path) -> bool:
    try:
        parent_r = parent.resolve()
        child_r = child.resolve()
        return str(child_r).startswith(str(parent_r))
    except Exception:
        return False


def _collect_active_file_paths() -> set[str]:
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT file_path
            FROM upload_tasks
            WHERE status IN ('queued', 'running')
            """
        ).fetchall()
        return {str(r["file_path"]) for r in rows if r and r["file_path"]}
    finally:
        conn.close()


def _cleanup_once() -> Dict[str, Any]:
    started = time.time()
    stats: Dict[str, Any] = {
        "chunk_dirs_deleted": 0,
        "chunk_bytes_freed": 0,
        "ocr_cache_files_deleted": 0,
        "ocr_cache_bytes_freed": 0,
        "failed_files_deleted": 0,
        "failed_bytes_freed": 0,
        "old_documents_deleted": 0,
        "old_documents_bytes_freed": 0,
        "errors": 0,
    }

    now = time.time()
    chunk_retention_h = _env_int("CLEANUP_CHUNK_RETENTION_HOURS", 24, min_value=1, max_value=24 * 365)
    ocr_cache_retention_h = _env_int("CLEANUP_OCR_CACHE_RETENTION_HOURS", 72, min_value=1, max_value=24 * 365)
    failed_retention_h = _env_int("CLEANUP_FAILED_FILE_RETENTION_HOURS", 168, min_value=1, max_value=24 * 365)
    document_retention_days = _env_int("CLEANUP_DOCUMENT_RETENTION_DAYS", 30, min_value=1, max_value=365)
    chunk_cutoff = now - chunk_retention_h * 3600
    ocr_cutoff = now - ocr_cache_retention_h * 3600
    failed_cutoff = now - failed_retention_h * 3600
    document_cutoff = now - document_retention_days * 86400

    # 1) 清理过期分片临时目录
    try:
        if CHUNK_TEMP_DIR.exists():
            for entry in CHUNK_TEMP_DIR.iterdir():
                try:
                    if not entry.is_dir():
                        continue
                    mtime = entry.stat().st_mtime
                    if mtime >= chunk_cutoff:
                        continue
                    total = 0
                    for p in entry.rglob("*"):
                        if p.is_file():
                            try:
                                total += p.stat().st_size
                            except Exception:
                                pass
                    shutil.rmtree(entry, ignore_errors=True)
                    stats["chunk_dirs_deleted"] += 1
                    stats["chunk_bytes_freed"] += total
                except Exception:
                    stats["errors"] += 1
    except Exception:
        stats["errors"] += 1

    # 2) 清理 uploads/_cache 过期 OCR 缓存
    cache_dir = UPLOAD_DIR / "_cache"
    try:
        if cache_dir.exists():
            for p in cache_dir.rglob("*"):
                try:
                    if not p.is_file():
                        continue
                    if p.stat().st_mtime >= ocr_cutoff:
                        continue
                    sz = p.stat().st_size
                    p.unlink(missing_ok=True)
                    stats["ocr_cache_files_deleted"] += 1
                    stats["ocr_cache_bytes_freed"] += sz
                except Exception:
                    stats["errors"] += 1
    except Exception:
        stats["errors"] += 1

    # 3) 清理失败且过期任务的本地残留文件（保护：不删活跃任务文件，不删远端 URI）
    active_paths = _collect_active_file_paths()
    failed_cutoff_iso = datetime.fromtimestamp(failed_cutoff, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT file_path
            FROM upload_tasks
            WHERE status = 'failed'
              AND updated_at < ?
            """,
            (failed_cutoff_iso,),
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        try:
            fp = str(r["file_path"] or "").strip()
            if not fp:
                continue
            if fp.startswith("supabase://") or fp.startswith("http://") or fp.startswith("https://"):
                continue
            if fp in active_paths:
                continue
            fpath = Path(fp)
            if not fpath.exists() or not fpath.is_file():
                continue
            if not _safe_under(UPLOAD_DIR, fpath):
                continue
            if fpath.stat().st_mtime >= failed_cutoff:
                continue
            sz = fpath.stat().st_size
            fpath.unlink(missing_ok=True)
            stats["failed_files_deleted"] += 1
            stats["failed_bytes_freed"] += sz
        except Exception:
            stats["errors"] += 1

    # 4) 清理超过保留期的旧文档（释放存储空间）
    try:
        conn = _conn()
        try:
            old_docs = conn.execute(
                "SELECT id, file_path, created_at FROM documents WHERE created_at < ?",
                (int(document_cutoff),),
            ).fetchall()
        finally:
            conn.close()

        for doc in old_docs:
            try:
                doc_id = int(doc["id"])
                file_path = str(doc.get("file_path") or "")

                # 删除文件
                if file_path and not file_path.startswith(("http://", "https://", "supabase://")):
                    fpath = Path(file_path) if Path(file_path).is_absolute() else UPLOAD_DIR / file_path
                    if fpath.exists() and fpath.is_file():
                        sz = fpath.stat().st_size
                        fpath.unlink(missing_ok=True)
                        stats["old_documents_bytes_freed"] += sz

                # 删除数据库记录（向量会级联删除）
                conn_del = _conn()
                try:
                    conn_del.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                    conn_del.commit()
                finally:
                    conn_del.close()

                stats["old_documents_deleted"] += 1
            except Exception:
                stats["errors"] += 1
    except Exception:
        stats["errors"] += 1

    stats["elapsed_ms"] = int((time.time() - started) * 1000)
    return stats


async def _cleanup_scheduler() -> None:
    interval = _cleanup_interval_seconds()
    while True:
        try:
            await asyncio.sleep(interval)
            stats = _cleanup_once()
            logger.info("cleanup finished: %s", stats)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("cleanup scheduler failed")


def _init_runtime_tables() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_random_codes (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                purpose TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                target_email TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                used_at TEXT DEFAULT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_email_random_codes_lookup ON email_random_codes(tenant_id, client_id, purpose, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runpod_jobs (
                id TEXT PRIMARY KEY,
                task_id INTEGER NOT NULL,
                tenant_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                runpod_job_id TEXT NOT NULL DEFAULT '',
                request_payload TEXT NOT NULL DEFAULT '',
                response_payload TEXT NOT NULL DEFAULT '',
                error_text TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runpod_jobs_task ON runpod_jobs(task_id, created_at)")
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _cleanup_task
    _enforce_production_security_baseline()
    _init_db()
    _init_runtime_tables()
    upload_ingestion_service.init_schema()
    try:
        deep_pipeline_service.init_schema()
    except Exception:
        logger.exception("deep pipeline PostgreSQL init_schema failed (check DATABASE_URL)")
    if _cleanup_enabled():
        try:
            startup_stats = _cleanup_once()
            logger.info("cleanup startup run finished: %s", startup_stats)
        except Exception:
            logger.exception("cleanup startup run failed")
        _cleanup_task = asyncio.create_task(_cleanup_scheduler())
    try:
        yield
    finally:
        if _cleanup_task and not _cleanup_task.done():
            _cleanup_task.cancel()
            try:
                await _cleanup_task
            except asyncio.CancelledError:
                pass
        _cleanup_task = None


app = FastAPI(title="Academic Knowledge PWA API", lifespan=lifespan)
def _cors_allow_origins() -> List[str]:
    raw = (os.getenv("CORS_ALLOW_ORIGINS") or "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [s.strip() for s in raw.split(",") if s.strip()]


def _client_id_from_request(request: Request) -> str:
    cid = (request.headers.get("X-Client-Id") or "").strip()
    if cid:
        return cid[:128]
    try:
        host = request.client.host if request.client else ""
    except Exception:
        host = ""
    return (host or "unknown")[:128]


def _today_key() -> str:
    return date.today().isoformat()


def _day_key_beijing() -> str:
    # Use fixed UTC+8 to avoid zoneinfo dependency.
    dt = datetime.utcnow() + timedelta(hours=8)
    return dt.date().isoformat()


def _month_key_beijing() -> str:
    # Use fixed UTC+8 to avoid zoneinfo dependency.
    dt = datetime.utcnow() + timedelta(hours=8)
    return dt.strftime("%Y-%m")


def _hmac_sign(secret: str, msg: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")


def _make_special_cookie(secret: str, tenant_id: str, ttl_sec: int) -> str:
    now = int(time.time())
    exp = now + max(60, int(ttl_sec))
    nonce = secrets.token_urlsafe(8)
    payload = f"v1.{tenant_id}.{exp}.{nonce}"
    sig = _hmac_sign(secret, payload)
    return f"{payload}.{sig}"


def _is_special_user(request: Request) -> bool:
    secret = (os.getenv("SPECIAL_OCR_SECRET") or "").strip()
    if not secret:
        return False
    token = (request.cookies.get("special_ocr") or "").strip()
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 5:
        return False
    base = ".".join(parts[:4])
    sig = parts[4]
    expect = _hmac_sign(secret, base)
    if not hmac.compare_digest(expect, sig):
        return False
    try:
        exp = int(parts[2])
    except ValueError:
        return False
    if exp <= int(time.time()):
        return False
    return True


def _gpu_daily_limit() -> int:
    try:
        v = int((os.getenv("GPU_OCR_DAILY_LIMIT") or "1").strip() or "1")
    except ValueError:
        v = 1
    return max(0, min(v, 100))


def _gpu_monthly_limit() -> int:
    try:
        v = int((os.getenv("GPU_OCR_MONTHLY_LIMIT") or "20").strip() or "20")
    except ValueError:
        v = 20
    return max(0, min(v, 100000))


def _gpu_daily_page_limit() -> int:
    # 默认：普通用户每日 100 页
    try:
        v = int((os.getenv("GPU_OCR_DAILY_PAGE_LIMIT") or "100").strip() or "100")
    except ValueError:
        v = 100
    return max(0, min(v, 100000))


def _gpu_monthly_global_page_limit() -> int:
    # 默认：全站每月 3000 页（北京时间月）
    try:
        v = int((os.getenv("GPU_OCR_MONTHLY_GLOBAL_PAGE_LIMIT") or "3000").strip() or "3000")
    except ValueError:
        v = 3000
    return max(0, min(v, 10**9))


def _gpu_scan_text_char_threshold() -> int:
    try:
        v = int((os.getenv("GPU_OCR_SCAN_TEXT_CHAR_THRESHOLD") or "200").strip() or "200")
    except ValueError:
        v = 200
    return max(0, min(v, 1000000))


def _gpu_redeem_secret() -> str:
    return (os.getenv("GPU_OCR_REDEEM_SECRET") or "").strip()


def _pay_provider_name() -> str:
    return (os.getenv("PAY_PROVIDER") or "easypay").strip().lower()


PAY_PAGE_PACKS: Dict[str, Dict[str, Any]] = {
    "A": {"pages": 500, "amount_cny": 9.9},
    "B": {"pages": 2000, "amount_cny": 29.9},
    "C": {"pages": 5000, "amount_cny": 59.9},
}


def _random_code_email_target() -> str:
    return (os.getenv("CODE_EMAIL_TO") or "").strip()


def _hash_random_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _gen_random_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def _random_code_ttl_sec() -> int:
    try:
        return max(60, int((os.getenv("CODE_EMAIL_TTL_SEC") or "600").strip() or "600"))
    except ValueError:
        return 600


def _verify_and_consume_random_code(tenant_id: str, client_id: str, purpose: str, code: str) -> bool:
    hashed = _hash_random_code((code or "").strip())
    conn = _conn()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        row = conn.execute(
            """
            SELECT id FROM email_random_codes
            WHERE tenant_id=? AND client_id=? AND purpose=? AND code_hash=? AND used=0 AND expires_at>?
            ORDER BY created_at DESC LIMIT 1
            """,
            (tenant_id, client_id, purpose, hashed, now_iso),
        ).fetchone()
        if not row:
            return False
        conn.execute("UPDATE email_random_codes SET used=1, used_at=CURRENT_TIMESTAMP WHERE id=?", (str(row["id"]),))
        conn.commit()
        return True
    finally:
        conn.close()


def _create_and_send_random_code(tenant_id: str, client_id: str, purpose: str) -> Dict[str, Any]:
    to_email = _random_code_email_target()
    if not to_email:
        raise HTTPException(status_code=503, detail="未配置 CODE_EMAIL_TO")
    code = _gen_random_code()
    ttl = _random_code_ttl_sec()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT created_at FROM email_random_codes
            WHERE tenant_id=? AND client_id=? AND purpose=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (tenant_id, client_id, purpose),
        ).fetchone()
        if row and row.get("created_at"):
            try:
                created_at = str(row["created_at"]).replace("Z", "+00:00")
                if " " in created_at and "T" not in created_at:
                    created_at = created_at.replace(" ", "T")
                dt = datetime.fromisoformat(created_at)
                if (datetime.now(timezone.utc) - dt).total_seconds() < 45:
                    raise HTTPException(status_code=429, detail="发送过于频繁，请稍后重试")
            except HTTPException:
                raise
            except Exception:
                pass
        code_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO email_random_codes(id, tenant_id, client_id, purpose, code_hash, target_email, expires_at, used)
            VALUES(?,?,?,?,?,?,?,0)
            """,
            (code_id, tenant_id, client_id, purpose, _hash_random_code(code), to_email, expires),
        )
        conn.commit()
    finally:
        conn.close()
    subject = f"sKrt 随机码（{purpose}）"
    body = f"你的随机码：{code}\n有效期：{ttl // 60} 分钟\n用途：{purpose}\n如非本人操作请忽略。"
    send_plain_email(subject=subject, body=body, to_email=to_email)
    return {"ok": True, "sent": True, "expires_in_sec": ttl, "to": to_email}


def _pay_refund_enabled() -> bool:
    return (os.getenv("PAY_REFUND_ENABLED") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _amount_to_fen(amount_cny: float) -> int:
    val = Decimal(str(amount_cny)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int((val * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP))


def _payjs_sign(payload: Dict[str, Any], key: str) -> str:
    items: List[str] = []
    for k in sorted(payload.keys()):
        v = payload.get(k)
        if k == "sign" or v is None:
            continue
        sv = str(v).strip()
        if sv == "":
            continue
        items.append(f"{k}={sv}")
    raw = "&".join(items) + f"&key={key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def _payjs_post_form(path: str, form: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{_payjs_api_base()}/{path.lstrip('/')}"
    body = urlencode({k: str(v) for k, v in form.items() if v is not None}).encode("utf-8")
    req = UrlRequest(url=url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req, timeout=15) as resp:  # nosec B310
        raw = resp.read().decode("utf-8", errors="ignore")
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {"return_code": 0, "status": 0, "msg": "invalid_json", "raw": raw}


def _new_order_no() -> str:
    return f"XM{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{secrets.token_hex(3).upper()}"


def _get_payment_provider() -> EasyPayProvider:
    provider = _pay_provider_name()
    if provider == "easypay":
        return EasyPayProvider()
    raise HTTPException(status_code=503, detail=f"不支持的支付通道: {provider}")


def _create_pay_order(tenant_id: str, client_id: str, pack_key: str, channel: str) -> Dict[str, Any]:
    pack = PAY_PAGE_PACKS.get(pack_key)
    if not pack:
        raise HTTPException(status_code=400, detail="无效页包")
    if channel not in {"wechat_native", "alipay_qr"}:
        raise HTTPException(status_code=400, detail="无效支付渠道")
    order_no = _new_order_no()
    pages = int(pack["pages"])
    amount_cny = float(pack["amount_cny"])
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO pay_orders(order_no, tenant_id, client_id, pack_key, pages, amount_cny, status, provider, channel)
            VALUES(?,?,?,?,?,?, 'pending', ?, ?)
            """,
            (order_no, tenant_id, client_id, pack_key, pages, amount_cny, _pay_provider_name(), channel),
        )
        conn.commit()
        return {"order_no": order_no, "pages": pages, "amount_cny": amount_cny, "channel": channel}
    finally:
        conn.close()


def _get_pay_order(order_no: str) -> Optional[Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM pay_orders WHERE order_no=?", (order_no,)).fetchone()
        return row
    finally:
        conn.close()


def _log_pay_callback(order_no: str, payload: Dict[str, Any], *, sign_ok: bool, handled: bool, result_text: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO pay_callbacks(provider, event_type, order_no, payload_text, sign_ok, handled, result_text)
            VALUES(?, 'notify', ?, ?, ?, ?, ?)
            """,
            (_pay_provider_name(), order_no, json.dumps(payload, ensure_ascii=False), int(sign_ok), int(handled), str(result_text)),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_order_paid_if_needed(order_no: str, transaction_id: str = "", provider_order_id: str = "") -> Dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM pay_orders WHERE order_no=?", (order_no,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="订单不存在")
        if str(row["status"]) == "paid":
            return dict(row)
        if str(row["status"]) == "refunded":
            return dict(row)
        tenant_id = str(row["tenant_id"])
        client_id = str(row["client_id"])
        pages = int(row["pages"] or 0)
        conn.execute(
            """
            INSERT INTO gpu_ocr_paid_pages_balance(tenant_id, client_id, pages_balance)
            VALUES(?, ?, ?)
            ON CONFLICT(tenant_id, client_id) DO UPDATE
            SET pages_balance = pages_balance + excluded.pages_balance, updated_at=CURRENT_TIMESTAMP
            """,
            (tenant_id, client_id, pages),
        )
        conn.execute(
            "INSERT INTO gpu_ocr_paid_pages_ledger(tenant_id, client_id, delta_pages, reason) VALUES(?,?,?,?)",
            (tenant_id, client_id, pages, f"pay_order_credit:{order_no}"),
        )
        conn.execute(
            """
            UPDATE pay_orders
            SET status='paid',
                credited_pages=?,
                payjs_transaction_id=COALESCE(NULLIF(?, ''), payjs_transaction_id),
                payjs_order_id=COALESCE(NULLIF(?, ''), payjs_order_id),
                paid_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE order_no=?
            """,
            (pages, transaction_id, provider_order_id, order_no),
        )
        conn.commit()
        row2 = conn.execute("SELECT * FROM pay_orders WHERE order_no=?", (order_no,)).fetchone()
        return dict(row2) if row2 else dict(row)
    finally:
        conn.close()


def _refund_order_pages(order_no: str) -> Dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM pay_orders WHERE order_no=?", (order_no,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="订单不存在")
        if str(row["status"]) not in {"paid", "refund_pending_settlement"}:
            raise HTTPException(status_code=400, detail="订单状态不允许退款")
        tenant_id = str(row["tenant_id"])
        client_id = str(row["client_id"])
        credit_total = int(row["credited_pages"] or 0)
        reverted = int(row["reverted_pages"] or 0)
        to_revert = max(0, credit_total - reverted)
        if to_revert <= 0:
            return dict(row)
        b = conn.execute(
            "SELECT pages_balance FROM gpu_ocr_paid_pages_balance WHERE tenant_id=? AND client_id=?",
            (tenant_id, client_id),
        ).fetchone()
        balance = int(b["pages_balance"]) if b else 0
        if balance < to_revert:
            conn.execute(
                "UPDATE pay_orders SET status='refund_pending_settlement', refund_status='pending_settlement', updated_at=CURRENT_TIMESTAMP WHERE order_no=?",
                (order_no,),
            )
            conn.commit()
            row2 = conn.execute("SELECT * FROM pay_orders WHERE order_no=?", (order_no,)).fetchone()
            return dict(row2) if row2 else dict(row)
        conn.execute(
            """
            INSERT INTO gpu_ocr_paid_pages_balance(tenant_id, client_id, pages_balance)
            VALUES(?, ?, ?)
            ON CONFLICT(tenant_id, client_id) DO UPDATE
            SET pages_balance = pages_balance + excluded.pages_balance, updated_at=CURRENT_TIMESTAMP
            """,
            (tenant_id, client_id, -to_revert),
        )
        conn.execute(
            "INSERT INTO gpu_ocr_paid_pages_ledger(tenant_id, client_id, delta_pages, reason) VALUES(?,?,?,?)",
            (tenant_id, client_id, -to_revert, f"pay_order_refund:{order_no}"),
        )
        conn.execute(
            """
            UPDATE pay_orders
            SET status='refunded',
                refund_status='refunded',
                reverted_pages=reverted_pages + ?,
                refunded_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE order_no=?
            """,
            (to_revert, order_no),
        )
        conn.commit()
        row3 = conn.execute("SELECT * FROM pay_orders WHERE order_no=?", (order_no,)).fetchone()
        return dict(row3) if row3 else dict(row)
    finally:
        conn.close()

def _totp_6_digits(secret: str, counter: int) -> str:
    # RFC6238 风格：HMAC-SHA1 + 动态截断，输出 6 位数字
    msg = counter.to_bytes(8, "big", signed=False)
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{code_int % 1_000_000:06d}"


def _verify_redeem_code(raw_code: str) -> bool:
    code = (raw_code or "").strip()
    if not code or len(code) != 6 or not code.isdigit():
        return False
    secret = _gpu_redeem_secret()
    if not secret:
        return False
    counter = int(time.time() // 30)
    for drift in (-1, 0, 1):
        if hmac.compare_digest(_totp_6_digits(secret, counter + drift), code):
            return True
    return False


def _get_gpu_paid_pages_balance(tenant_id: str, client_id: str) -> int:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT pages_balance FROM gpu_ocr_paid_pages_balance WHERE tenant_id=? AND client_id=?",
            (tenant_id, client_id),
        ).fetchone()
        return int(row["pages_balance"]) if row else 0
    finally:
        conn.close()


def _add_gpu_paid_pages(tenant_id: str, client_id: str, delta_pages: int, reason: str) -> int:
    delta = int(delta_pages or 0)
    if delta == 0:
        return _get_gpu_paid_pages_balance(tenant_id, client_id)
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO gpu_ocr_paid_pages_balance(tenant_id, client_id, pages_balance)
            VALUES(?, ?, ?)
            ON CONFLICT(tenant_id, client_id) DO UPDATE
            SET pages_balance = pages_balance + excluded.pages_balance, updated_at=CURRENT_TIMESTAMP
            """,
            (tenant_id, client_id, delta),
        )
        conn.execute(
            "INSERT INTO gpu_ocr_paid_pages_ledger(tenant_id, client_id, delta_pages, reason) VALUES(?,?,?,?)",
            (tenant_id, client_id, delta, str(reason or "")),
        )
        row = conn.execute(
            "SELECT pages_balance FROM gpu_ocr_paid_pages_balance WHERE tenant_id=? AND client_id=?",
            (tenant_id, client_id),
        ).fetchone()
        conn.commit()
        return int(row["pages_balance"]) if row else 0
    finally:
        conn.close()


def _consume_gpu_pages_with_paid_balance_or_raise(request: Request, tenant_id: str, page_count: int) -> None:
    if _is_special_user(request):
        return
    pages = max(1, int(page_count or 0))
    client_id = _client_id_from_request(request)

    paid = _get_gpu_paid_pages_balance(tenant_id, client_id)
    if paid > 0:
        use_paid = min(pages, paid)
        # 扣付费/赠送余额并写流水
        _add_gpu_paid_pages(tenant_id, client_id, -use_paid, reason="consume_gpu_ocr_pages")
        pages -= use_paid
    if pages <= 0:
        return

    # 剩余走免费额度（每日 / 全站月）
    _consume_gpu_page_quota_or_raise(request, tenant_id, pages)
    _consume_global_gpu_monthly_pages_or_raise(request, pages)


def _pdf_scan_decision(file_path: Path) -> Dict[str, Any]:
    """
    保守判定：若 PDF 可提取文字字符数 >= threshold，则认为无需 GPU OCR。
    返回:
      - need_gpu: bool
      - page_count: int (取不到则 1)
      - extracted_chars: int
    """
    threshold = _gpu_scan_text_char_threshold()
    page_count = 1
    extracted_chars = 0
    try:
        if file_path.suffix.lower() != ".pdf":
            return {"need_gpu": False, "page_count": 1, "extracted_chars": 0}
        from PyPDF2 import PdfReader  # type: ignore

        reader = PdfReader(str(file_path))
        pages = list(reader.pages)
        page_count = max(1, len(pages))
        if threshold <= 0:
            # 阈值为 0：只要能打开就认为无需 GPU
            return {"need_gpu": False, "page_count": page_count, "extracted_chars": 0}
        for p in pages:
            try:
                extracted_chars += len((p.extract_text() or "").strip())
            except Exception:
                pass
            if extracted_chars >= threshold:
                return {"need_gpu": False, "page_count": page_count, "extracted_chars": extracted_chars}
        return {"need_gpu": True, "page_count": page_count, "extracted_chars": extracted_chars}
    except Exception:
        # 无法读取或无法提取：倾向认为扫描版，且页数取不到时按 1 页保守扣减
        return {"need_gpu": True, "page_count": max(1, int(page_count)), "extracted_chars": int(extracted_chars)}


_EXTERNAL_OCR_SIZE_THRESHOLD_BYTES = 10 * 1024 * 1024


def _baidu_ocr_env_configured() -> bool:
    return bool(os.getenv("BAIDU_OCR_API_KEY", "").strip() and os.getenv("BAIDU_OCR_SECRET_KEY", "").strip())


def _ocr_http_api_base_configured() -> bool:
    return bool((os.getenv("OCR_API_BASE") or os.getenv("GPU_OCR_ENDPOINT") or "").strip())


def _external_ocr_billing_enabled() -> bool:
    if _baidu_ocr_env_configured():
        return True
    return _ocr_http_api_base_configured()


def _external_ocr_scan_quota_result(
    *,
    final_path: Path,
    target_filename: str,
    actual_size: int,
    use_gpu_ocr_req: bool,
    external_ocr_confirmed: bool,
    request: Request,
    tenant_id: str,
    upload_id: Optional[str] = None,
) -> Tuple[bool, Optional[JSONResponse]]:
    """
    扫描版 PDF 且启用外部 OCR 计费时扣减额度；>10MB 且未确认则返回 409。
    返回 (upload_tasks.use_gpu_ocr 标记, 若非空则直接作为 HTTP 响应返回)。
    """
    is_pdf = Path(target_filename).suffix.lower() == ".pdf"
    if not is_pdf or not use_gpu_ocr_req:
        return False, None
    if not _external_ocr_billing_enabled():
        return False, None
    decision = _pdf_scan_decision(final_path)
    need_scan = bool(decision.get("need_gpu"))
    if not need_scan:
        return False, None
    auto_large = actual_size > _EXTERNAL_OCR_SIZE_THRESHOLD_BYTES
    if auto_large and not external_ocr_confirmed:
        payload: Dict[str, Any] = {
            "code": "external_ocr_confirm_required",
            "message": "此为扫描件，因处理器受限，超过10MB的需要调用外部OCR，是否继续",
            "page_count": int(decision.get("page_count") or 1),
        }
        if upload_id:
            payload["upload_id"] = upload_id
        return False, JSONResponse(status_code=409, content=payload)
    pages = int(decision.get("page_count") or 1)
    _consume_gpu_pages_with_paid_balance_or_raise(request, tenant_id, pages)
    return True, None


def _consume_global_gpu_monthly_or_raise(request: Request) -> None:
    if _is_special_user(request):
        return
    limit = _gpu_monthly_limit()
    if limit <= 0:
        raise HTTPException(status_code=403, detail="外部 OCR 未开放")
    month_key = _month_key_beijing()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT used FROM gpu_ocr_global_monthly_usage WHERE month_key=?",
            (month_key,),
        ).fetchone()
        used = int(row["used"]) if row else 0
        if used >= limit:
            raise HTTPException(status_code=429, detail="本月全站外部 OCR 额度已用完（测试版）")
        if row:
            conn.execute(
                "UPDATE gpu_ocr_global_monthly_usage SET used = used + 1, updated_at = CURRENT_TIMESTAMP WHERE month_key=?",
                (month_key,),
            )
        else:
            conn.execute(
                "INSERT INTO gpu_ocr_global_monthly_usage(month_key, used) VALUES(?, 1)",
                (month_key,),
            )
        conn.commit()
    finally:
        conn.close()


def _consume_gpu_page_quota_or_raise(request: Request, tenant_id: str, page_count: int) -> None:
    if _is_special_user(request):
        return
    pages = max(1, int(page_count or 0))
    daily_limit = _gpu_daily_page_limit()
    if daily_limit <= 0:
        raise HTTPException(status_code=403, detail="外部 OCR 未开放")
    client_id = _client_id_from_request(request)
    day = _day_key_beijing()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT pages_used FROM gpu_ocr_daily_pages WHERE tenant_id=? AND client_id=? AND day=?",
            (tenant_id, client_id, day),
        ).fetchone()
        used = int(row["pages_used"]) if row else 0
        if used + pages > daily_limit:
            raise HTTPException(status_code=429, detail="已超出剩余外部 OCR 额度")
        if row:
            conn.execute(
                "UPDATE gpu_ocr_daily_pages SET pages_used = pages_used + ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE tenant_id=? AND client_id=? AND day=?",
                (pages, tenant_id, client_id, day),
            )
        else:
            conn.execute(
                "INSERT INTO gpu_ocr_daily_pages(tenant_id, client_id, day, pages_used) VALUES(?,?,?,?)",
                (tenant_id, client_id, day, pages),
            )
        conn.commit()
    finally:
        conn.close()


def _consume_global_gpu_monthly_pages_or_raise(request: Request, page_count: int) -> None:
    if _is_special_user(request):
        return
    pages = max(1, int(page_count or 0))
    limit = _gpu_monthly_global_page_limit()
    if limit <= 0:
        raise HTTPException(status_code=403, detail="外部 OCR 未开放")
    month_key = _month_key_beijing()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT pages_used FROM gpu_ocr_global_monthly_pages WHERE month_key=?",
            (month_key,),
        ).fetchone()
        used = int(row["pages_used"]) if row else 0
        if used + pages > limit:
            raise HTTPException(status_code=429, detail="已超出剩余外部 OCR 额度")
        if row:
            conn.execute(
                "UPDATE gpu_ocr_global_monthly_pages SET pages_used = pages_used + ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE month_key=?",
                (pages, month_key),
            )
        else:
            conn.execute(
                "INSERT INTO gpu_ocr_global_monthly_pages(month_key, pages_used) VALUES(?, ?)",
                (month_key, pages),
            )
        conn.commit()
    finally:
        conn.close()


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/auth/special-ocr/unlock")
async def unlock_special_ocr(request: Request, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    raw_key = str(body.get("key") or "").strip()
    if not raw_key:
        raise HTTPException(status_code=400, detail="缺少随机码")
    secret = (os.getenv("SPECIAL_OCR_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="未配置 SPECIAL_OCR_SECRET")
    try:
        ttl = int((os.getenv("SPECIAL_OCR_TTL_SEC") or str(30 * 86400)).strip() or str(30 * 86400))
    except ValueError:
        ttl = 30 * 86400
    identity = _get_request_identity(request)
    tenant_id = str(identity.get("tenant_id", "public"))
    client_id = _client_id_from_request(request)
    if not _verify_and_consume_random_code(tenant_id, client_id, "special_unlock", raw_key):
        raise HTTPException(status_code=403, detail="随机码错误或已过期")
    token = _make_special_cookie(secret, tenant_id, ttl_sec=ttl)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "special_ocr",
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=max(60, ttl),
        path="/",
    )
    return resp


@app.post("/auth/special-ocr/send-code")
async def send_special_unlock_code(request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    tenant_id = str(identity.get("tenant_id", "public"))
    client_id = _client_id_from_request(request)
    try:
        return _create_and_send_random_code(tenant_id, client_id, "special_unlock")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("send special unlock code failed")
        raise HTTPException(status_code=502, detail=f"发送失败: {exc}")


@app.get("/gpu/ocr/quota")
async def get_gpu_ocr_quota(request: Request) -> Dict[str, Any]:
    month_key = _month_key_beijing()
    limit = _gpu_monthly_global_page_limit()
    identity = _get_request_identity(request)
    tenant_id = str(identity.get("tenant_id", "public"))
    client_id = _client_id_from_request(request)
    day = _day_key_beijing()
    daily_limit = _gpu_daily_page_limit()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT pages_used FROM gpu_ocr_global_monthly_pages WHERE month_key=?",
            (month_key,),
        ).fetchone()
        used = int(row["pages_used"]) if row else 0
        row2 = conn.execute(
            "SELECT pages_used FROM gpu_ocr_daily_pages WHERE tenant_id=? AND client_id=? AND day=?",
            (tenant_id, client_id, day),
        ).fetchone()
        daily_used = int(row2["pages_used"]) if row2 else 0
        row3 = conn.execute(
            "SELECT pages_balance FROM gpu_ocr_paid_pages_balance WHERE tenant_id=? AND client_id=?",
            (tenant_id, client_id),
        ).fetchone()
        paid_balance = int(row3["pages_balance"]) if row3 else 0
    finally:
        conn.close()
    remaining = max(0, int(limit) - int(used))
    return {
        "month_key": month_key,
        "used": int(used),
        "limit": int(limit),
        "remaining": int(remaining),
        "daily_used": int(daily_used),
        "daily_limit": int(daily_limit),
        "paid_balance": int(max(0, paid_balance)),
        "special": bool(_is_special_user(request)),
    }


@app.post("/gpu/autostart/start")
async def gpu_autostart_start_route(request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.upload.write")
    if not gpu_autostart_enabled():
        raise HTTPException(status_code=503, detail="GPU 自动启停未启用或未配置")
    try:
        result = await asyncio.to_thread(start_gpu_instances)
        return {"ok": True, **result}
    except Exception as exc:
        logger.exception("gpu autostart start failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/gpu/autostart/stop")
async def gpu_autostart_stop_route(request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.upload.write")
    if not gpu_autostart_enabled():
        raise HTTPException(status_code=503, detail="GPU 自动启停未启用或未配置")
    try:
        result = await asyncio.to_thread(stop_gpu_instances)
        return {"ok": True, **result}
    except Exception as exc:
        logger.exception("gpu autostart stop failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/gpu/ocr/redeem")
async def redeem_gpu_ocr_pages(request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    raw_code = str(body.get("code") or "").strip()
    if not raw_code:
        raise HTTPException(status_code=400, detail="缺少随机码")
    identity = _get_request_identity(request)
    tenant_id = str(identity.get("tenant_id", "public"))
    client_id = _client_id_from_request(request)
    if not _verify_and_consume_random_code(tenant_id, client_id, "gpu_redeem", raw_code):
        raise HTTPException(status_code=403, detail="随机码错误或已过期")
    new_balance = _add_gpu_paid_pages(tenant_id, client_id, 500, reason="redeem_500_pages")
    return {"ok": True, "delta_pages": 500, "paid_balance": int(max(0, new_balance))}


@app.post("/gpu/ocr/redeem/send-code")
async def send_gpu_redeem_code(request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    tenant_id = str(identity.get("tenant_id", "public"))
    client_id = _client_id_from_request(request)
    try:
        return _create_and_send_random_code(tenant_id, client_id, "gpu_redeem")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("send gpu redeem code failed")
        raise HTTPException(status_code=502, detail=f"发送失败: {exc}")


@app.post("/gpu/ocr/pay/order/create")
async def create_gpu_pay_order(request: Request, body: PayOrderCreateRequest) -> Dict[str, Any]:
    provider = _get_payment_provider()
    notify_url = (os.getenv("PAY_NOTIFY_URL") or os.getenv("EASYPAY_NOTIFY_URL") or "").strip()
    if not notify_url:
        raise HTTPException(status_code=503, detail="未配置 PAY_NOTIFY_URL/EASYPAY_NOTIFY_URL")
    identity = _get_request_identity(request)
    tenant_id = str(identity.get("tenant_id", "public"))
    client_id = _client_id_from_request(request)
    created = _create_pay_order(tenant_id, client_id, body.pack_key, body.channel)
    order_no = str(created["order_no"])
    pages = int(created["pages"])
    amount_cny = float(created["amount_cny"])
    channel = str(created["channel"])
    total_fee = _amount_to_fen(amount_cny)
    try:
        created_rsp = provider.create_order(
            order_no=order_no,
            amount_fen=total_fee,
            channel=channel,
            subject=f"sKrt 页包{body.pack_key} {pages}页",
            notify_url=notify_url,
        )
    except Exception as exc:
        logger.exception("create payment order failed provider=%s", _pay_provider_name())
        raise HTTPException(status_code=502, detail=f"支付下单失败: {exc}")
    payjs_order_id = str(created_rsp.provider_order_id or "")
    conn = _conn()
    try:
        conn.execute(
            "UPDATE pay_orders SET payjs_order_id=?, updated_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (payjs_order_id, order_no),
        )
        conn.commit()
    finally:
        conn.close()
    code_url = str(created_rsp.code_url or "")
    qr_image_url = f"https://api.qrserver.com/v1/create-qr-code/?size=240x240&data={quote_plus(code_url)}" if code_url else ""
    return {
        "ok": True,
        "order_no": order_no,
        "pack_key": body.pack_key,
        "channel": channel,
        "pages": pages,
        "amount_cny": amount_cny,
        "status": "pending",
        "code_url": code_url,
        "qr_image_url": qr_image_url,
    }


@app.get("/gpu/ocr/pay/order/{order_no}")
async def get_gpu_pay_order(order_no: str, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    tenant_id = str(identity.get("tenant_id", "public"))
    client_id = _client_id_from_request(request)
    row = _get_pay_order(order_no)
    if not row:
        raise HTTPException(status_code=404, detail="订单不存在")
    order = dict(row)
    if str(order.get("tenant_id")) != tenant_id or str(order.get("client_id")) != client_id:
        raise HTTPException(status_code=403, detail="订单不属于当前用户")
    return {
        "ok": True,
        "order_no": str(order.get("order_no") or ""),
        "status": str(order.get("status") or "pending"),
        "channel": str(order.get("channel") or "wechat_native"),
        "refund_status": str(order.get("refund_status") or "none"),
        "pack_key": str(order.get("pack_key") or ""),
        "pages": int(order.get("pages") or 0),
        "amount_cny": float(order.get("amount_cny") or 0),
        "credited_pages": int(order.get("credited_pages") or 0),
        "reverted_pages": int(order.get("reverted_pages") or 0),
    }


@app.post("/gpu/ocr/pay/notify")
async def notify_gpu_pay_order(request: Request) -> Dict[str, Any]:
    provider = _get_payment_provider()
    payload: Dict[str, Any] = {}
    try:
        form = await request.form()
        payload = {str(k): str(v) for k, v in form.items()}
    except Exception:
        payload = {}
    if not payload:
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                payload = {str(k): str(v) for k, v in raw.items()}
        except Exception:
            payload = {}
    try:
        notify_result = provider.verify_notify(payload)
    except Exception:
        order_no = str(payload.get("out_trade_no") or "")
        _log_pay_callback(order_no, payload, sign_ok=False, handled=False, result_text="bad_sign")
        raise HTTPException(status_code=403, detail="invalid_sign")
    order_no = notify_result.order_no
    if not notify_result.paid:
        status_val = str(payload.get("trade_status") or payload.get("status") or "")
        _log_pay_callback(order_no, payload, sign_ok=True, handled=False, result_text=f"ignore_status:{status_val}")
        return {"ok": True, "ignored": True}
    order = _mark_order_paid_if_needed(
        order_no=order_no,
        transaction_id=notify_result.transaction_id,
        provider_order_id=notify_result.provider_order_id,
    )
    _log_pay_callback(order_no, payload, sign_ok=True, handled=True, result_text="credited")
    return {"ok": True, "order_no": order_no, "status": str(order.get("status") or "paid")}


@app.post("/gpu/ocr/pay/order/{order_no}/refund")
async def refund_gpu_pay_order(order_no: str, request: Request, body: PayOrderRefundRequest = Body(default=PayOrderRefundRequest())) -> Dict[str, Any]:
    if not _pay_refund_enabled():
        raise HTTPException(status_code=403, detail="退款功能未开启")
    admin_key = (os.getenv("PAY_REFUND_ADMIN_KEY") or "").strip()
    if admin_key and str(body.key or "").strip() != admin_key:
        raise HTTPException(status_code=403, detail="退款密钥错误")
    row = _get_pay_order(order_no)
    if not row:
        raise HTTPException(status_code=404, detail="订单不存在")
    identity = _get_request_identity(request)
    tenant_id = str(identity.get("tenant_id", "public"))
    client_id = _client_id_from_request(request)
    order = dict(row)
    if str(order.get("tenant_id")) != tenant_id or str(order.get("client_id")) != client_id:
        raise HTTPException(status_code=403, detail="订单不属于当前用户")
    provider = _get_payment_provider()
    payjs_order_id = str(order.get("payjs_order_id") or "")
    try:
        provider.refund(order_no=order_no, provider_order_id=payjs_order_id)
    except Exception:
        logger.exception("payment refund api failed, continue with local settlement")
    new_order = _refund_order_pages(order_no)
    return {
        "ok": True,
        "order_no": order_no,
        "status": str(new_order.get("status") or ""),
        "refund_status": str(new_order.get("refund_status") or ""),
        "reverted_pages": int(new_order.get("reverted_pages") or 0),
    }


@app.post("/ingestion/runpod/callback")
async def runpod_ingestion_callback(body: RunpodIngestionCallbackRequest) -> Dict[str, Any]:
    if not _verify_runpod_callback_signature(body.task_id, body.tenant_id, body.status, body.signature):
        raise HTTPException(status_code=403, detail="invalid_signature")
    conn = _conn()
    try:
        conn.execute(
            "UPDATE runpod_jobs SET status=?, updated_at=CURRENT_TIMESTAMP, runpod_job_id=COALESCE(NULLIF(?,''),runpod_job_id), error_text=COALESCE(NULLIF(?,''), error_text) WHERE task_id=?",
            (body.status, str(body.runpod_job_id or ""), str(body.error_message or ""), body.task_id),
        )
        if body.status == "failed":
            conn.execute(
                "UPDATE upload_tasks SET status='failed', phase='failed', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(body.error_message or "runpod_failed"), body.task_id),
            )
        elif body.status == "completed":
            # RunPod 已完成 OCR+解析+向量化并写库，仅更新任务状态并补建关系图。
            conn.execute(
                "UPDATE upload_tasks SET status='completed', phase='completed', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (body.task_id,),
            )
        else:
            conn.execute(
                "UPDATE upload_tasks SET status='running', phase='running', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (body.task_id,),
            )
        conn.commit()
    finally:
        conn.close()
    if body.status == "completed":
        try:
            await _rebuild_kg_relations(tenant_id=body.tenant_id)
        except Exception:
            logger.exception("runpod callback rebuild relations failed task_id=%s", body.task_id)
    if body.status in {"completed", "failed"}:
        schedule_gpu_idle_stop(assume_gpu=True)
    return {"ok": True}


@app.post("/admin/gpu/ocr/quota/reset")
async def reset_gpu_ocr_quota(request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    key = str(body.get("key") or "").strip()
    reset_key = (os.getenv("GPU_OCR_RESET_KEY") or "").strip()
    if not reset_key:
        raise HTTPException(status_code=503, detail="未配置 GPU_OCR_RESET_KEY")
    if key != reset_key:
        raise HTTPException(status_code=403, detail="密钥错误")
    month_key = _month_key_beijing()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO gpu_ocr_global_monthly_pages(month_key, pages_used) VALUES(?, 0) "
            "ON CONFLICT(month_key) DO UPDATE SET pages_used=0, updated_at=CURRENT_TIMESTAMP",
            (month_key,),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "month_key": month_key, "used": 0}


@app.middleware("http")
async def request_security_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id", uuid.uuid4().hex)
    started = time.time()
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
    except HTTPException as exc:
        identity = getattr(request.state, "identity", None)
        if exc.status_code in {401, 403}:
            _security_event(
                request=request,
                identity=identity if isinstance(identity, dict) else None,
                event_type="authz_denied" if exc.status_code == 403 else "authn_failed",
                severity="high",
                message=str(exc.detail),
                details={"path": str(request.url.path), "method": request.method, "request_id": request_id},
            )
        raise
    except Exception as exc:
        identity = getattr(request.state, "identity", None)
        _security_event(
            request=request,
            identity=identity if isinstance(identity, dict) else None,
            event_type="server_error",
            severity="critical",
            message=str(exc),
            details={"path": str(request.url.path), "method": request.method, "request_id": request_id},
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "internal_server_error", "request_id": request_id, "elapsed_ms": int((time.time() - started) * 1000)},
        )

ai_router = FreeAIRouter(RUNTIME_CONFIG)
parser = DocumentParser()
chunker = DocumentChunker()
rag_engine = RAGEngine(ai_router)
agent_chains = AgentChains(ai_router=ai_router, rag_engine=rag_engine)
kg_builder = KGBuilder()
exam_processor = ExamProcessor(rag_engine, ai_router, agent_chains=agent_chains)
upload_ingestion_service = UploadIngestionService(
    db_path=str(DB_PATH),
    upload_dir=str(UPLOAD_DIR),
    ai_router=ai_router,
    agent_chains=agent_chains,
    parser=parser,
    runtime_config=RUNTIME_CONFIG,
)
deep_pipeline_service = DeepPipelineService(
    database_url=RUNTIME_CONFIG.postgres.database_url,
    rag_engine=rag_engine,
    ai_router=ai_router,
    pipeline_defaults=RUNTIME_CONFIG.pipeline.as_dict(),
)
_ingestion_workers: Dict[int, asyncio.Task[Any]] = {}
_chunk_uploads: Dict[str, ChunkUploadMeta] = {}
_deep_pipeline_tasks: Dict[str, asyncio.Task[Any]] = {}


def _guess_doc_type(filename: str) -> str:
    lower = filename.lower()
    if any(x in lower for x in ["exam", "题", "试卷"]):
        return "exam"
    if any(x in lower for x in ["api", "tech", "技术", "接口"]):
        return "technical"
    if any(x in lower for x in ["project", "需求", "里程碑", "任务"]):
        return "project"
    return "academic"


def _conn() -> Any:
    return knowledge_store.connect()


def _normalize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    total_chunks = int(task.get("total_chunks", 0) or 0)
    processed_chunks = int(task.get("processed_chunks", 0) or 0)
    row = dict(task)
    prog = upload_ingestion_service.compute_task_progress(row)
    timing = UploadIngestionService.task_timing_snapshot(row)
    rollup = upload_ingestion_service.get_rollup_metrics()
    return {
        "task_id": int(task.get("id", 0)),
        "filename": str(task.get("filename", "")),
        "discipline": str(task.get("discipline", "all")),
        "document_type": str(task.get("document_type", "academic")),
        "status": str(task.get("status", "queued")),
        "phase": str(task.get("phase", task.get("status", "queued"))),
        "document_id": task.get("document_id"),
        "total_chunks": total_chunks,
        "processed_chunks": processed_chunks,
        # 整体进度：文本提取与索引加权，兼容旧前端仅看 progress_percent
        "progress_percent": prog["overall_progress_percent"],
        "extract_progress_percent": prog["extract_progress_percent"],
        "index_progress_percent": prog["index_progress_percent"],
        "error_message": task.get("error_message"),
        "retries": int(task.get("retries", 0) or 0),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "file_size_bytes": int(task.get("file_size_bytes", 0) or 0),
        "use_gpu_ocr": bool(int(task.get("use_gpu_ocr", 0) or 0) == 1),
        **timing,
        "rollup_task_count": rollup.get("rollup_task_count"),
        "rollup_avg_sec_per_mb_extract": rollup.get("avg_extract_sec_per_mb"),
        "rollup_avg_sec_per_page_extract": rollup.get("avg_extract_sec_per_page"),
    }


def _spawn_ingestion_worker(task_id: int, tenant_id: str) -> None:
    if runpod_enabled():
        try:
            callback_url = (os.getenv("RUNPOD_CALLBACK_URL") or "").strip()
            callback_secret = (os.getenv("RUNPOD_CALLBACK_SECRET") or "").strip()
            if not callback_url or not callback_secret:
                raise RuntimeError("未配置 RUNPOD_CALLBACK_URL/RUNPOD_CALLBACK_SECRET")
            payload = {
                "task_id": task_id,
                "tenant_id": tenant_id,
                "callback_url": callback_url,
                "callback_signature_hint": callback_secret[:6],
            }
            rsp = submit_ingestion_job(payload)
            conn = _conn()
            try:
                conn.execute(
                    """
                    INSERT INTO runpod_jobs(id, task_id, tenant_id, status, runpod_job_id, request_payload, response_payload)
                    VALUES(?, ?, ?, 'queued', ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        task_id,
                        tenant_id,
                        str(rsp.get("id") or rsp.get("job_id") or ""),
                        json.dumps(payload, ensure_ascii=False),
                        json.dumps(rsp, ensure_ascii=False),
                    ),
                )
                conn.execute(
                    "UPDATE upload_tasks SET status='running', phase='queued', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (task_id,),
                )
                conn.commit()
            finally:
                conn.close()
            return
        except Exception:
            logger.exception("RunPod 入队失败，回退进程内 worker task_id=%s", task_id)
    try:
        from backend.services.ingestion_rq import enqueue_ingestion, ingestion_use_rq

        if ingestion_use_rq():
            enqueue_ingestion(task_id, tenant_id)
            return
    except ImportError:
        pass
    except Exception:
        logger.exception("RQ 入队失败，回退进程内 worker task_id=%s", task_id)
    running = _ingestion_workers.get(task_id)
    if running and not running.done():
        return
    _ingestion_workers[task_id] = asyncio.create_task(_run_ingestion_worker(task_id, tenant_id))


async def _run_ingestion_worker(task_id: int, tenant_id: str) -> None:
    t0 = time.perf_counter()
    log_ingestion_event("worker_start", task_id=task_id, tenant_id=tenant_id)
    try:
        await upload_ingestion_service.run_task(task_id, tenant_id=tenant_id)
        await _rebuild_kg_relations(tenant_id=tenant_id)
        log_ingestion_event(
            "worker_done",
            task_id=task_id,
            tenant_id=tenant_id,
            elapsed_sec=round(time.perf_counter() - t0, 3),
        )
    except Exception:
        log_ingestion_event(
            "worker_failed",
            task_id=task_id,
            tenant_id=tenant_id,
            elapsed_sec=round(time.perf_counter() - t0, 3),
        )
        logger.exception("ingestion worker failed task_id=%s", task_id)
    finally:
        _ingestion_workers.pop(task_id, None)
        schedule_gpu_idle_stop(task_id=task_id)


def _verify_runpod_callback_signature(task_id: int, tenant_id: str, status: str, signature: str) -> bool:
    secret = (os.getenv("RUNPOD_CALLBACK_SECRET") or "").strip()
    if not secret:
        return False
    msg = f"{task_id}:{tenant_id}:{status}"
    expected = _hmac_sign(secret, msg)
    return hmac.compare_digest(expected, signature)


def _safe_filename(value: str) -> str:
    name = (value or "").strip() or "uploaded.bin"
    sanitized = "".join(ch for ch in name if ch.isalnum() or ch in {"-", "_", ".", " ", "(", ")"})
    return sanitized.strip() or "uploaded.bin"


def _content_type_for_upload_suffix(suffix: str) -> str:
    s = (suffix or "").lower()
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".markdown": "text/markdown; charset=utf-8",
    }.get(s, "application/octet-stream")


def _unique_upload_basename(original_filename: str) -> str:
    """磁盘存储名：UUID 前缀 + 安全原始名，避免并发/重复上传互相覆盖。"""
    return f"{uuid.uuid4().hex}_{_safe_filename(original_filename)}"


def _chunk_path(upload_id: str, chunk_index: int) -> Path:
    return CHUNK_TEMP_DIR / upload_id / f"{chunk_index:08d}.part"


def _calc_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            block = fp.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


async def _save_upload_file_stream(upload_file: UploadFile, target_path: Path, read_chunk_size: int = 1024 * 1024) -> int:
    max_mb = 0
    try:
        max_mb = int((os.getenv("MAX_UPLOAD_MB") or "0").strip() or "0")
    except ValueError:
        max_mb = 0
    max_bytes = max_mb * 1024 * 1024 if max_mb > 0 else 0
    total = 0
    with open(target_path, "wb") as out:
        while True:
            block = await upload_file.read(read_chunk_size)
            if not block:
                break
            out.write(block)
            total += len(block)
            if max_bytes > 0 and total > max_bytes:
                try:
                    out.close()
                finally:
                    try:
                        target_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                await upload_file.close()
                raise HTTPException(status_code=413, detail=f"文件过大（>{max_mb}MB），请分拆或压缩后再上传")
    await upload_file.close()
    return total


async def _handle_completed_chunked_upload(
    target_path: Path, filename: str, discipline: str, document_type: str, purpose: str, tenant_id: str, use_gpu_ocr: bool
) -> Dict[str, Any]:
    if purpose == "docs":
        task = upload_ingestion_service.create_task(
            filename=filename,
            discipline=discipline if discipline != "auto" else "all",
            document_type=document_type or _guess_doc_type(filename),
            storage_basename=target_path.name,
            tenant_id=tenant_id,
        )
        upload_ingestion_service.update_task_use_gpu_ocr(int(task.get("id", 0)), bool(use_gpu_ocr))
        _spawn_ingestion_worker(int(task.get("id", 0)), tenant_id=tenant_id)
        return {"tasks": [_normalize_task(task)]}

    parsed = parser.parse(str(target_path), document_type or "exam")
    analysis = await exam_processor.analyze_and_answer_exam(parsed.text, discipline, tenant_id=tenant_id)
    return {
        "filename": filename,
        "discipline": discipline,
        "document_type": document_type or "exam",
        "analysis": analysis,
    }


def _env_bool(key: str, default: bool) -> bool:
    v = (os.getenv(key) or "").strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    return default


def _resolve_summary_compact_level(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed not in {0, 1, 2}:
        return default
    return parsed


def _resolve_summary_mode(value: Any, default: str = "fast") -> str:
    mode = str(value or "").strip().lower()
    if mode in {"fast", "full"}:
        return mode
    return default


@app.get("/health")
async def health() -> Dict[str, Any]:
    baidu_ocr_ok = bool(
        os.getenv("BAIDU_OCR_API_KEY", "").strip() and os.getenv("BAIDU_OCR_SECRET_KEY", "").strip()
    )
    pdf_ocr_engine = (os.getenv("PDF_OCR_ENGINE", "auto") or "auto").strip().lower()
    capacity = _capacity_snapshot()
    return {
        "status": "ok",
        "database": knowledge_store.health_database_label(),
        "storage": str(UPLOAD_DIR),
        "hybrid_local_first": RUNTIME_CONFIG.hybrid.local_first,
        "active_embedding_model": ai_router.get_active_embedding_model_id(),
        "llamaindex_enabled": RUNTIME_CONFIG.llama_index.enabled,
        "langchain_enabled": RUNTIME_CONFIG.langchain.enabled,
        "agent_graph_enabled": True,
        "postgres_pipeline_enabled": RUNTIME_CONFIG.postgres.enabled,
        "baidu_ocr_configured": baidu_ocr_ok,
        "pdf_ocr_engine": pdf_ocr_engine,
        "pdf_ocr_remote_first": _env_bool("PDF_OCR_REMOTE_FIRST", True) if baidu_ocr_ok else False,
        "tenant_header_name": RUNTIME_CONFIG.tenant.header_name,
        "tenant_require_header": RUNTIME_CONFIG.tenant.require_header,
        "auth_jwt_enabled": bool(RUNTIME_CONFIG.auth.enabled),
        "auth_membership_check": bool(RUNTIME_CONFIG.auth.require_membership_check),
        "capacity": capacity,
    }


@app.get("/capacity/status")
async def capacity_status(request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.metrics.read")
    snap = _capacity_snapshot()
    can_accept_new_jobs = not bool(snap.get("hard_exceeded"))
    quota = _tenant_quota_snapshot(identity)
    return {
        **snap,
        "can_accept_new_jobs": can_accept_new_jobs,
        "pause_on_hard_limit": bool(RUNTIME_CONFIG.capacity.pause_on_hard_limit),
        "tenant_quota": quota,
    }


@app.get("/tenant/quota/status")
async def tenant_quota_status(request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.metrics.read")
    return _tenant_quota_snapshot(identity)


@app.get("/security/baseline")
async def security_baseline(request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.security.read")
    checks = {
        "auth_jwt_enabled": bool(RUNTIME_CONFIG.auth.enabled),
        "postgres_enabled": bool(RUNTIME_CONFIG.postgres.enabled),
        "membership_check_enabled": bool(RUNTIME_CONFIG.auth.require_membership_check),
        "tenant_header_deprecated": bool(RUNTIME_CONFIG.auth.enabled),
        "capacity_guard_enabled": bool(RUNTIME_CONFIG.capacity.pause_on_hard_limit),
    }
    return {
        "checks": checks,
        "all_passed": all(checks.values()),
        "tenant_id": str(identity.get("tenant_id", "public")),
    }


@app.post("/upload/chunks/init")
async def init_chunk_upload(req: ChunkInitRequest, request: Request) -> Dict[str, Any]:
    _ensure_capacity_for_write()
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.upload.write")
    _enforce_tenant_quota(identity)
    tenant_id = str(identity.get("tenant_id", "public"))
    filename = _safe_filename(req.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md", ".markdown"}:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {filename}")
    if req.total_chunks > 10000:
        raise HTTPException(status_code=400, detail="分片数量过大，请增大分片大小")

    upload_id = uuid.uuid4().hex
    temp_dir = CHUNK_TEMP_DIR / upload_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    _chunk_uploads[upload_id] = {
        "upload_id": upload_id,
        "filename": filename,
        "total_size": int(req.total_size),
        "total_chunks": int(req.total_chunks),
        "purpose": req.purpose,
        "discipline": req.discipline,
        "document_type": req.document_type,
        "use_gpu_ocr": bool(req.use_gpu_ocr),
        "received_chunks": 0,
        "temp_dir": str(temp_dir),
        "tenant_id": tenant_id,
    }
    return {"upload_id": upload_id, "filename": filename, "total_chunks": int(req.total_chunks)}


@app.put("/upload/chunks/{upload_id}")
async def put_chunk(upload_id: str, chunk_index: int, request: Request, chunk: UploadFile = File(...)) -> Dict[str, Any]:
    meta = _chunk_uploads.get(upload_id)
    if not meta:
        raise HTTPException(status_code=404, detail="upload_id 不存在或已过期")
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.upload.write")
    if str(meta.get("tenant_id", "")) != str(identity.get("tenant_id", "public")):
        raise HTTPException(status_code=403, detail="租户不匹配，禁止跨租户上传分片。")
    if chunk_index < 0 or chunk_index >= int(meta["total_chunks"]):
        raise HTTPException(status_code=400, detail="chunk_index 超出范围")

    part_path = _chunk_path(upload_id, chunk_index)
    part_path.parent.mkdir(parents=True, exist_ok=True)
    await _save_upload_file_stream(chunk, part_path, read_chunk_size=512 * 1024)
    meta["received_chunks"] = len(list(Path(meta["temp_dir"]).glob("*.part")))
    return {"upload_id": upload_id, "chunk_index": chunk_index, "received_chunks": int(meta["received_chunks"])}


@app.post("/upload/chunks/{upload_id}/complete")
async def complete_chunk_upload(
    upload_id: str, request: Request, req: Optional[ChunkCompleteRequest] = Body(default=None)
) -> Any:
    meta = _chunk_uploads.get(upload_id)
    if not meta:
        raise HTTPException(status_code=404, detail="upload_id 不存在或已过期")
    _ensure_capacity_for_write()
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.upload.write")
    tenant_id = str(identity.get("tenant_id", "public"))
    if str(meta.get("tenant_id", "")) != tenant_id:
        raise HTTPException(status_code=403, detail="租户不匹配，禁止跨租户完成上传。")

    purpose = (req.purpose if req else meta["purpose"]) if req else meta["purpose"]
    discipline = (req.discipline if req else meta["discipline"]) if req else meta["discipline"]
    document_type = (req.document_type if req else meta["document_type"]) if req else meta["document_type"]
    external_ocr_confirmed = bool(req.external_ocr_confirmed) if req else False

    temp_dir = Path(meta["temp_dir"])
    total_chunks = int(meta["total_chunks"])
    resuming = bool(meta.get("awaiting_external_ocr_confirm")) and bool(meta.get("pending_final_path"))

    if resuming:
        if not external_ocr_confirmed:
            raise HTTPException(status_code=400, detail="请在确认调用外部 OCR 后继续")
        final_path = Path(str(meta["pending_final_path"]))
        if not final_path.exists():
            _chunk_uploads.pop(upload_id, None)
            raise HTTPException(status_code=410, detail="待确认文件已过期，请重新上传")
        storage_name = str(meta["pending_storage_basename"])
        target_filename = str(meta["pending_target_filename"])
        actual_size = final_path.stat().st_size
        use_gpu_ocr = bool(actual_size > _EXTERNAL_OCR_SIZE_THRESHOLD_BYTES)
    else:
        missing = [idx for idx in range(total_chunks) if not _chunk_path(upload_id, idx).exists()]
        if missing:
            raise HTTPException(status_code=400, detail=f"分片缺失: {missing[:6]}")

        target_filename = _safe_filename(meta["filename"])
        storage_name = _unique_upload_basename(meta["filename"])
        final_path = UPLOAD_DIR / storage_name
        with open(final_path, "wb") as out:
            for idx in range(total_chunks):
                part_file = _chunk_path(upload_id, idx)
                with open(part_file, "rb") as pf:
                    while True:
                        block = pf.read(1024 * 1024)
                        if not block:
                            break
                        out.write(block)

        actual_size = final_path.stat().st_size if final_path.exists() else 0
        expected_size = int(meta["total_size"])
        if expected_size > 0 and actual_size != expected_size:
            raise HTTPException(status_code=400, detail=f"文件大小校验失败 expected={expected_size} actual={actual_size}")

        use_gpu_ocr = bool((req.use_gpu_ocr if req and req.use_gpu_ocr is not None else meta.get("use_gpu_ocr")) or False)
        if actual_size > _EXTERNAL_OCR_SIZE_THRESHOLD_BYTES:
            use_gpu_ocr = True

    merged_sha256 = _calc_sha256(final_path) if final_path.exists() else ""

    use_task_gpu, ocr_resp = _external_ocr_scan_quota_result(
        final_path=final_path,
        target_filename=target_filename,
        actual_size=actual_size,
        use_gpu_ocr_req=use_gpu_ocr,
        external_ocr_confirmed=external_ocr_confirmed,
        request=request,
        tenant_id=tenant_id,
        upload_id=upload_id,
    )
    if ocr_resp is not None:
        if not resuming:
            meta["awaiting_external_ocr_confirm"] = True
            meta["pending_final_path"] = str(final_path)
            meta["pending_storage_basename"] = storage_name
            meta["pending_target_filename"] = target_filename
            for idx in range(total_chunks):
                try:
                    _chunk_path(upload_id, idx).unlink(missing_ok=True)
                except Exception:
                    pass
            try:
                temp_dir.rmdir()
            except Exception:
                pass
        return ocr_resp

    use_gpu_ocr = use_task_gpu

    # 先持久化到对象存储（优先 R2），并把 upload_tasks.file_path 指向远端 URI（保证后续解析不依赖本地磁盘）
    r2_cfg = R2StorageConfig.from_env()
    r2_prefix = (os.getenv("R2_STORAGE_PREFIX") or "").strip().strip("/")
    r2_delete_local = _env_bool("R2_DELETE_LOCAL_AFTER_UPLOAD", True)
    if r2_cfg and purpose == "docs":
        object_key = f"{r2_prefix}/{tenant_id}/{storage_name}".strip("/")
        try:
            uri = r2_upload_file(r2_cfg, key=object_key, file_path=final_path)
            # 将 file_path 更新到远端，后续 ingestion worker 会自动 r2:// 下载到缓存再解析
            # 这里还没创建 task；在 create_task 后会再写一次，但保持一致：先把本地文件删掉以省磁盘
            if r2_delete_local:
                try:
                    final_path.unlink(missing_ok=True)
                except Exception:
                    pass
            # 通过 meta 暂存，交给 _handle_completed_chunked_upload 创建任务后写入
            meta["file_path_override"] = uri
        except Exception:
            logger.exception("upload to r2 failed for chunked upload (fallback to local file_path)")

    client_id = _client_id_from_request(request)
    if purpose == "docs":
        conn_th = _conn()
        try:
            enforce_upload_create_allowed(
                conn_th,
                tenant_id,
                client_id,
                1,
                in_memory_workers=len(_ingestion_workers),
            )
        finally:
            conn_th.close()

    result = await _handle_completed_chunked_upload(
        target_path=final_path,
        filename=target_filename,
        discipline=discipline,
        document_type=document_type,
        purpose=purpose,
        tenant_id=tenant_id,
        use_gpu_ocr=use_gpu_ocr,
    )

    # 若上面已写入 R2，则把 task.file_path 更新为 r2://...（并确保本地文件可删除）
    try:
        uri = meta.get("file_path_override")
        if uri and isinstance(result, dict) and result.get("tasks"):
            # result["tasks"] = [_normalize_task(task)] 其中 task_id 为 id
            tid = int(result["tasks"][0].get("task_id") or 0)
            if tid > 0:
                upload_ingestion_service.update_task_file_path(tid, str(uri))
    except Exception:
        logger.exception("update task file_path to r2 uri failed")

    if purpose == "docs":
        conn_rec = _conn()
        try:
            record_upload_tasks_created(conn_rec, tenant_id, client_id, 1)
        finally:
            conn_rec.close()

    for idx in range(total_chunks):
        part_file = _chunk_path(upload_id, idx)
        if part_file.exists():
            part_file.unlink()
    if temp_dir.exists():
        try:
            temp_dir.rmdir()
        except Exception:
            pass
    _chunk_uploads.pop(upload_id, None)

    return {
        "upload_id": upload_id,
        "filename": target_filename,
        "size": actual_size,
        "sha256": merged_sha256 or (_calc_sha256(final_path) if final_path.exists() else ""),
        "purpose": purpose,
        **result,
    }


@app.post("/upload")
async def upload_documents(
    request: Request,
    files: List[UploadFile] = File(...),
    discipline: str = "general",
    document_type: Optional[str] = None,
) -> Dict[str, Any]:
    _ensure_capacity_for_write()
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.upload.write")
    _enforce_tenant_quota(identity)
    tenant_id = str(identity.get("tenant_id", "public"))
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")

    result = []
    for f in files:
        if not f.filename:
            continue
        suffix = Path(f.filename).suffix.lower()
        if suffix not in {".pdf", ".docx", ".txt", ".md", ".markdown"}:
            raise HTTPException(status_code=400, detail=f"不支持的文件格式: {f.filename}")

        storage = _unique_upload_basename(f.filename)
        target = UPLOAD_DIR / storage
        await _save_upload_file_stream(f, target)

        dtype = document_type or _guess_doc_type(f.filename)
        parsed = parser.parse(str(target), dtype)
        merged_meta = dict(parsed.metadata)
        merged_meta["discipline"] = discipline if discipline != "auto" else parsed.metadata.get("discipline", "general")
        merged_meta["embedding_model"] = ai_router.get_active_embedding_model_id()
        chunks = chunker.chunk_document(parsed.text, dtype, parsed.metadata.get("title", f.filename))

        conn = _conn()
        try:
            doc_id = insert_returning_id(
                conn,
                """
                INSERT INTO documents (tenant_id, filename, title, discipline, document_type, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    f.filename,
                    parsed.metadata.get("title", f.filename),
                    merged_meta.get("discipline", "general"),
                    dtype,
                    json.dumps(merged_meta, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        await rag_engine.index_chunks_for_tenant(doc_id, chunks, tenant_id=tenant_id)
        result.append(
            {
                "document_id": doc_id,
                "filename": f.filename,
                "title": parsed.metadata.get("title", f.filename),
                "discipline": merged_meta.get("discipline", "general"),
                "document_type": dtype,
                "chunk_count": len(chunks),
            }
        )

    await _rebuild_kg_relations(tenant_id=tenant_id)
    return {"uploaded": result}


@app.post("/upload/tasks")
async def create_upload_tasks(
    request: Request,
    files: List[UploadFile] = File(...),
    discipline: str = "general",
    document_type: Optional[str] = None,
    use_gpu_ocr: bool = False,
    external_ocr_confirmed: bool = False,
) -> Any:
    _ensure_capacity_for_write()
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.upload.write")
    _enforce_tenant_quota(identity)
    tenant_id = str(identity.get("tenant_id", "public"))
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")

    valid_count = 0
    for f in files:
        if not f.filename:
            continue
        suffix = Path(f.filename).suffix.lower()
        if suffix not in {".pdf", ".docx", ".txt", ".md", ".markdown"}:
            raise HTTPException(status_code=400, detail=f"不支持的文件格式: {f.filename}")
        valid_count += 1
    if valid_count == 0:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")

    client_id = _client_id_from_request(request)
    conn_th = _conn()
    try:
        enforce_upload_create_allowed(
            conn_th,
            tenant_id,
            client_id,
            valid_count,
            in_memory_workers=len(_ingestion_workers),
        )
    finally:
        conn_th.close()

    tasks: List[Dict[str, Any]] = []
    r2_cfg = R2StorageConfig.from_env()
    r2_prefix = (os.getenv("R2_STORAGE_PREFIX") or "").strip().strip("/")
    r2_delete_local = _env_bool("R2_DELETE_LOCAL_AFTER_UPLOAD", True)

    sb_cfg = SupabaseStorageConfig.from_env()
    sb_prefix = (os.getenv("SUPABASE_STORAGE_PREFIX") or "").strip().strip("/")
    sb_delete_local = _env_bool("SUPABASE_DELETE_LOCAL_AFTER_UPLOAD", False)
    req_use_gpu_ocr = bool(use_gpu_ocr)
    for f in files:
        if not f.filename:
            continue
        suffix = Path(f.filename).suffix.lower()
        if suffix not in {".pdf", ".docx", ".txt", ".md", ".markdown"}:
            raise HTTPException(status_code=400, detail=f"不支持的文件格式: {f.filename}")

        storage = _unique_upload_basename(f.filename)
        target = UPLOAD_DIR / storage
        await _save_upload_file_stream(f, target)

        sz = target.stat().st_size if target.exists() else 0
        auto_large = bool(sz > _EXTERNAL_OCR_SIZE_THRESHOLD_BYTES)
        try_ocr = bool(req_use_gpu_ocr or auto_large)
        task_use_gpu, ocr_resp = _external_ocr_scan_quota_result(
            final_path=target,
            target_filename=f.filename,
            actual_size=sz,
            use_gpu_ocr_req=try_ocr,
            external_ocr_confirmed=external_ocr_confirmed,
            request=request,
            tenant_id=tenant_id,
            upload_id=None,
        )
        if ocr_resp is not None:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
            return ocr_resp

        dtype = document_type or _guess_doc_type(f.filename)
        task = upload_ingestion_service.create_task(
            filename=f.filename,
            discipline=discipline if discipline != "auto" else "all",
            document_type=dtype,
            storage_basename=storage,
            tenant_id=tenant_id,
        )
        upload_ingestion_service.update_task_use_gpu_ocr(int(task.get("id", 0)), bool(task_use_gpu))
        # 优先：R2（S3 兼容）持久化，并把 upload_tasks.file_path 指向 r2://bucket/key
        if r2_cfg:
            object_key = f"{r2_prefix}/{tenant_id}/{storage}".strip("/")
            try:
                uri = r2_upload_file(r2_cfg, key=object_key, file_path=target)
                upload_ingestion_service.update_task_file_path(int(task.get("id", 0)), uri)
                if r2_delete_local:
                    try:
                        target.unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                logger.exception("upload to r2 failed (fallback to local file_path)")
        # 兜底：Supabase Storage（若未启用 R2）
        elif sb_cfg:
            object_key = f"{sb_prefix}/{tenant_id}/{storage}".strip("/")
            try:
                uri = await upload_file(sb_cfg, key=object_key, file_path=target)
                upload_ingestion_service.update_task_file_path(int(task.get("id", 0)), uri)
                if sb_delete_local:
                    try:
                        target.unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                logger.exception("upload to supabase storage failed (fallback to local file_path)")

        _spawn_ingestion_worker(int(task.get("id", 0)), tenant_id=tenant_id)
        conn_rec = _conn()
        try:
            record_upload_tasks_created(conn_rec, tenant_id, client_id, 1)
        finally:
            conn_rec.close()
        tasks.append(_normalize_task(task))

    return {"tasks": tasks}


@app.post(
    "/upload/tasks/one",
    tags=["upload"],
    summary="单文件入库（Swagger 选文件）",
)
@app.post(
    "/upload/single",
    tags=["upload"],
    summary="单文件入库（别名，同上）",
)
async def create_upload_task_single_file(
    request: Request,
    file: UploadFile = File(..., description="单个 PDF / DOCX / TXT / MD"),
    discipline: str = "general",
    document_type: Optional[str] = None,
    external_ocr_confirmed: bool = False,
) -> Any:
    """与 POST /upload/tasks 相同，仅上传一个文件；在 /docs 里应出现标准 file 控件。若看不到：重启后端并强制刷新 /docs（Ctrl+F5）。"""
    return await create_upload_tasks(
        request=request,
        files=[file],
        discipline=discipline,
        document_type=document_type,
        external_ocr_confirmed=external_ocr_confirmed,
    )


@app.post("/upload/tasks/presign-init", tags=["upload"])
async def upload_tasks_presign_init(request: Request, body: UploadPresignInitRequest) -> Dict[str, Any]:
    """R2 预签名 PUT 直传：先创建占位任务，客户端上传完成后调用 presign-complete。"""
    _ensure_capacity_for_write()
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.upload.write")
    _enforce_tenant_quota(identity)
    tenant_id = str(identity.get("tenant_id", "public"))
    r2_cfg = R2StorageConfig.from_env()
    if not r2_cfg:
        raise HTTPException(status_code=503, detail="未配置 R2（R2_ENDPOINT 等），无法使用预签名直传")

    filename = _safe_filename(body.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md", ".markdown"}:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {filename}")

    client_id = _client_id_from_request(request)
    conn_th = _conn()
    try:
        enforce_upload_create_allowed(
            conn_th,
            tenant_id,
            client_id,
            1,
            in_memory_workers=len(_ingestion_workers),
        )
    finally:
        conn_th.close()

    storage = _unique_upload_basename(filename)
    dtype = body.document_type or _guess_doc_type(filename)
    disc = body.discipline if body.discipline != "auto" else "all"
    task = upload_ingestion_service.create_task_placeholder(
        filename=filename,
        discipline=disc,
        document_type=dtype,
        tenant_id=tenant_id,
        storage_basename=storage,
    )
    upload_ingestion_service.update_task_use_gpu_ocr(int(task.get("id", 0)), False)

    conn_rec = _conn()
    try:
        record_upload_tasks_created(conn_rec, tenant_id, client_id, 1)
    finally:
        conn_rec.close()

    r2_prefix = (os.getenv("R2_STORAGE_PREFIX") or "").strip().strip("/")
    object_key = f"{r2_prefix}/{tenant_id}/{storage}".strip("/")
    content_type = _content_type_for_upload_suffix(suffix)
    try:
        expires = int((os.getenv("R2_PRESIGN_EXPIRES_SEC") or "3600").strip() or "3600")
    except ValueError:
        expires = 3600
    expires = max(60, min(expires, 86400))
    try:
        upload_url = generate_presigned_put_url(
            r2_cfg, key=object_key, content_type=content_type, expires_in=expires
        )
    except Exception:
        logger.exception("generate_presigned_put_url failed")
        raise HTTPException(status_code=503, detail="预签名 URL 生成失败")

    return {
        "task_id": int(task.get("id", 0)),
        "upload_method": "PUT",
        "upload_url": upload_url,
        "headers": {"Content-Type": content_type},
        "object_key": object_key,
        "expires_in": expires,
    }


@app.post("/upload/tasks/{task_id}/presign-complete", tags=["upload"])
async def upload_tasks_presign_complete(
    task_id: int, request: Request, body: UploadPresignCompleteRequest
) -> Dict[str, Any]:
    """预签名上传完成后校验对象元数据、更新任务并启动解析（或 RQ 入队）。"""
    _ensure_capacity_for_write()
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.upload.write")
    tenant_id = str(identity.get("tenant_id", "public"))
    r2_cfg = R2StorageConfig.from_env()
    if not r2_cfg:
        raise HTTPException(status_code=503, detail="未配置 R2")

    task = upload_ingestion_service.get_task(task_id, tenant_id=tenant_id)
    if str(task.get("status")) != "queued":
        raise HTTPException(status_code=400, detail="任务状态不允许完成直传（需为 queued）")

    object_key = (body.object_key or "").strip().lstrip("/")
    if not object_key:
        raise HTTPException(status_code=400, detail="object_key 无效")

    r2_prefix = (os.getenv("R2_STORAGE_PREFIX") or "").strip().strip("/")
    norm_key = object_key.replace("\\", "/")
    if r2_prefix:
        need = f"{r2_prefix}/{tenant_id}/".replace("//", "/")
        if not norm_key.startswith(need):
            raise HTTPException(status_code=403, detail="object_key 与当前租户/前缀不匹配")
    elif not norm_key.startswith(f"{tenant_id}/"):
        raise HTTPException(status_code=403, detail="object_key 须以 tenant_id/ 开头")

    try:
        meta = r2_head_object(r2_cfg, key=object_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法访问对象（请确认已 PUT 成功）: {exc}") from exc

    sz = int(meta.get("content_length") or 0)
    if sz <= 0:
        raise HTTPException(status_code=400, detail="对象大小为 0，请重新上传")

    uri = r2_uri(r2_cfg.bucket, object_key)
    upload_ingestion_service.update_task_file_path(task_id, uri)
    upload_ingestion_service.update_task_file_size_bytes(task_id, sz)

    fp = str(task.get("file_path") or "")
    if fp and not str(fp).startswith("r2://"):
        try:
            Path(fp).unlink(missing_ok=True)
        except Exception:
            pass

    use_gpu = bool(body.use_gpu_ocr)
    if sz > _EXTERNAL_OCR_SIZE_THRESHOLD_BYTES:
        use_gpu = True
    task_fn = str(task.get("filename") or "")
    is_pdf = Path(task_fn).suffix.lower() == ".pdf"
    spawn_worker = True
    if use_gpu and _external_ocr_billing_enabled() and is_pdf:
        parsed_uri = parse_r2_uri(uri)
        if parsed_uri:
            bucket, key = parsed_uri
            cache_dir = UPLOAD_DIR / "_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            dest = cache_dir / f"presign_gpu_{task_id}_{Path(key).name}"
            try:
                r2_download_to_file(r2_cfg, bucket=bucket, key=key, dest_path=dest)
                task_use, ocr_resp = _external_ocr_scan_quota_result(
                    final_path=dest,
                    target_filename=task_fn,
                    actual_size=sz,
                    use_gpu_ocr_req=True,
                    external_ocr_confirmed=bool(body.external_ocr_confirmed),
                    request=request,
                    tenant_id=tenant_id,
                    upload_id=None,
                )
                if ocr_resp is not None:
                    upload_ingestion_service.update_task_use_gpu_ocr(task_id, False)
                    spawn_worker = False
                    return ocr_resp
                upload_ingestion_service.update_task_use_gpu_ocr(task_id, bool(task_use))
            finally:
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            upload_ingestion_service.update_task_use_gpu_ocr(task_id, False)
    else:
        upload_ingestion_service.update_task_use_gpu_ocr(task_id, False)

    if spawn_worker:
        _spawn_ingestion_worker(task_id, tenant_id)
    return {"ok": True, "task_id": task_id, "file_size_bytes": sz, "file_uri": uri}


@app.get("/upload/tasks", tags=["upload"])
async def list_upload_tasks(request: Request, limit: int = 50) -> Dict[str, Any]:
    """列出最近上传任务（含 task_id），便于在 /docs 或浏览器中查看 ID 再调 GET /upload/tasks/{{id}}。"""
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.upload.read")
    tenant_id = str(identity.get("tenant_id", "public"))
    rows = upload_ingestion_service.list_tasks(limit=min(max(limit, 1), 200), tenant_id=tenant_id)
    return {"tasks": [_normalize_task(t) for t in rows]}


@app.get("/upload/tasks/{task_id}")
async def get_upload_task(task_id: int, request: Request) -> Dict[str, Any]:
    try:
        identity = _get_request_identity(request)
        _require_permission(identity, "tenant.upload.read")
        task = upload_ingestion_service.get_task(task_id, tenant_id=str(identity.get("tenant_id", "public")))
    except ValueError:
        raise HTTPException(status_code=404, detail="上传任务不存在")
    return _normalize_task(task)


@app.get("/upload/metrics")
async def get_upload_metrics(request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.metrics.read")
    return upload_ingestion_service.get_rollup_metrics()


@app.get("/upload/queue/metrics")
async def get_upload_queue_metrics_endpoint(request: Request) -> Dict[str, Any]:
    """解析队列深度、按状态计数、限流阈值与进程内 worker 数（多进程部署请配置 REDIS_URL 共享限流）。"""
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.metrics.read")
    rollup = upload_ingestion_service.get_rollup_metrics()
    avg_sec: Optional[float] = None
    try:
        n = int(rollup.get("rollup_task_count") or 0)
        s = float(rollup.get("sum_extract_sec") or 0)
        if n > 0 and s > 0:
            avg_sec = s / float(n)
    except (TypeError, ValueError, ZeroDivisionError):
        avg_sec = None
    conn = _conn()
    try:
        base = get_upload_queue_metrics(
            conn,
            ingest_workers_in_memory=len(_ingestion_workers),
            estimated_avg_task_sec=avg_sec,
        )
    finally:
        conn.close()
    base["rollup_extract"] = {
        "rollup_task_count": rollup.get("rollup_task_count"),
        "sum_extract_sec": rollup.get("sum_extract_sec"),
        "estimated_avg_extract_sec_per_task": avg_sec,
    }
    return base


@app.get("/documents")
async def list_documents(request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.documents.read")
    tenant_id = str(identity.get("tenant_id", "public"))
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, filename, title, discipline, document_type, metadata, created_at FROM documents WHERE tenant_id = ? ORDER BY id DESC",
            (tenant_id,),
        ).fetchall()
        docs = []
        for row in rows:
            meta = FreeAIRouter.safe_json_loads(row["metadata"], {})
            docs.append(
                {
                    "id": row["id"],
                    "filename": row["filename"],
                    "title": row["title"],
                    "discipline": row["discipline"],
                    "document_type": row["document_type"],
                    "metadata": meta,
                    "has_summary": bool(upload_ingestion_service.get_summary_by_document_id(int(row["id"]), tenant_id=tenant_id)),
                    "created_at": row["created_at"],
                }
            )
        return {"documents": docs}
    finally:
        conn.close()


@app.get("/documents/{doc_id}/summary")
async def get_document_summary(doc_id: int, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.documents.read")
    tenant_id = str(identity.get("tenant_id", "public"))
    conn = _conn()
    try:
        row = conn.execute("SELECT id FROM documents WHERE id = ? AND tenant_id = ?", (doc_id, tenant_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="文档不存在")
    finally:
        conn.close()
    summary = upload_ingestion_service.get_summary_by_document_id(doc_id, tenant_id=tenant_id)
    if not summary:
        return {"document_id": doc_id, "summary": None}
    return {"document_id": doc_id, "summary": summary}


def _try_remove_upload_file(path_str: str) -> None:
    """仅删除位于 UPLOAD_DIR 下的文件，防止误删。"""
    try:
        p = Path(path_str).resolve()
        base = UPLOAD_DIR.resolve()
        p.relative_to(base)
    except (ValueError, OSError):
        return
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        pass


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: int, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.documents.delete")
    tenant_id = str(identity.get("tenant_id", "public"))
    conn = _conn()
    upload_paths: List[str] = []
    try:
        row = conn.execute("SELECT filename FROM documents WHERE id = ? AND tenant_id = ?", (doc_id, tenant_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="文档不存在")
        task_rows = conn.execute(
            "SELECT file_path FROM upload_tasks WHERE document_id = ? AND tenant_id = ?",
            (doc_id, tenant_id),
        ).fetchall()
        upload_paths = [str(r["file_path"]) for r in task_rows if r["file_path"]]
        conn.execute(
            "DELETE FROM ocr_page_cache WHERE task_id IN (SELECT id FROM upload_tasks WHERE document_id = ? AND tenant_id = ?)",
            (doc_id, tenant_id),
        )
        conn.execute(
            "DELETE FROM vector_ingest_checkpoints WHERE task_id IN (SELECT id FROM upload_tasks WHERE document_id = ? AND tenant_id = ?)",
            (doc_id, tenant_id),
        )
        conn.execute("DELETE FROM document_summaries WHERE document_id = ? AND tenant_id = ?", (doc_id, tenant_id))
        conn.execute("DELETE FROM upload_tasks WHERE document_id = ? AND tenant_id = ?", (doc_id, tenant_id))
        conn.execute("DELETE FROM vectors WHERE document_id = ? AND tenant_id = ?", (doc_id, tenant_id))
        conn.execute("DELETE FROM documents WHERE id = ? AND tenant_id = ?", (doc_id, tenant_id))
        conn.commit()
    finally:
        conn.close()
    if RUNTIME_CONFIG.postgres.enabled:
        try:
            deep_pipeline_service.delete_document_data(
                doc_id,
                tenant_id=tenant_id,
                user_id=str(identity.get("user_id", "anonymous")),
                roles=list(identity.get("roles", [])),
            )
        except Exception:
            logger.exception("PostgreSQL 流水线数据清理失败 document_id=%s", doc_id)
    for fp in upload_paths:
        _try_remove_upload_file(fp)
    await _rebuild_kg_relations(tenant_id=tenant_id)
    _audit_log(
        request,
        identity,
        action="documents.delete",
        resource_type="document",
        resource_id=str(doc_id),
        result="success",
        details={"document_id": doc_id},
    )
    return {"deleted": doc_id}


@app.get("/knowledge-graph")
async def knowledge_graph(request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.knowledge.read")
    tenant_id = str(identity.get("tenant_id", "public"))
    docs = _load_documents_with_meta(tenant_id=tenant_id)
    chunks = _load_chunks_by_doc(tenant_id=tenant_id)
    graph = kg_builder.build_graph(docs, chunks)
    graph["insights"] = _build_graph_insights(graph)
    return graph


@app.post("/chat")
async def chat(req: ChatRequest, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.chat.write")
    tenant_id = str(identity.get("tenant_id", "public"))
    user_id = str(identity.get("user_id", "anonymous"))
    session_id = _normalize_tenant_id(req.session_id or "") or "default"
    memory_rows = _load_chat_memory_context(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    memory_context_lines: List[str] = []
    for item in memory_rows:
        q = str(item.get("question", "")).strip()
        a = str(item.get("answer", "")).strip()
        if q and a:
            memory_context_lines.append(f"Q: {q}\nA: {a}")
    enriched_query = req.query
    if memory_context_lines:
        enriched_query = f"{req.query}\n\n[历史工作记忆]\n" + "\n\n".join(memory_context_lines[-_chat_memory_recent_limit():])
    graph_result = await agent_chains.run_chat_graph(
        query=enriched_query,
        discipline=req.discipline,
        mode=req.mode,
        tenant_id=tenant_id,
    )
    answer = str(graph_result.get("answer", "")).strip()
    brief_reasoning = graph_result.get("brief_reasoning", [])
    sources = graph_result.get("evidence", [])
    regression_gates = graph_result.get("qa_regression_gates", _build_reasoning_gates(answer, brief_reasoning, sources))
    _append_chat_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        question=req.query,
        answer=answer,
        sources=sources if isinstance(sources, list) else [],
    )
    return {
        "answer": answer,
        "brief_reasoning": brief_reasoning,
        "five_dimensions": graph_result.get("five_dimensions", {}),
        "five_dimensions_meta": graph_result.get("five_dimensions_meta", {}),
        "evidence": sources,
        "provider": graph_result.get("provider", "unknown"),
        "sources": sources,
        "qa_regression_gates": regression_gates,
        "quality_gates": graph_result.get("quality_gates", regression_gates),
        "fallback_reason": graph_result.get("fallback_reason", "none"),
        "cross_discipline": graph_result.get("cross_discipline", []),
        "agent_trace": graph_result.get("agent_trace", []),
        "cost_profile": graph_result.get("cost_profile", {}),
        "session_id": session_id,
    }


@app.delete("/chat/memory")
async def clear_chat_memory(req: ChatMemoryClearRequest, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.chat.clear")
    tenant_id = str(identity.get("tenant_id", "public"))
    user_id = str(identity.get("user_id", "anonymous"))
    session_id = _normalize_tenant_id(req.session_id)
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 无效")
    if RUNTIME_CONFIG.postgres.enabled:
        conn = None
        try:
            conn = pg_store.connect(RUNTIME_CONFIG.postgres.database_url)
            pg_store.set_request_context(conn, tenant_id=tenant_id, user_id=user_id, roles=list(identity.get("roles", [])))
            cleared = pg_store.clear_chat_turns(conn, tenant_id=tenant_id, session_id=session_id, user_id=user_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"清空聊天工作记忆失败: {exc}") from exc
        finally:
            if conn:
                conn.close()
    else:
        # SQLite 降级：删除该 session 的所有记录
        try:
            sq = sqlite3.connect(DB_PATH, timeout=RUNTIME_CONFIG.sqlite.busy_timeout_ms / 1000.0)
            try:
                cur = sq.execute(
                    "DELETE FROM chat_sessions WHERE tenant_id=? AND session_id=?",
                    (tenant_id, session_id),
                )
                cleared = cur.rowcount
                sq.commit()
            finally:
                sq.close()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"清空聊天工作记忆失败: {exc}") from exc
    _audit_log(
        request,
        identity,
        action="chat.memory.clear",
        resource_type="chat_session",
        resource_id=session_id,
        result="success",
        details={"cleared": cleared},
    )
    return {"cleared": cleared, "session_id": session_id}


@app.post("/insights/summary")
async def insights_summary(req: SummaryRequest, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.insights.read")
    tenant_id = str(identity.get("tenant_id", "public"))
    summary_debug_passthrough = _env_bool("SUMMARY_DEBUG_PASSTHROUGH", False)
    summary_compact_level = 0
    summary_mode = "full"
    graph_result = await agent_chains.run_summary_graph(
        query=req.query,
        discipline=req.discipline,
        document_id=req.document_id,
        tenant_id=tenant_id,
        summary_debug_passthrough=summary_debug_passthrough,
        summary_compact_level=summary_compact_level,
        summary_mode=summary_mode,
    )
    debug_payload: Dict[str, Any] = {}
    if summary_debug_passthrough:
        debug_payload = {
            "raw_model_content": graph_result.get("raw_model_content"),
            "parsed_before_clip": graph_result.get("parsed_before_clip"),
        }
    if not graph_result.get("retrieved"):
        fallback_payload = _fallback_summary(req.query, [], [])
        return {
            **fallback_payload,
            "five_dimensions": graph_result.get("five_dimensions", {}),
            "five_dimensions_meta": graph_result.get("five_dimensions_meta", {}),
            "provider": graph_result.get("provider", "unknown"),
            "fallback": True,
            "fallback_reason": graph_result.get("fallback_reason", "no_results"),
            "summary_compact_level": summary_compact_level,
            "summary_mode": summary_mode,
            "raw_lengths": graph_result.get("raw_lengths", {}),
            "clipped_lengths": graph_result.get("clipped_lengths", {}),
            "effective_coverage": graph_result.get("effective_coverage", {}),
            "coverage_stats": graph_result.get("coverage_stats", {}),
            "document_id": req.document_id,
            "agent_trace": graph_result.get("agent_trace", []),
            "cost_profile": graph_result.get("cost_profile", {}),
            **debug_payload,
        }
    summary = graph_result.get("summary", {})
    normalized = {
        "brief": summary.get("brief", []),
        "highlights": summary.get("highlights", []),
        "conclusions": summary.get("conclusions", []),
        "actions": summary.get("actions", []),
        "citations": summary.get("citations", []),
    }
    if not normalized.get("highlights"):
        fallback_payload = _fallback_summary(req.query, graph_result.get("retrieved", []), _build_sources(graph_result.get("retrieved", []), limit=6))
        return {
            **fallback_payload,
            "five_dimensions": graph_result.get("five_dimensions", {}),
            "five_dimensions_meta": graph_result.get("five_dimensions_meta", {}),
            "provider": graph_result.get("provider", "unknown"),
            "fallback": True,
            "fallback_reason": graph_result.get("fallback_reason", "no_results"),
            "summary_compact_level": summary_compact_level,
            "summary_mode": summary_mode,
            "raw_lengths": graph_result.get("raw_lengths", {}),
            "clipped_lengths": graph_result.get("clipped_lengths", {}),
            "effective_coverage": graph_result.get("effective_coverage", {}),
            "coverage_stats": graph_result.get("coverage_stats", {}),
            "document_id": req.document_id,
            "agent_trace": graph_result.get("agent_trace", []),
            "cost_profile": graph_result.get("cost_profile", {}),
            **debug_payload,
        }
    return {
        **normalized,
        "five_dimensions": graph_result.get("five_dimensions", {}),
        "five_dimensions_meta": graph_result.get("five_dimensions_meta", {}),
        "provider": graph_result.get("provider", "unknown"),
        "fallback": False,
        "quality_gates": graph_result.get("quality_gates", {}),
        "fallback_reason": graph_result.get("fallback_reason", "none"),
        "summary_compact_level": summary_compact_level,
        "summary_mode": summary_mode,
        "raw_lengths": graph_result.get("raw_lengths", {}),
        "clipped_lengths": graph_result.get("clipped_lengths", {}),
        "effective_coverage": graph_result.get("effective_coverage", {}),
        "coverage_stats": graph_result.get("coverage_stats", {}),
        "document_id": req.document_id,
        "agent_trace": graph_result.get("agent_trace", []),
        "cost_profile": graph_result.get("cost_profile", {}),
        **debug_payload,
    }


@app.post("/insights/report")
async def insights_report(req: ReportRequest, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.insights.read")
    tenant_id = str(identity.get("tenant_id", "public"))
    default_summary_compact_level = _resolve_summary_compact_level(os.getenv("SUMMARY_COMPACT_LEVEL"), default=1)
    summary_compact_level = _resolve_summary_compact_level(req.summary_compact_level, default=default_summary_compact_level)
    report_mode = _resolve_summary_mode(req.report_mode, default="full")
    graph_result = await agent_chains.run_report_graph(
        query=req.query,
        discipline=req.discipline,
        document_id=req.document_id,
        tenant_id=tenant_id,
        report_mode=report_mode,
        summary_compact_level=summary_compact_level,
    )
    report_text = str(graph_result.get("report", "")).strip()
    sections = graph_result.get("report_sections", []) if isinstance(graph_result.get("report_sections"), list) else []
    citations = graph_result.get("citations", []) if isinstance(graph_result.get("citations"), list) else []
    coverage_stats = graph_result.get("coverage_stats", {}) if isinstance(graph_result.get("coverage_stats"), dict) else {}
    validation_graph_skipped = bool(
        graph_result.get("validation_graph_skipped", coverage_stats.get("validation_graph_skipped", False))
    )
    if not citations:
        citations = _build_sources(graph_result.get("retrieved", []), limit=8)
    if not citations:
        citations = [{"title": "基于当前检索未命中", "discipline": "all", "section_path": "N/A"}]
    short_report_guard_hit = _is_short_report_for_mode(report_text, report_mode, summary_compact_level)
    if _is_invalid_report_text(report_text) or short_report_guard_hit:
        fallback_payload = _fallback_summary(req.query, graph_result.get("retrieved", []), citations)
        report_text, sections = _build_report_markdown_from_fallback(
            fallback_payload=fallback_payload,
            existing_sections=sections,
            short_report=short_report_guard_hit,
        )
        fallback_reason = str(graph_result.get("fallback_reason", "none")).strip() or "none"
        if fallback_reason in {"none", "report_ok"}:
            fallback_reason = "report_endpoint_short_guard" if short_report_guard_hit else "report_endpoint_fallback"
        if (not validation_graph_skipped) and _is_report_under_coverage(coverage_stats):
            fallback_reason = "parse_under_coverage"
    else:
        fallback_reason = str(graph_result.get("fallback_reason", "none")).strip() or "none"
    if not sections:
        fallback_payload = _fallback_summary(req.query, graph_result.get("retrieved", []), citations)
        report_text, sections = _build_report_markdown_from_fallback(
            fallback_payload=fallback_payload,
            existing_sections=[],
            short_report=False,
        )
    return {
        "report": report_text,
        "sections": sections,
        "five_dimensions": graph_result.get("five_dimensions", {}),
        "five_dimensions_meta": graph_result.get("five_dimensions_meta", {}),
        "citations": citations,
        "provider": graph_result.get("provider", "unknown"),
        "report_mode": report_mode,
        "summary_compact_level": summary_compact_level,
        "coverage_stats": coverage_stats,
        "validation_graph_skipped": validation_graph_skipped,
        "quality_gates": graph_result.get("quality_gates", {}),
        "fallback_reason": fallback_reason,
        "document_id": req.document_id,
        "agent_trace": graph_result.get("agent_trace", []),
        "cost_profile": graph_result.get("cost_profile", {}),
    }


def _spawn_deep_pipeline(job_id: str, tenant_id: str, user_id: str, roles: List[str]) -> None:
    existing = _deep_pipeline_tasks.get(job_id)
    if existing and not existing.done():
        return

    async def _runner() -> None:
        try:
            await agent_chains.run_deep_pipeline_job(
                deep_pipeline_service,
                job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                roles=roles,
            )
        except Exception:
            logger.exception("deep pipeline background task failed job_id=%s", job_id)

    _deep_pipeline_tasks[job_id] = asyncio.create_task(_runner())


@app.post("/pipeline/deep-report/start", tags=["pipeline"])
async def pipeline_deep_report_start(req: DeepReportStartRequest, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.pipeline.write")
    tenant_id = str(identity.get("tenant_id", "public"))
    if not RUNTIME_CONFIG.postgres.enabled:
        raise HTTPException(status_code=503, detail="PostgreSQL 未配置：请设置环境变量 DATABASE_URL")
    try:
        job_id = deep_pipeline_service.create_job(
            sqlite_document_id=req.document_id,
            discipline=req.discipline,
            config=req.config,
            tenant_id=tenant_id,
            user_id=str(identity.get("user_id", "anonymous")),
            roles=list(identity.get("roles", [])),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _spawn_deep_pipeline(
        job_id,
        tenant_id=tenant_id,
        user_id=str(identity.get("user_id", "anonymous")),
        roles=list(identity.get("roles", [])),
    )
    return {"job_id": job_id, "status": "queued"}


@app.get("/pipeline/deep-report/{job_id}", tags=["pipeline"])
async def pipeline_deep_report_status(job_id: str, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.pipeline.read")
    tenant_id = str(identity.get("tenant_id", "public"))
    if not RUNTIME_CONFIG.postgres.enabled:
        raise HTTPException(status_code=503, detail="PostgreSQL 未配置：请设置环境变量 DATABASE_URL")
    row = deep_pipeline_service.get_job(
        job_id,
        tenant_id=tenant_id,
        user_id=str(identity.get("user_id", "anonymous")),
        roles=list(identity.get("roles", [])),
    )
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    out = dict(row)
    for key in ("created_at", "updated_at"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    if isinstance(out.get("config"), (dict, list)):
        pass
    elif isinstance(out.get("config"), str):
        try:
            out["config"] = json.loads(out["config"])
        except Exception:
            pass
    return out


@app.get("/presentation-tree/{doc_id}", tags=["pipeline"])
async def get_presentation_tree(doc_id: int, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.pipeline.read")
    tenant_id = str(identity.get("tenant_id", "public"))
    if not RUNTIME_CONFIG.postgres.enabled:
        raise HTTPException(status_code=503, detail="PostgreSQL 未配置：请设置环境变量 DATABASE_URL")
    bundle = deep_pipeline_service.get_presentation_bundle(
        doc_id,
        tenant_id=tenant_id,
        user_id=str(identity.get("user_id", "anonymous")),
        roles=list(identity.get("roles", [])),
    )
    if not bundle:
        raise HTTPException(status_code=404, detail="未找到已完成的展示树")
    tree = dict(bundle["tree"])
    if tree.get("created_at") is not None:
        tree["created_at"] = str(tree["created_at"])
    nodes_out: List[Dict[str, Any]] = []
    for n in bundle["nodes"]:
        item = dict(n)
        if item.get("created_at") is not None:
            item["created_at"] = str(item["created_at"])
        for json_key in ("payload", "source_span_refs"):
            if isinstance(item.get(json_key), str):
                try:
                    item[json_key] = json.loads(item[json_key])
                except Exception:
                    pass
        nodes_out.append(item)
    return {"document_id": doc_id, "tree": tree, "nodes": nodes_out}


@app.post("/exam")
async def exam(req: ExamRequest, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.exam.write")
    tenant_id = str(identity.get("tenant_id", "public"))
    return await exam_processor.analyze_exam(req.exam_text, req.discipline, tenant_id=tenant_id)


@app.post("/exam/upload")
async def exam_upload(
    request: Request,
    file: UploadFile = File(...),
    discipline: str = "all",
    document_type: Optional[str] = None,
) -> Dict[str, Any]:
    _ensure_capacity_for_write()
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.exam.write")
    tenant_id = str(identity.get("tenant_id", "public"))
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md", ".markdown"}:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {file.filename}")

    storage = _unique_upload_basename(file.filename)
    target = UPLOAD_DIR / storage
    await _save_upload_file_stream(file, target)

    dtype = document_type or "exam"
    parsed = parser.parse(str(target), dtype)
    analysis = await exam_processor.analyze_and_answer_exam(parsed.text, discipline, tenant_id=tenant_id)
    return {
        "filename": file.filename,
        "tenant_id": tenant_id,
        "discipline": discipline,
        "document_type": dtype,
        "analysis": analysis,
    }


@app.post("/generate")
async def generate(req: GenerateRequest, request: Request) -> Dict[str, Any]:
    identity = _get_request_identity(request)
    _require_permission(identity, "tenant.generate.write")
    prompt = (
        f"请基于学科 {req.discipline} 生成可执行内容。要求：简明、分点、可落地。\n"
        f"输入: {req.prompt}"
    )
    resp = await ai_router.chat([{"role": "user", "content": prompt}])
    return {"content": resp["content"], "provider": resp["provider"]}


async def _rebuild_kg_relations(tenant_id: str) -> None:
    docs = _load_documents_with_meta(tenant_id=tenant_id)
    chunks = _load_chunks_by_doc(tenant_id=tenant_id)
    graph = kg_builder.build_graph(docs, chunks)
    cross_relations = kg_builder.extract_cross_relations(graph)
    conn = _conn()
    try:
        conn.execute("DELETE FROM kg_relations WHERE tenant_id = ?", (tenant_id,))
        for source_id, target_id, explanation in cross_relations:
            conn.execute(
                """
                INSERT INTO kg_relations (tenant_id, source_id, target_id, relation_type, explanation)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tenant_id, source_id, target_id, "cross_discipline", explanation),
            )
        conn.commit()
    finally:
        conn.close()


def _load_documents_with_meta(tenant_id: str) -> List[Dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, title, discipline, document_type, metadata FROM documents WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchall()
        docs = []
        for row in rows:
            meta = FreeAIRouter.safe_json_loads(row["metadata"], {})
            docs.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "discipline": row["discipline"],
                    "document_type": row["document_type"],
                    "knowledge_points": meta.get("knowledge_points", []),
                }
            )
        return docs
    finally:
        conn.close()


def _load_chunks_by_doc(tenant_id: str) -> Dict[int, List[Dict[str, Any]]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT document_id, chunk_id, section_path, content FROM vectors WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchall()
        out: Dict[int, List[Dict[str, Any]]] = {}
        for row in rows:
            out.setdefault(row["document_id"], []).append(dict(row))
        return out
    finally:
        conn.close()


def _build_graph_insights(graph: Dict[str, Any], max_items: int = 40, max_explanation_len: int = 160) -> List[Dict[str, str]]:
    insights: List[Dict[str, str]] = []
    seen = set()
    for link in graph.get("links", []):
        source = str(link.get("source", "")).strip()
        target = str(link.get("target", "")).strip()
        relation_type = str(link.get("type", "")).strip()
        if not source or not target or not relation_type:
            continue
        key = (source, target, relation_type)
        if key in seen:
            continue
        seen.add(key)
        insights.append(
            {
                "source": source,
                "target": target,
                "relation_type": relation_type,
                "explanation": _clip_text(str(link.get("explanation", "")), max_explanation_len),
            }
        )
        if len(insights) >= max_items:
            break
    return insights


def _clip_text(value: str, max_len: int) -> str:
    text = (value or "").strip()
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 1]}…"


def _build_sources(rows: List[Dict[str, Any]], limit: int = 6) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for row in rows:
        item = {
            "title": str(row.get("title", "")),
            "discipline": str(row.get("discipline", "")),
            "section_path": str(row.get("section_path", "")),
            "document_type": str(row.get("document_type", "")),
        }
        key = (item["title"], item["discipline"], item["section_path"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _is_invalid_report_text(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    placeholder_flags = [
        "当前未生成稳定报告内容",
        "建议补充更明确的问题边界后重试",
        "未生成稳定报告",
        "report unavailable",
        "no stable report",
    ]
    if any(flag in text or flag in lowered for flag in placeholder_flags):
        return True
    effective_chars = re.sub(r"\s+", "", text)
    if len(effective_chars) < 80:
        return True
    return False


def _is_short_report_for_mode(text: str, report_mode: str, compact_level: int) -> bool:
    if str(report_mode or "full").strip().lower() != "full":
        return False
    effective_chars = len(re.sub(r"\s+", "", (text or "").strip()))
    if compact_level == 0:
        return effective_chars < 700
    return effective_chars < 320


def _is_report_under_coverage(coverage_stats: Dict[str, Any]) -> bool:
    if bool(coverage_stats.get("validation_graph_skipped", False)):
        return False
    raw_total = int(coverage_stats.get("raw_total_chunks", 0) or 0)
    processed_rows = int(coverage_stats.get("processed_rows", 0) or 0)
    after_doc_limit = int(coverage_stats.get("after_doc_limit", processed_rows) or processed_rows)
    after_candidate_limit = int(coverage_stats.get("after_candidate_limit", processed_rows) or processed_rows)
    require_complete = bool(coverage_stats.get("full_require_complete", False))
    if require_complete and raw_total > 0 and processed_rows < raw_total:
        return True
    if raw_total < 120:
        return False
    if after_doc_limit <= 0:
        return True
    return (after_candidate_limit / max(raw_total, 1)) < 0.08


def _build_report_markdown_from_fallback(
    fallback_payload: Dict[str, Any],
    existing_sections: List[Dict[str, Any]],
    short_report: bool,
) -> tuple[str, List[Dict[str, str]]]:
    sections: List[Dict[str, str]] = []
    if isinstance(existing_sections, list):
        for item in existing_sections[:8]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()
            if title and content:
                sections.append({"title": title, "content": content})
    if not sections:
        sections = [
            {"title": "要点", "content": "；".join(fallback_payload.get("highlights", []))},
            {"title": "结论", "content": "；".join(fallback_payload.get("conclusions", []))},
            {"title": "行动建议", "content": "；".join(fallback_payload.get("actions", []))},
        ]
    lines: List[str] = []
    for sec in sections:
        title = sec["title"]
        bullet_items = [x.strip() for x in sec["content"].split("；") if x.strip()]
        if not bullet_items:
            continue
        lines.append(f"## {title}")
        lines.extend([f"- {item}" for item in bullet_items])
        lines.append("")
    if short_report:
        lines.extend(
            [
                "## 覆盖说明",
                "- 当前输出命中短报告守卫，已按可执行结构重组内容。",
                "- 建议结合引用来源复核中后段章节，必要时缩小问题范围重试。",
                "",
            ]
        )
    report_text = "\n".join(lines).strip()
    return report_text, sections


def _parse_reasoning_contract(raw_text: str) -> Dict[str, Any]:
    raw = str(raw_text or "").strip()
    if not raw:
        return {"answer": "", "brief_reasoning": []}
    parsed = FreeAIRouter.safe_json_loads(raw, None)
    if isinstance(parsed, dict):
        return parsed
    cleaned = raw
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2 and lines[0].strip().startswith("```"):
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
            parsed = FreeAIRouter.safe_json_loads(cleaned, None)
            if isinstance(parsed, dict):
                return parsed
    return {"answer": raw, "brief_reasoning": []}


def _sanitize_answer_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(
        flag in lowered
        for flag in ["chain of thought", "let's think step by step", "逐步推理", "完整推理", "内部思考"]
    ):
        return "已根据检索证据生成结论，为避免泄露完整推理链，仅展示答案与简版思路。"
    return _clip_text(text, 800)


def _sanitize_brief_reasoning(value: Any) -> List[str]:
    items: List[str]
    if isinstance(value, list):
        items = [str(x).strip() for x in value]
    elif isinstance(value, str):
        items = [x.strip() for x in re.split(r"[\n;；]+", value)]
    else:
        items = []
    out: List[str] = []
    for item in items:
        if not item:
            continue
        lowered = item.lower()
        if any(flag in lowered for flag in ["chain of thought", "let's think step by step", "逐步推理", "完整推理"]):
            continue
        out.append(_clip_text(item, 120))
        if len(out) >= 3:
            break
    if not out:
        out = ["已基于检索证据完成信息压缩并给出可验证结论。"]
    return out


def _build_reasoning_gates(answer: str, brief_reasoning: List[str], evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    consistency = bool((answer or "").strip())
    evidence_traceable = bool(evidence) and all(
        str(item.get("title", "")).strip() and str(item.get("section_path", "")).strip() for item in evidence
    )
    reasoning_visibility = 1 <= len(brief_reasoning) <= 3 and all(
        "逐步推理" not in x and "完整推理" not in x and "chain of thought" not in x.lower() for x in brief_reasoning
    )
    failed_checks: List[str] = []
    if not consistency:
        failed_checks.append("consistency")
    if not evidence_traceable:
        failed_checks.append("evidence_traceable")
    if not reasoning_visibility:
        failed_checks.append("reasoning_visibility")
    return {
        "consistency": consistency,
        "evidence_traceable": evidence_traceable,
        "reasoning_visibility": reasoning_visibility,
        "passed": len(failed_checks) == 0,
        "failed_checks": failed_checks,
    }


def _fallback_summary(query: str, rows: List[Dict[str, Any]], sources: List[Dict[str, str]]) -> Dict[str, Any]:
    highlights: List[str] = []
    for row in rows[:3]:
        title = str(row.get("title", "未命名资料")).strip() or "未命名资料"
        snippet = _clip_text(str(row.get("content", "")).replace("\n", " ").strip(), 90)
        if snippet:
            highlights.append(f"{title}：{snippet}")
    if not highlights:
        highlights = ["基于当前检索未命中，请先上传相关资料或调整问题表述。"]

    conclusions = [f"围绕“{query}”已完成检索与归纳，可优先关注高相关来源。"]
    actions = ["建议补充更具体的约束条件（场景、对象、边界）以获得更强可执行建议。"]
    citations = sources if sources else [{"title": "基于当前检索未命中", "discipline": "all", "section_path": "N/A"}]
    return {
        "highlights": highlights,
        "conclusions": conclusions,
        "actions": actions,
        "citations": citations,
    }


def _extract_summary_json(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    parsed = FreeAIRouter.safe_json_loads(raw, None)
    if isinstance(parsed, dict):
        return parsed

    # Handle fenced code blocks: ```json ... ``` or ``` ... ```
    cleaned = raw
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            # Drop the opening/closing fences if present.
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
            parsed = FreeAIRouter.safe_json_loads(cleaned, None)
            if isinstance(parsed, dict):
                return parsed

    def _extract_first_json_object(payload: str) -> Optional[str]:
        start = payload.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(payload)):
            ch = payload[idx]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return payload[start : idx + 1]
        return None

    candidate = _extract_first_json_object(cleaned)
    if candidate:
        parsed = FreeAIRouter.safe_json_loads(candidate, None)
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_summary(data: Dict[str, Any], sources: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    def _as_str_list(value: Any) -> List[str]:
        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            items = [value]
        else:
            items = []
        return [str(x).strip() for x in items if str(x).strip()]

    highlights = _as_str_list(data.get("highlights"))
    conclusions = _as_str_list(data.get("conclusions"))
    actions = _as_str_list(data.get("actions"))
    brief = _as_str_list(data.get("brief"))
    if not brief:
        brief = (highlights[:1] + conclusions[:1] + actions[:1])[:3]
    if len(brief) < 3:
        for text in highlights + conclusions + actions:
            if text in brief:
                continue
            brief.append(text)
            if len(brief) >= 3:
                break
    if len(brief) < 3:
        defaults = [
            "已完成本轮资料提炼，可先阅读要点与结论。",
            "当前内容包含可执行建议，建议结合业务场景筛选。",
            "引用来源已附上，便于快速回溯原文。",
        ]
        for text in defaults:
            brief.append(text)
            if len(brief) >= 3:
                break

    if not highlights:
        highlights = ["已完成核心信息梳理，建议结合引用来源查看原文细节。"]
    if not conclusions:
        conclusions = ["已形成初步结论，请结合业务目标进一步确认优先级。"]
    if not actions:
        actions = ["建议补充场景、对象与边界条件，以生成更具体的执行方案。"]

    citations: List[Dict[str, str]] = []
    raw_citations = data.get("citations", [])
    if isinstance(raw_citations, dict):
        raw_citations = [raw_citations]
    if not isinstance(raw_citations, list):
        raw_citations = []
    for item in raw_citations:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        discipline = str(item.get("discipline", "")).strip()
        section_path = str(item.get("section_path", "")).strip()
        if not title and not section_path:
            continue
        citations.append(
            {
                "title": title or "未命名来源",
                "discipline": discipline or "all",
                "section_path": section_path or "N/A",
            }
        )
    if not citations:
        citations = [
            {
                "title": s.get("title", "未命名来源"),
                "discipline": s.get("discipline", "all"),
                "section_path": s.get("section_path", "N/A"),
            }
            for s in sources[:5]
        ]
    if not citations:
        citations = [{"title": "基于当前检索未命中", "discipline": "all", "section_path": "N/A"}]

    return {
        "brief": brief[:3],
        "highlights": highlights[:6],
        "conclusions": conclusions[:4],
        "actions": actions[:4],
        "citations": citations[:6],
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
