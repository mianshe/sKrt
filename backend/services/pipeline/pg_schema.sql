-- 四库树状分块流水线 — PostgreSQL schema（与 SQLite 向量库并行）
-- 执行前需有可用数据库；由代码在连接后运行本脚本。

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    sqlite_document_id INTEGER NOT NULL,
    discipline TEXT NOT NULL DEFAULT 'all',
    status TEXT NOT NULL DEFAULT 'queued',
    pipeline_version INTEGER NOT NULL DEFAULT 1,
    config JSONB NOT NULL DEFAULT '{}',
    error_message TEXT,
    result_summary JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_doc ON pipeline_jobs (sqlite_document_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_status ON pipeline_jobs (status);
CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_tenant ON pipeline_jobs (tenant_id, sqlite_document_id);

CREATE TABLE IF NOT EXISTS tenant_users (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    roles TEXT[] NOT NULL DEFAULT ARRAY['tenant_member']::text[],
    permissions TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_users_tenant_status ON tenant_users (tenant_id, status);

CREATE TABLE IF NOT EXISTS tenant_quotas (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL UNIQUE,
    max_documents INTEGER NOT NULL DEFAULT 1000,
    max_vectors INTEGER NOT NULL DEFAULT 1000000,
    max_storage_bytes BIGINT NOT NULL DEFAULT 5368709120,
    max_requests_per_min INTEGER NOT NULL DEFAULT 1200,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    result TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    reason TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_user_created ON audit_logs (tenant_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action_created ON audit_logs (action, created_at DESC);

CREATE TABLE IF NOT EXISTS security_events (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    message TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_security_events_tenant_created ON security_events (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS staging_scan_chunks (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    task_id BIGINT NOT NULL,
    sqlite_document_id INTEGER NOT NULL,
    chunk_seq INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    section_path TEXT NOT NULL,
    content TEXT NOT NULL,
    chunk_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, task_id, chunk_hash)
);

CREATE INDEX IF NOT EXISTS idx_staging_scan_tenant_task_status
    ON staging_scan_chunks (tenant_id, task_id, status, chunk_seq);

CREATE TABLE IF NOT EXISTS staging_vector_chunks (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    task_id BIGINT NOT NULL,
    sqlite_document_id INTEGER NOT NULL,
    sqlite_vector_id BIGINT,
    chunk_id TEXT NOT NULL,
    section_path TEXT NOT NULL,
    chunk_hash TEXT NOT NULL,
    embedding_json JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'queued',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, task_id, chunk_hash)
);

CREATE INDEX IF NOT EXISTS idx_staging_vector_tenant_task_status
    ON staging_vector_chunks (tenant_id, task_id, status, id);

CREATE TABLE IF NOT EXISTS chat_work_memory (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'public',
    user_id TEXT NOT NULL DEFAULT 'anonymous',
    session_id TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    source_json JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expire_at TIMESTAMPTZ
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'chat_work_memory'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'chat_work_memory' AND column_name = 'user_id'
    ) THEN
        ALTER TABLE chat_work_memory
            ADD COLUMN user_id TEXT NOT NULL DEFAULT 'anonymous';
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_chat_work_memory_tenant_session
    ON chat_work_memory (tenant_id, user_id, session_id, created_at DESC);

-- RLS: 关键业务表按 app.tenant_id 行级隔离
ALTER TABLE pipeline_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging_scan_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging_vector_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_work_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_quotas ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'pipeline_jobs' AND policyname = 'p_pipeline_jobs_tenant_rls') THEN
        CREATE POLICY p_pipeline_jobs_tenant_rls ON pipeline_jobs
        USING (tenant_id = current_setting('app.tenant_id', true));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'staging_scan_chunks' AND policyname = 'p_staging_scan_tenant_rls') THEN
        CREATE POLICY p_staging_scan_tenant_rls ON staging_scan_chunks
        USING (tenant_id = current_setting('app.tenant_id', true));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'staging_vector_chunks' AND policyname = 'p_staging_vector_tenant_rls') THEN
        CREATE POLICY p_staging_vector_tenant_rls ON staging_vector_chunks
        USING (tenant_id = current_setting('app.tenant_id', true));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'chat_work_memory' AND policyname = 'p_chat_memory_tenant_rls') THEN
        CREATE POLICY p_chat_memory_tenant_rls ON chat_work_memory
        USING (tenant_id = current_setting('app.tenant_id', true));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'audit_logs' AND policyname = 'p_audit_logs_tenant_rls') THEN
        CREATE POLICY p_audit_logs_tenant_rls ON audit_logs
        USING (tenant_id = current_setting('app.tenant_id', true));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'security_events' AND policyname = 'p_security_events_tenant_rls') THEN
        CREATE POLICY p_security_events_tenant_rls ON security_events
        USING (tenant_id = current_setting('app.tenant_id', true));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'tenant_users' AND policyname = 'p_tenant_users_tenant_rls') THEN
        CREATE POLICY p_tenant_users_tenant_rls ON tenant_users
        USING (tenant_id = current_setting('app.tenant_id', true));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'tenant_quotas' AND policyname = 'p_tenant_quotas_tenant_rls') THEN
        CREATE POLICY p_tenant_quotas_tenant_rls ON tenant_quotas
        USING (tenant_id = current_setting('app.tenant_id', true));
    END IF;
