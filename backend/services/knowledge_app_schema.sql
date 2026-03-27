-- 知识库应用主表（与 SQLite knowledge.db 对齐），KNOWLEDGE_STORE=postgres 时由 knowledge_store 初始化。
-- 与 pipeline/pg_schema.sql 四库表共用同一 DATABASE_URL。

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    filename TEXT NOT NULL,
    title TEXT NOT NULL,
    discipline TEXT NOT NULL,
    document_type TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vectors (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    document_id INTEGER NOT NULL REFERENCES documents(id),
    chunk_id TEXT NOT NULL,
    content TEXT NOT NULL,
    section_path TEXT NOT NULL,
    embedding TEXT NOT NULL,
    page_num INTEGER NOT NULL DEFAULT 0,
    chunk_type TEXT NOT NULL DEFAULT 'knowledge'
);

CREATE TABLE IF NOT EXISTS kg_relations (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    explanation TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    user_id TEXT NOT NULL DEFAULT 'anonymous',
    session_id TEXT NOT NULL DEFAULT 'default',
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS ocr_page_cache (
    id SERIAL PRIMARY KEY,
    task_id INTEGER NOT NULL,
    page_num INTEGER NOT NULL,
    ocr_text TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (task_id, page_num)
);

CREATE TABLE IF NOT EXISTS gpu_ocr_daily_pages (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    client_id TEXT NOT NULL,
    day TEXT NOT NULL,
    pages_used INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, client_id, day)
);

CREATE TABLE IF NOT EXISTS gpu_ocr_global_monthly_pages (
    month_key TEXT PRIMARY KEY,
    pages_used INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gpu_ocr_daily_usage (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    client_id TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, client_id, day)
);

CREATE TABLE IF NOT EXISTS gpu_ocr_global_monthly_usage (
    month_key TEXT PRIMARY KEY,
    used INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gpu_ocr_paid_pages_balance (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    client_id TEXT NOT NULL,
    pages_balance INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, client_id)
);

CREATE TABLE IF NOT EXISTS gpu_ocr_paid_pages_ledger (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    client_id TEXT NOT NULL,
    delta_pages INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pay_orders (
    id SERIAL PRIMARY KEY,
    order_no TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    client_id TEXT NOT NULL,
    pack_key TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    amount_cny DOUBLE PRECISION NOT NULL DEFAULT 0,
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
);

CREATE TABLE IF NOT EXISTS pay_callbacks (
    id SERIAL PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'payjs',
    event_type TEXT NOT NULL DEFAULT 'notify',
    order_no TEXT NOT NULL DEFAULT '',
    payload_text TEXT NOT NULL DEFAULT '',
    sign_ok INTEGER NOT NULL DEFAULT 0,
    handled INTEGER NOT NULL DEFAULT 0,
    result_text TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS upload_tasks (
    id SERIAL PRIMARY KEY,
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
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    use_gpu_ocr INTEGER NOT NULL DEFAULT 0,
    extract_started_at TEXT,
    extract_finished_at TEXT,
    index_started_at TEXT,
    index_finished_at TEXT,
    extract_duration_sec DOUBLE PRECISION,
    index_duration_sec DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS vector_ingest_checkpoints (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    task_id INTEGER NOT NULL REFERENCES upload_tasks(id),
    document_id INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    chunk_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    vector_id INTEGER,
    last_error TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (task_id, chunk_hash)
);

CREATE TABLE IF NOT EXISTS ingestion_timing_rollups (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    task_count INTEGER NOT NULL DEFAULT 0,
    sum_extract_sec DOUBLE PRECISION NOT NULL DEFAULT 0,
    sum_index_sec DOUBLE PRECISION NOT NULL DEFAULT 0,
    sum_file_mb DOUBLE PRECISION NOT NULL DEFAULT 0,
    sum_page_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS document_summaries (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    document_id INTEGER NOT NULL UNIQUE REFERENCES documents(id),
    granularity TEXT NOT NULL,
    version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    artifact_path TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS upload_throttle_minute (
    tenant_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    minute_key TEXT NOT NULL,
    created_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, client_id, minute_key)
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id, id);
CREATE INDEX IF NOT EXISTS idx_vectors_tenant_doc ON vectors(tenant_id, document_id, chunk_id);
CREATE INDEX IF NOT EXISTS idx_kg_relations_tenant ON kg_relations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_lookup ON chat_sessions(tenant_id, session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ocr_page_cache ON ocr_page_cache(task_id, page_num);
CREATE INDEX IF NOT EXISTS idx_gpu_ocr_daily_pages_lookup ON gpu_ocr_daily_pages(tenant_id, client_id, day);
CREATE INDEX IF NOT EXISTS idx_gpu_ocr_usage_lookup ON gpu_ocr_daily_usage(tenant_id, client_id, day);
CREATE INDEX IF NOT EXISTS idx_gpu_ocr_paid_pages_balance_lookup ON gpu_ocr_paid_pages_balance(tenant_id, client_id);
CREATE INDEX IF NOT EXISTS idx_gpu_ocr_paid_pages_ledger_lookup ON gpu_ocr_paid_pages_ledger(tenant_id, client_id, created_at);
CREATE INDEX IF NOT EXISTS idx_pay_orders_lookup ON pay_orders(order_no, tenant_id, client_id);
CREATE INDEX IF NOT EXISTS idx_pay_orders_status ON pay_orders(status, created_at);
CREATE INDEX IF NOT EXISTS idx_pay_callbacks_lookup ON pay_callbacks(order_no, created_at);
CREATE INDEX IF NOT EXISTS idx_upload_tasks_tenant ON upload_tasks(tenant_id, id);
CREATE INDEX IF NOT EXISTS idx_vectors_doc_chunk ON vectors(document_id, chunk_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_task_status ON vector_ingest_checkpoints(task_id, status);
CREATE INDEX IF NOT EXISTS idx_checkpoints_tenant_task ON vector_ingest_checkpoints(tenant_id, task_id);
CREATE INDEX IF NOT EXISTS idx_document_summaries_tenant_doc ON document_summaries(tenant_id, document_id);

INSERT INTO ingestion_timing_rollups (id, task_count)
VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;
