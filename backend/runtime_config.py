import os
from dataclasses import dataclass


def _as_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int(value: str, default: int, min_value: int) -> int:
    try:
        return max(min_value, int(str(value).strip()))
    except Exception:
        return default


def _as_float(value: str, default: float, min_value: float) -> float:
    try:
        return max(min_value, float(str(value).strip()))
    except Exception:
        return default


@dataclass(frozen=True)
class HybridModelConfig:
    local_first: bool
    enable_local_chat: bool
    enable_local_embedding: bool
    enable_remote_fallback: bool
    enable_hash_fallback: bool
    local_chat_model_id: str
    local_embedding_model_id: str
    local_chat_max_new_tokens: int
    local_chat_temperature: float
    remote_timeout_seconds: float
    remote_max_retries: int
    remote_retry_backoff_seconds: float
    embedding_dimensions: int


@dataclass(frozen=True)
class LlamaIndexConfig:
    enabled: bool
    splitter_chunk_size: int
    splitter_chunk_overlap: int


@dataclass(frozen=True)
class LangChainConfig:
    enabled: bool
    strict_json_output: bool
    max_context_blocks: int


@dataclass(frozen=True)
class SqliteConfig:
    wal_enabled: bool
    busy_timeout_ms: int


@dataclass(frozen=True)
class IngestionConfig:
    embed_batch_size: int
    max_retries: int
    base_retry_delay_seconds: float


@dataclass(frozen=True)
class PostgresConfig:
    """四库树状流水线（深度报告）可选 PostgreSQL。未配置 DATABASE_URL 时流水线 API 返回 503。"""
    database_url: str
    enabled: bool


@dataclass(frozen=True)
class PipelineConfig:
    """四库流水线默认参数（可被 POST /pipeline/deep-report/start 的 body.config 覆盖）。"""

    batch_chunk_size: int
    group_count: int
    max_chunks: int
    validation_flush_interval: int
    validation_segment_rotate: int

    def as_dict(self) -> dict:
        return {
            "batch_chunk_size": self.batch_chunk_size,
            "group_count": self.group_count,
            "max_chunks": self.max_chunks,
            "validation_flush_interval": self.validation_flush_interval,
            "validation_segment_rotate": self.validation_segment_rotate,
        }


@dataclass(frozen=True)
class TenantConfig:
    require_header: bool
    default_tenant_id: str
    header_name: str


@dataclass(frozen=True)
class CapacityConfig:
    disk_soft_limit_bytes: int
    disk_hard_limit_bytes: int
    pause_on_hard_limit: bool


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    issuer: str
    audience: str
    jwks_url: str
    leeway_seconds: int
    require_membership_check: bool
    tenant_claim_key: str
    roles_claim_key: str
    permissions_claim_key: str


