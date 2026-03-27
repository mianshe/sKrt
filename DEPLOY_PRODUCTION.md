# 生产级免费/低成本方案

## 方案 A：纯免费（适合小规模测试）

### 文件存储
- **Supabase Storage**（与数据库同平台，适合小规模生产/展示）
  - 建议创建 bucket：`documents`
  - 后端使用 `SUPABASE_SERVICE_ROLE_KEY` 写入（不要下发到前端）
  - 对于大 PDF：优先做上传限流/排队与对象存储持久化

### GPU 推理
- **Hugging Face Inference API**（免费层）
  - 每月 30,000 次调用
  - 自动 GPU 加速
  - 限制：速率限制、排队等待

### 数据库
- **Supabase**（500MB）或 **Neon**（512MB）

### 容量预估
- 支持约 500 个文档（每个 50MB）
- 适合 50-100 人轻度使用

---

## 方案 B：低成本（$5-10/月，适合生产）

### 文件存储
- **Cloudflare R2**（$0/月 + 按量）
  - 10GB 免费存储
  - 无出站流量费用
  - 兼容 S3 API

### GPU 推理
- **Replicate**（按量付费）
  - $0.0002/秒（约 $0.01/次推理）
  - 自动 GPU 扩展
  - 或使用 **Together AI**（$0.0008/1K tokens）

### 后端
- **Railway**（$5/月）
  - 更稳定（不休眠）
  - 8GB RAM
  - 持久化存储

### 数据库
- **Neon**（免费 512MB）或升级到 **Supabase Pro**（$25/月，8GB）

### 容量预估
- 支持 1000+ 文档
- 适合 200-500 人使用

---

## 方案 C：完全免费但需要自己有 GPU

### 使用 Hugging Face Spaces
- 免费 GPU（T4，16GB）
- 限制：7 天无活动会休眠
- 适合演示和测试

---

## 推荐配置优先级

1. **立即可用**（纯免费）：
   - Cloudinary（文件）+ HF Inference（GPU）+ Supabase（数据库）

2. **小成本升级**（$5/月）：
   - Cloudinary + Replicate + Railway

3. **生产级**（$30/月）：
   - Cloudflare R2 + Together AI + Railway + Supabase Pro
