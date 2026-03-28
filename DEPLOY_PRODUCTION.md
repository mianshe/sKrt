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

---

## 本项目新增落地项（易支付 + 邮件随机码 + RunPod）

### 1) 易支付（微信/支付宝）
- 必填环境变量：
  - `PAY_PROVIDER=easypay`
  - `PAY_NOTIFY_URL=https://<你的域名>/api/gpu/ocr/pay/notify`
  - `EASYPAY_API_BASE`
  - `EASYPAY_PID`
  - `EASYPAY_KEY`
- 行为：
  - 前端仍调用原接口 `/gpu/ocr/pay/order/create`、`/gpu/ocr/pay/order/{order_no}`
  - 支付回调命中 `/gpu/ocr/pay/notify` 后自动给 `GPU` 余额加页，幂等防重复入账

### 2) 隐藏入口随机码邮件
- 必填环境变量：
  - `CODE_EMAIL_TO`（接收随机码邮箱）
  - `SMTP_HOST` `SMTP_PORT` `SMTP_USERNAME` `SMTP_PASSWORD` `SMTP_FROM` `SMTP_USE_TLS`
- 行为：
  - 上传页标题副标题连点触发隐藏窗口时，后端发送随机码邮件
  - 随机码可用于：特殊用户解锁、GPU 兑换加页（一次性、过期失效）

### 3) RunPod 下沉 OCR+解析+向量化
- 必填环境变量：
  - `RUNPOD_ENABLED=1`
  - `RUNPOD_INGEST_ENDPOINT`
  - `RUNPOD_API_KEY`
  - `RUNPOD_CALLBACK_URL=https://<你的域名>/api/ingestion/runpod/callback`
  - `RUNPOD_CALLBACK_SECRET`
- 行为：
  - 新上传任务默认提交到 RunPod
  - RunPod 完成后调用回调接口更新任务状态并触发关系重建
  - 回调需带签名，防止伪造请求

### 4) GPU 云主机自动开机脚本（腾讯云 / 阿里云）

适用于：**自建 GPU Worker**、无法人工随时开机、又未使用 RunPod 时，由香港 CPU 机通过 API 远程开机。

- 脚本目录：[scripts/gpu_autostart/](scripts/gpu_autostart/README.md)（内含安装步骤、`--dry-run`、IAM 最小权限说明）。
- 环境变量示例见根目录 [.env.example](.env.example) 中 `GPU_AUTOSTART_*`。
- **注意**：开机后 GPU 机上的系统与 HTTP Worker 仍需 **冷启动时间**；若要用户无感，需在应用侧做 **队列 + 重试 / 健康检查**（本仓库当前仅提供独立脚本，不包含与入库队列的自动耦合）。
- **上传页**：`GPU_AUTOSTART_ENABLED=1` 时，GPU 确认弹窗出现会调 `POST /gpu/autostart/start`，取消会调 `POST /gpu/autostart/stop`；IAM 需含 **StopInstances**。
- **空闲关机**：`use_gpu_ocr` 入库任务或 RunPod 回调进入终态后，经 `GPU_AUTOSTOP_IDLE_SECONDS`（默认 120s，最小 30s）防抖，若全局无未完成 GPU 任务则自动调云 API 关机。

---

## 前端 API 与「Failed to fetch」排查

### 浏览器侧（发码、兑换、支付下单等）

1. 打开 DevTools → **Network**，复现失败后选中 **`send-code`**、`redeem/send-code`、`pay/order/create` 等请求。
2. 查看 **Request URL**：若页面是 `https://…` 而请求仍指向 `http://localhost:8000`、内网地址或与页面**协议/域名**不一致，常见结果为 **Failed to fetch**（不可达或**混合内容**被拦截）。
3. 查看 **Console** 是否出现 **CORS**、**mixed content** 相关报错。

### 生产推荐：`VITE_API_BASE=/api` 与反代

- 执行 `npm run build` **之前**设置：`VITE_API_BASE=/api`，使浏览器请求与当前站点同源。
- Nginx 将 `/api/` 代理到 uvicorn，并去掉 `/api` 前缀（后端路由为 `/auth/...`、`/gpu/...` 等，无前缀）：

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

### `CORS_ALLOW_ORIGINS`（跨域 API 时）

发码等请求使用 `credentials: "include"` 时，规范不允许 `Access-Control-Allow-Origin: *`。若前端与 API **不同源**，在后端设置精确来源，例如：

`CORS_ALLOW_ORIGINS=https://你的前端域名`

多个来源用英文逗号分隔，**不要**尾斜杠。使用上文同源 `/api` 时通常不必改 CORS。

### 「Failed to fetch」与邮件 / SMTP 失败的区别

| 现象 | 含义 |
|------|------|
| 界面为「无法连接后端…」或英文 **Failed to fetch** | 浏览器**未收到有效 HTTP 响应**，优先查 **VITE_API_BASE**、HTTPS/HTTP、反代、CORS。 |
| HTTP **502**，响应中含「发送失败」、SMTP 等 | 请求已到后端，**发邮件**等环节失败，查 `SMTP_*`、`CODE_EMAIL_TO` 及出站 **25/587/465** 是否被云厂商拦截。 |

---

## 前端发布后「页面没变化」排查

1. **服务器**：`git pull` 后必须在 `frontend/` 执行 `npm run build`（Nginx `root` 指向 `dist` 时只 pull 不会更新静态文件）。
2. **一键自检**（在项目根目录）：
   ```bash
   bash scripts/verify-frontend-deploy.sh
   ```
   确认 `quotaLoadError` 存在、`dist/index.html` 中 `index-*.js` 文件名与 Network 里一致。
3. **浏览器 / PWA**：
   - DevTools → Network 勾选 **Disable cache**，硬刷新。
   - **Application** → **Service Workers** → **Unregister**；**Storage** → **清除站点数据**（或无痕窗口再试）。
   - 对比 Network 里加载的 `/assets/index-xxxx.js` 与服务器 `dist/index.html` 是否同一文件名；不一致即为旧 SW/缓存。
4. **构建侧**：已调整 `vite.config.ts` 中 Workbox **不再 precache `*.html`**，新部署后更易拿到新入口（仍建议发版后清一次 SW 以淘汰旧清单）。
