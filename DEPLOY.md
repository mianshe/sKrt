# 免费部署指南

## 方案架构
- 后端：Render.com（免费 750 小时/月）
- 前端：Vercel（完全免费）
- 数据库：Supabase（免费 500MB PostgreSQL）

## 一、数据库部署（Supabase）

1. 访问 https://supabase.com 注册账号
2. 创建新项目，等待初始化完成
3. 进入项目设置 → Database → Connection string
4. 复制 URI 格式的连接字符串（类似）：
   ```
   postgresql://postgres:[password]@[host]:5432/postgres
   ```

## 二、后端部署（Render）

1. 访问 https://render.com 注册账号
2. 点击 "New +" → "Web Service"
3. 连接你的 GitHub 仓库
4. Render 会自动检测 `render.yaml` 配置
5. 在环境变量中添加：
   - `DATABASE_URL`: 粘贴 Supabase 连接字符串
6. 点击 "Create Web Service"
7. 等待部署完成，复制后端 URL（如 `https://xm-backend.onrender.com`）

## 三、前端部署（Vercel）

1. 访问 https://vercel.com 注册账号
2. 点击 "Add New..." → "Project"
3. 导入你的 GitHub 仓库
4. 本项目在 `xm1/frontend/api/[...path].ts` 内提供了同域 `/api/*` 代理（Serverless Function）
5. 在环境变量中添加：
   - `VITE_API_BASE`: 设置为 `/api`
   - `BACKEND_URL`: 粘贴后端 URL（如 `https://xm-backend.onrender.com`）
6. 点击 "Deploy"
7. 等待部署完成

## 四、前端配置后端地址

本地开发时，修改 `xm1/frontend/.env.example` 或创建 `xm1/frontend/.env`：
```
VITE_API_BASE=http://localhost:8000
```

## 注意事项

1. **Render 免费层限制**：
   - 15 分钟无请求会自动休眠
   - 首次访问需要 30-60 秒唤醒

2. **文件存储**：
   - Render 提供 1GB 持久化磁盘（已配置在 render.yaml）
   - 挂载路径：`/opt/render/project/src/data`
   - 如需对象存储持久化（推荐论文 PDF）：推荐 Cloudflare R2（后端代写，前端不直传）：
     - `R2_ACCOUNT_ID`
     - `R2_ENDPOINT=https://<account_id>.r2.cloudflarestorage.com`
     - `R2_BUCKET=xm1-docs`
     - `R2_ACCESS_KEY_ID`（仅后端）
     - `R2_SECRET_ACCESS_KEY`（仅后端）
     - `R2_STORAGE_PREFIX=xm1`（可选）
     - `R2_DELETE_LOCAL_AFTER_UPLOAD=1`（可选：上传到 R2 后删除本地文件以省磁盘）
   - Supabase Storage 仍可作为兜底方案（未启用 R2 时生效）：
     - `SUPABASE_URL`
     - `SUPABASE_SERVICE_ROLE_KEY`（仅后端）
     - `SUPABASE_STORAGE_BUCKET=documents`
     - `SUPABASE_STORAGE_PREFIX=xm1`（可选）
     - `SUPABASE_DELETE_LOCAL_AFTER_UPLOAD=1`（可选）
   - 可开启自动清理（每周）以控制磁盘占用：
     - `CLEANUP_ENABLED=1`
     - `CLEANUP_INTERVAL_HOURS=168`
     - `CLEANUP_CHUNK_RETENTION_HOURS=24`
     - `CLEANUP_OCR_CACHE_RETENTION_HOURS=72`
     - `CLEANUP_FAILED_FILE_RETENTION_HOURS=168`

3. **数据库连接**：
   - Supabase 免费层有连接数限制（最多 60 个并发）
   - 建议在代码中使用连接池

## 部署完成

访问 Vercel 提供的域名即可使用你的应用！