END $$;

-- 校验子图分片：同一 job 下多段 validation 图谱，避免单次校验上下文无限膨胀
CREATE TABLE IF NOT EXISTS validation_segments (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES pipeline_jobs (id) ON DELETE CASCADE,
    segment_index INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, segment_index)
);

CREATE INDEX IF NOT EXISTS idx_validation_segments_job ON validation_segments (job_id);

CREATE TABLE IF NOT EXISTS ingest_batches (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES pipeline_jobs (id) ON DELETE CASCADE,
    batch_index INTEGER NOT NULL,
    chunk_start_idx INTEGER,
    chunk_end_idx INTEGER,
    source_refs JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, batch_index)
);

CREATE INDEX IF NOT EXISTS idx_ingest_batches_job ON ingest_batches (job_id);

CREATE TABLE IF NOT EXISTS chunk_units (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES ingest_batches (id) ON DELETE CASCADE,
    sqlite_vector_id INTEGER,
    chunk_id TEXT,
    section_path TEXT,
    content_preview TEXT,
    meta JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_chunk_units_batch ON chunk_units (batch_id);

CREATE TABLE IF NOT EXISTS evidence_spans (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES ingest_batches (id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL,
    span_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS abstraction_runs (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES ingest_batches (id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL,
    strategy TEXT,
    abstraction_json JSONB NOT NULL DEFAULT '{}',
    provider TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kg_nodes (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES pipeline_jobs (id) ON DELETE CASCADE,
    graph_role TEXT NOT NULL,
    external_key TEXT NOT NULL,
    label TEXT,
    payload JSONB NOT NULL DEFAULT '{}',
    batch_id BIGINT REFERENCES ingest_batches (id) ON DELETE SET NULL,
    segment_id BIGINT REFERENCES validation_segments (id) ON DELETE CASCADE,
    segment_discrim BIGINT GENERATED ALWAYS AS (COALESCE(segment_id, 0::bigint)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kg_nodes_job_role ON kg_nodes (job_id, graph_role);

CREATE TABLE IF NOT EXISTS kg_edges (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES pipeline_jobs (id) ON DELETE CASCADE,
    graph_role TEXT NOT NULL,
    source_key TEXT NOT NULL,
    target_key TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    segment_id BIGINT REFERENCES validation_segments (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_job_role ON kg_edges (job_id, graph_role);

CREATE TABLE IF NOT EXISTS reasoning_traces (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES pipeline_jobs (id) ON DELETE CASCADE,
    batch_id BIGINT REFERENCES ingest_batches (id) ON DELETE SET NULL,
    trace_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS presentation_trees (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES pipeline_jobs (id) ON DELETE CASCADE,
    sqlite_document_id INTEGER NOT NULL,
    root_node_id BIGINT,
    meta JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_presentation_trees_job ON presentation_trees (job_id);

CREATE TABLE IF NOT EXISTS tree_nodes (
    id BIGSERIAL PRIMARY KEY,
    tree_id BIGINT NOT NULL REFERENCES presentation_trees (id) ON DELETE CASCADE,
    parent_id BIGINT REFERENCES tree_nodes (id) ON DELETE CASCADE,
    path TEXT NOT NULL DEFAULT '/',
    sort_order INTEGER NOT NULL DEFAULT 0,
    payload JSONB NOT NULL DEFAULT '{}',
    source_span_refs JSONB NOT NULL DEFAULT '[]',
    projection_round INTEGER NOT NULL DEFAULT 2,
    superseded_by BIGINT REFERENCES tree_nodes (id) ON DELETE SET NULL,
    flush_batch_index INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tree_nodes_tree ON tree_nodes (tree_id);
CREATE INDEX IF NOT EXISTS idx_tree_nodes_parent ON tree_nodes (tree_id, parent_id);

CREATE TABLE IF NOT EXISTS validation_runs (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES pipeline_jobs (id) ON DELETE CASCADE,
    tree_id BIGINT REFERENCES presentation_trees (id) ON DELETE SET NULL,
    segment_id BIGINT REFERENCES validation_segments (id) ON DELETE CASCADE,
    trigger_after_flushes INTEGER NOT NULL DEFAULT 3,
    result_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_validation_runs_job ON validation_runs (job_id);

CREATE INDEX IF NOT EXISTS idx_kg_edges_validation_segment ON kg_edges (job_id, graph_role, segment_id);

ALTER TABLE validation_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunk_units ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_spans ENABLE ROW LEVEL SECURITY;
ALTER TABLE abstraction_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE reasoning_traces ENABLE ROW LEVEL SECURITY;
ALTER TABLE presentation_trees ENABLE ROW LEVEL SECURITY;
ALTER TABLE tree_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE validation_runs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'validation_segments' AND policyname = 'p_validation_segments_tenant_rls') THEN
        CREATE POLICY p_validation_segments_tenant_rls ON validation_segments
        USING (
            EXISTS (
                SELECT 1 FROM pipeline_jobs pj
                WHERE pj.id = validation_segments.job_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ingest_batches' AND policyname = 'p_ingest_batches_tenant_rls') THEN
        CREATE POLICY p_ingest_batches_tenant_rls ON ingest_batches
        USING (
            EXISTS (
                SELECT 1 FROM pipeline_jobs pj
                WHERE pj.id = ingest_batches.job_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'chunk_units' AND policyname = 'p_chunk_units_tenant_rls') THEN
        CREATE POLICY p_chunk_units_tenant_rls ON chunk_units
        USING (
            EXISTS (
                SELECT 1
                FROM ingest_batches ib
                JOIN pipeline_jobs pj ON pj.id = ib.job_id
                WHERE ib.id = chunk_units.batch_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'evidence_spans' AND policyname = 'p_evidence_spans_tenant_rls') THEN
        CREATE POLICY p_evidence_spans_tenant_rls ON evidence_spans
        USING (
            EXISTS (
                SELECT 1
                FROM ingest_batches ib
                JOIN pipeline_jobs pj ON pj.id = ib.job_id
                WHERE ib.id = evidence_spans.batch_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'abstraction_runs' AND policyname = 'p_abstraction_runs_tenant_rls') THEN
        CREATE POLICY p_abstraction_runs_tenant_rls ON abstraction_runs
        USING (
            EXISTS (
                SELECT 1
                FROM ingest_batches ib
                JOIN pipeline_jobs pj ON pj.id = ib.job_id
                WHERE ib.id = abstraction_runs.batch_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'kg_nodes' AND policyname = 'p_kg_nodes_tenant_rls') THEN
        CREATE POLICY p_kg_nodes_tenant_rls ON kg_nodes
        USING (
            EXISTS (
                SELECT 1 FROM pipeline_jobs pj
                WHERE pj.id = kg_nodes.job_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'kg_edges' AND policyname = 'p_kg_edges_tenant_rls') THEN
        CREATE POLICY p_kg_edges_tenant_rls ON kg_edges
        USING (
            EXISTS (
                SELECT 1 FROM pipeline_jobs pj
                WHERE pj.id = kg_edges.job_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'reasoning_traces' AND policyname = 'p_reasoning_traces_tenant_rls') THEN
        CREATE POLICY p_reasoning_traces_tenant_rls ON reasoning_traces
        USING (
            EXISTS (
                SELECT 1 FROM pipeline_jobs pj
                WHERE pj.id = reasoning_traces.job_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'presentation_trees' AND policyname = 'p_presentation_trees_tenant_rls') THEN
        CREATE POLICY p_presentation_trees_tenant_rls ON presentation_trees
        USING (
            EXISTS (
                SELECT 1 FROM pipeline_jobs pj
                WHERE pj.id = presentation_trees.job_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'tree_nodes' AND policyname = 'p_tree_nodes_tenant_rls') THEN
        CREATE POLICY p_tree_nodes_tenant_rls ON tree_nodes
        USING (
            EXISTS (
                SELECT 1
                FROM presentation_trees pt
                JOIN pipeline_jobs pj ON pj.id = pt.job_id
                WHERE pt.id = tree_nodes.tree_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'validation_runs' AND policyname = 'p_validation_runs_tenant_rls') THEN
        CREATE POLICY p_validation_runs_tenant_rls ON validation_runs
        USING (
            EXISTS (
                SELECT 1 FROM pipeline_jobs pj
                WHERE pj.id = validation_runs.job_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 幂等迁移：已有库（旧 kg_nodes 唯一约束、无 segment 列）
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'kg_nodes'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'kg_nodes' AND column_name = 'segment_id'
    ) THEN
        ALTER TABLE kg_nodes
            ADD COLUMN segment_id BIGINT REFERENCES validation_segments (id) ON DELETE CASCADE;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'pipeline_jobs'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'pipeline_jobs' AND column_name = 'tenant_id'
    ) THEN
        ALTER TABLE pipeline_jobs
            ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'public';
    END IF;
END $$;

ALTER TABLE kg_nodes DROP CONSTRAINT IF EXISTS kg_nodes_job_id_graph_role_external_key_key;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'kg_nodes'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'kg_nodes' AND column_name = 'segment_discrim'
    ) THEN
        ALTER TABLE kg_nodes
            ADD COLUMN segment_discrim BIGINT
            GENERATED ALWAYS AS (COALESCE(segment_id, 0::bigint)) STORED;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'kg_edges'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'kg_edges' AND column_name = 'segment_id'
    ) THEN
        ALTER TABLE kg_edges
            ADD COLUMN segment_id BIGINT REFERENCES validation_segments (id) ON DELETE CASCADE;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'validation_runs'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'validation_runs' AND column_name = 'segment_id'
    ) THEN
        ALTER TABLE validation_runs
            ADD COLUMN segment_id BIGINT REFERENCES validation_segments (id) ON DELETE CASCADE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS uq_kg_nodes_job_role_key_discrim
    ON kg_nodes (job_id, graph_role, external_key, segment_discrim);

-- 事实暂存表：用于存储各批次提取的原始事实片段，供全局合成（Synthesis）阶段使用
CREATE TABLE IF NOT EXISTS fact_staging (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES pipeline_jobs (id) ON DELETE CASCADE,
    batch_id BIGINT REFERENCES ingest_batches (id) ON DELETE CASCADE,
    fact_text TEXT NOT NULL,
    source_chunk_id TEXT,
    section_path TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fact_staging_job ON fact_staging (job_id);

ALTER TABLE fact_staging ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'fact_staging' AND policyname = 'p_fact_staging_tenant_rls') THEN
        CREATE POLICY p_fact_staging_tenant_rls ON fact_staging
        USING (
            EXISTS (
                SELECT 1 FROM pipeline_jobs pj
                WHERE pj.id = fact_staging.job_id
                  AND pj.tenant_id = current_setting('app.tenant_id', true)
            )
        );
    END IF;
END $$;
