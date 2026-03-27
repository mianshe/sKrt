# 四库树状深度流水线（PostgreSQL）

## 配置

1. 安装依赖：`pip install -r requirements.txt`（含 `psycopg2-binary`）。
2. 准备 PostgreSQL 数据库，设置环境变量：
   - `DATABASE_URL` 或 `POSTGRES_URL`，例如：  
     `postgresql://user:pass@localhost:5432/xm_pipeline`
3. 启动后端后，lifespan 会自动执行 `pg_schema.sql` 建表。

### 流水线参数优先级

1. **`POST /pipeline/deep-report/start` 请求体中的 `config`**（按字段覆盖）  
2. **环境变量默认值**（未在 `config` 中出现的字段才使用 env）

| 字段 | 环境变量 | 说明 |
|------|----------|------|
| `batch_chunk_size` | `PIPELINE_BATCH_CHUNK_SIZE` | 每批 chunk 数 |
| `group_count` | `PIPELINE_GROUP_COUNT` | 抽象分组数（1–3） |
| `max_chunks` | `PIPELINE_MAX_CHUNKS` | 单文档最多参与流水线的 chunk 数 |
| `validation_flush_interval` | `PIPELINE_VALIDATION_FLUSH_INTERVAL` | 每累计多少次树刷新触发一次校验 |
| `validation_segment_rotate` | `PIPELINE_VALIDATION_SEGMENT_ROTATE` | 每完成多少次校验后新开一段校验子图（默认 3） |

合并规则：`merged = { **env 默认, **body.config }`（代码里以 `RuntimeConfig.pipeline` 为默认字典，再被请求 `config` 覆盖）。

### 校验子图分片

校验图谱按 `validation_segments` 分片；`validation_runs` 与 `graph_role=validation` 的 `kg_nodes`/`kg_edges` 带 `segment_id`。新一轮校验的提示词只紧凑引用**当前分片**内已有校验子图，避免上下文随历史无限膨胀。

### 删除文档

删除 SQLite 文档（`DELETE /documents/{id}` 等入口）时，若已配置 PostgreSQL，会尝试 `DELETE pipeline_jobs WHERE sqlite_document_id = ?`，子表通过 `ON DELETE CASCADE` 一并清理。

## API

- `POST /pipeline/deep-report/start`  
  Body: `{ "document_id": 1, "discipline": "all", "config": { "batch_chunk_size": 30, "group_count": 3, "max_chunks": 500, "validation_flush_interval": 3, "validation_segment_rotate": 3 } }`  
  返回 `{ "job_id", "status": "queued" }`。

- `GET /pipeline/deep-report/{job_id}`  
  查询任务状态（`queued` / `running` / `completed` / `failed`）。

- `GET /presentation-tree/{doc_id}`  
  返回最近一条**已完成**任务的展示树与 `tree_nodes`（JSON 已尽量解析）。

## 健康检查

`GET /health` 含 `postgres_pipeline_enabled`（是否配置了 `DATABASE_URL`）。

## 与 SQLite 的关系

向量与原文 chunk 仍来自 `knowledge.db`（`RAGEngine.load_document_chunks`）；PostgreSQL 仅存流水线分层结果与图谱边。