@dataclass(frozen=True)
class RuntimeConfig:
    hybrid: HybridModelConfig
    llama_index: LlamaIndexConfig
    langchain: LangChainConfig
    sqlite: SqliteConfig
    ingestion: IngestionConfig
    postgres: PostgresConfig
    pipeline: PipelineConfig
    tenant: TenantConfig
    capacity: CapacityConfig
    auth: AuthConfig

    @staticmethod
    def from_env() -> "RuntimeConfig":
        _pg_url = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip()
        return RuntimeConfig(
            hybrid=HybridModelConfig(
                # 0 = 远程 API 优先（GitHub/HF/智谱），本地 transformers 作备用（默认）
                # 1 = 本地 transformers 优先；显式设置 HYBRID_LOCAL_FIRST=1 可恢复
                local_first=_as_bool(os.getenv("HYBRID_LOCAL_FIRST", "0"), False),
                enable_local_chat=_as_bool(os.getenv("HYBRID_ENABLE_LOCAL_CHAT", "1"), True),
                enable_local_embedding=_as_bool(os.getenv("HYBRID_ENABLE_LOCAL_EMBEDDING", "1"), True),
                enable_remote_fallback=_as_bool(os.getenv("HYBRID_ENABLE_REMOTE_FALLBACK", "1"), True),
                enable_hash_fallback=_as_bool(os.getenv("HYBRID_ENABLE_HASH_FALLBACK", "1"), True),
                local_chat_model_id=os.getenv("LOCAL_CHAT_MODEL_ID", "Qwen/Qwen2.5-0.5B-Instruct").strip()
                or "Qwen/Qwen2.5-0.5B-Instruct",
                local_embedding_model_id=os.getenv(
                    "LOCAL_EMBEDDING_MODEL_ID", "thenlper/gte-small-zh"
                ).strip()
                or "thenlper/gte-small-zh",
                local_chat_max_new_tokens=_as_int(os.getenv("LOCAL_CHAT_MAX_NEW_TOKENS", "512"), 512, 32),
                local_chat_temperature=_as_float(os.getenv("LOCAL_CHAT_TEMPERATURE", "0.2"), 0.2, 0.0),
                remote_timeout_seconds=_as_float(os.getenv("HYBRID_REMOTE_TIMEOUT_SECONDS", "25"), 25.0, 1.0),
                remote_max_retries=_as_int(os.getenv("HYBRID_REMOTE_MAX_RETRIES", "2"), 2, 0),
                remote_retry_backoff_seconds=_as_float(
                    os.getenv("HYBRID_REMOTE_RETRY_BACKOFF_SECONDS", "0.6"), 0.6, 0.0
                ),
                embedding_dimensions=_as_int(os.getenv("HYBRID_EMBEDDING_DIMENSIONS", "384"), 384, 8),
            ),
            llama_index=LlamaIndexConfig(
                enabled=_as_bool(os.getenv("LLAMAINDEX_ENABLED", "1"), True),
                splitter_chunk_size=_as_int(os.getenv("LLAMAINDEX_SPLITTER_CHUNK_SIZE", "480"), 480, 100),
                splitter_chunk_overlap=_as_int(os.getenv("LLAMAINDEX_SPLITTER_CHUNK_OVERLAP", "120"), 120, 0),
            ),
            langchain=LangChainConfig(
                enabled=_as_bool(os.getenv("LANGCHAIN_ENABLED", "1"), True),
                strict_json_output=_as_bool(os.getenv("LANGCHAIN_STRICT_JSON_OUTPUT", "1"), True),
                max_context_blocks=_as_int(os.getenv("LANGCHAIN_MAX_CONTEXT_BLOCKS", "6"), 6, 1),
            ),
            sqlite=SqliteConfig(
                wal_enabled=_as_bool(os.getenv("SQLITE_WAL_ENABLED", "1"), True),
                busy_timeout_ms=_as_int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "8000"), 8000, 1000),
            ),
            ingestion=IngestionConfig(
                embed_batch_size=_as_int(os.getenv("INGEST_EMBED_BATCH_SIZE", "8"), 8, 1),
                max_retries=_as_int(os.getenv("INGEST_MAX_RETRIES", "3"), 3, 1),
                base_retry_delay_seconds=_as_float(os.getenv("INGEST_BASE_RETRY_DELAY_SECONDS", "0.8"), 0.8, 0.1),
            ),
            postgres=PostgresConfig(
                database_url=_pg_url,
                enabled=bool(_pg_url),
            ),
            pipeline=PipelineConfig(
                batch_chunk_size=_as_int(os.getenv("PIPELINE_BATCH_CHUNK_SIZE", "30"), 30, 1),
                group_count=_as_int(os.getenv("PIPELINE_GROUP_COUNT", "3"), 3, 1),
                max_chunks=_as_int(os.getenv("PIPELINE_MAX_CHUNKS", "500"), 500, 1),
                validation_flush_interval=_as_int(
                    os.getenv("PIPELINE_VALIDATION_FLUSH_INTERVAL", "3"), 3, 1
                ),
                validation_segment_rotate=_as_int(
                    os.getenv("PIPELINE_VALIDATION_SEGMENT_ROTATE", "3"), 3, 1
                ),
            ),
            tenant=TenantConfig(
                require_header=_as_bool(os.getenv("TENANT_REQUIRE_HEADER", "0"), False),
                default_tenant_id=(os.getenv("TENANT_DEFAULT_ID", "public") or "public").strip() or "public",
                header_name=(os.getenv("TENANT_HEADER_NAME", "X-Tenant-Id") or "X-Tenant-Id").strip() or "X-Tenant-Id",
            ),
            capacity=CapacityConfig(
                disk_soft_limit_bytes=_as_int(os.getenv("CAPACITY_DISK_SOFT_LIMIT_BYTES", str(3 * 1024 * 1024 * 1024)), 3 * 1024 * 1024 * 1024, 0),
                disk_hard_limit_bytes=_as_int(os.getenv("CAPACITY_DISK_HARD_LIMIT_BYTES", str(1 * 1024 * 1024 * 1024)), 1 * 1024 * 1024 * 1024, 0),
                pause_on_hard_limit=_as_bool(os.getenv("CAPACITY_PAUSE_ON_HARD_LIMIT", "1"), True),
            ),
            auth=AuthConfig(
                enabled=_as_bool(os.getenv("AUTH_JWT_ENABLED", "0"), False),
                issuer=(os.getenv("AUTH_JWT_ISSUER", "") or "").strip(),
                audience=(os.getenv("AUTH_JWT_AUDIENCE", "") or "").strip(),
                jwks_url=(os.getenv("AUTH_JWT_JWKS_URL", "") or "").strip(),
                leeway_seconds=_as_int(os.getenv("AUTH_JWT_LEEWAY_SECONDS", "60"), 60, 0),
                require_membership_check=_as_bool(os.getenv("AUTH_REQUIRE_MEMBERSHIP_CHECK", "1"), True),
                tenant_claim_key=(os.getenv("AUTH_TENANT_CLAIM_KEY", "tenant_id") or "tenant_id").strip() or "tenant_id",
                roles_claim_key=(os.getenv("AUTH_ROLES_CLAIM_KEY", "roles") or "roles").strip() or "roles",
                permissions_claim_key=(os.getenv("AUTH_PERMISSIONS_CLAIM_KEY", "permissions") or "permissions").strip()
                or "permissions",
            ),
        )

