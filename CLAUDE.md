# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 工作目录说明

Git 仓库根为 `d:\xm`；本文件描述的是 **`xm1` 子项目**（全栈应用）。

- **全栈维护**：在 `d:\xm\xm1` 下运行 `claude`
- `d:\xm\frontend` 是独立前端副本，修改前确认以哪侧为准

## 构建与测试命令

**后端测试**（工作目录：`xm1` 根目录）：
```bash
.\backend\.venv\Scripts\python.exe -m pytest backend\tests -q
# 单个测试文件：
.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_security_isolation.py -q
```

**前端构建**（工作目录：`xm1\frontend`）：
```bash
npm run build
```

**一键开发启动**（需先创建 venv：`py -3.11 -m venv backend\.venv`）：
```powershell
./start.ps1   # 安装依赖 → 启动 uvicorn:8000 + vite:5173
```

## 架构概览

### 后端（`backend/`）

FastAPI 应用，ASGI 入口 `backend.main:app`。数据存储：

- **SQLite**（`data/knowledge.db`）：主存储，保存 `documents`、`vectors`（含 embedding）、`kg_relations`、`upload_tasks`、`document_summaries` 等表
- **PostgreSQL**（可选）：仅用于"四库树状深度报告"流水线（`/pipeline/deep-report/*`）及聊天记忆/审计日志；通过 `DATABASE_URL` 环境变量开启

**核心服务模块**：

| 模块 | 职责 |
|------|------|
| `runtime_config.py` | 从环境变量加载所有配置（`RuntimeConfig.from_env()`） |
| `services/free_ai_router.py` | **AI 提供商级联路由**：GitHub Models → ZhipuAI → HuggingFace → 本地 transformers |
| `services/document_parser.py` | 解析 PDF/DOCX/TXT/MD，提取文本与元数据 |
| `services/chunker.py` | 将文档文本切块供向量化 |
| `services/rag_engine.py` | 管理 embedding 与向量检索（存储在 SQLite vectors 表） |
| `services/upload_ingestion_service.py` | 异步后台入库：解析 → 切块 → embedding → 写入 SQLite |
| `services/exam_processor.py` | 试卷解析与智能答题 |
| `services/kg_builder.py` | 基于文档元数据构建知识图谱 |
| `services/graphs/` | LangGraph 风格 Agent 状态机（chat/summary/report/deep-pipeline 图） |
| `services/pipeline/` | PostgreSQL 深度报告流水线（需 `DATABASE_URL`） |
| `services/security_context.py` | JWT 校验与 Identity 解析 |

**文档入库数据流**：
`POST /upload/tasks` → `UploadIngestionService.create_task()` → 后台 `asyncio.Task` → `parse → chunk → embed → SQLite`

### 前端（`frontend/`）

React + Vite + TypeScript + Tailwind，三标签页 PWA：

- `UploadTab`：上传文档（支持分块上传 `/upload/chunks/*`）、查看任务进度、删除文档
- `KnowledgeTab`：知识图谱可视化（调用 `/knowledge-graph`）
- `ChatTab`：RAG 问答（调用 `/chat`）、试卷上传分析

状态通过 `hooks/useDocuments.ts` 统一管理，组件只做展示。

## 多租户与鉴权

所有数据按 `tenant_id` 隔离。身份获取方式：

- **开发默认（`AUTH_JWT_ENABLED=0`）**：从请求头 `X-Tenant-Id` 读取，缺省为 `"public"`
- **生产（`AUTH_JWT_ENABLED=1`）**：校验 Bearer JWT，同时强制要求 `DATABASE_URL` 与 membership check

权限模型：每个 API 端点调用 `_require_permission(identity, "tenant.<resource>.<action>")`，`tenant_admin` 角色拥有所有 `tenant.*` 权限。

## 关键环境变量

开发时复制 `.env.example` 为 `.env`，至少配置一个 AI 提供商：

```
GITHUB_TOKEN=          # GitHub Models（embedding: text-embedding-3-small，chat: gpt-4o-mini）
ZHIPU_API_KEY=         # ZhipuAI（embedding-3，glm-4-flash）
HF_TOKEN=              # HuggingFace Inference API

DATABASE_URL=          # PostgreSQL，留空则深度报告 API 返回 503
APP_ENV=dev            # prod 时强制要求 JWT + PostgreSQL
HYBRID_LOCAL_FIRST=0   # 1 = 优先本地 transformers（需安装 torch）
BAIDU_OCR_API_KEY=     # 百度 OCR：与 BAIDU_OCR_SECRET_KEY 成对必填（见 .env.example）
BAIDU_OCR_SECRET_KEY=
OCR_API_BASE=          # 外部 HTTP OCR（POST …/ocr/pdf）；旧名 GPU_OCR_ENDPOINT 仍兼容；与百度无关
```

## 注意事项

- 修改后端后执行 pytest；修改前端后执行 `npm run build`
- `data/` 目录（uploads、SQLite DB）已在 `.gitignore` 中排除，勿提交
- 深度报告流水线（`/pipeline/deep-report/*`）需 PostgreSQL，本地开发可跳过
- 生产部署必须设置 `APP_ENV=prod`、`AUTH_JWT_ENABLED=1`、`DATABASE_URL`，否则启动时抛出异常
