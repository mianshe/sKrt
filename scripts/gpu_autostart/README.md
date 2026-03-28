# GPU 云主机自动开机脚本

在香港 CPU 机（或任意能访问公网的环境）上调用云 API，将已关机的 **GPU 云服务器** 开机。密钥请用 **子账号 RAM / CAM**，并授予 **最小权限**。

## 安装依赖（独立于主后端亦可）

在 `xm1` 根目录：

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r scripts/gpu_autostart/requirements.txt
```

## 腾讯云 CVM

环境变量（也可使用兼容名 `TENCENTCLOUD_SECRET_ID` 等，见脚本内说明）：

| 变量 | 说明 |
|------|------|
| `GPU_AUTOSTART_TENCENT_SECRET_ID` | API 密钥 ID |
| `GPU_AUTOSTART_TENCENT_SECRET_KEY` | API 密钥 Key |
| `GPU_AUTOSTART_TENCENT_REGION` | 地域，如 `ap-hongkong` |
| `GPU_AUTOSTART_TENCENT_INSTANCE_IDS` | 实例 ID，多个用英文逗号分隔，如 `ins-xxx,ins-yyy` |

**建议权限策略**：`cvm:StartInstances`；若需查状态可加 `cvm:DescribeInstances`。资源条件限定到目标 `instance-id`。

运行：

```powershell
# 仅打印将开机的实例，不调用 API（可不配密钥）
.\backend\.venv\Scripts\python.exe scripts/gpu_autostart/tencent_start_gpu.py --dry-run

# 实际开机（从 xm1/.env 加载变量，需已配置上述 env）
.\backend\.venv\Scripts\python.exe scripts/gpu_autostart/tencent_start_gpu.py
```

命令行覆盖示例：

```powershell
.\backend\.venv\Scripts\python.exe scripts/gpu_autostart/tencent_start_gpu.py --region ap-hongkong --instance-ids ins-abc,ins-def
```

退出码：`0` 成功；`2` 参数缺失；`3` 未安装 SDK；其它异常为 `1`。

## 阿里云 ECS

| 变量 | 说明 |
|------|------|
| `GPU_AUTOSTART_ALIYUN_ACCESS_KEY_ID` | AccessKey ID（或标准名 `ALIBABA_CLOUD_ACCESS_KEY_ID`） |
| `GPU_AUTOSTART_ALIYUN_ACCESS_KEY_SECRET` | AccessKey Secret |
| `GPU_AUTOSTART_ALIYUN_REGION` | 地域，如 `cn-hongkong` |
| `GPU_AUTOSTART_ALIYUN_INSTANCE_IDS` | 实例 ID，多个逗号分隔 |

**建议 RAM 策略**：`ecs:StartInstances`（批量开机）；可选 `ecs:DescribeInstances`。资源限定到指定实例。

运行：

```powershell
.\backend\.venv\Scripts\python.exe scripts/gpu_autostart/aliyun_start_gpu.py --dry-run
.\backend\.venv\Scripts\python.exe scripts/gpu_autostart/aliyun_start_gpu.py
```

## 与业务集成

脚本 **不会** 自动关机等；开机后仍需等待系统与 Worker 就绪（常为数分钟）。若要从上传队列自动触发，需在应用内增加「调用脚本 / 云 API + 幂等 + 健康检查」逻辑（见项目部署文档简述）。

## 安全提示

- 勿将 AccessKey 提交到 Git；使用子账号 + 最小权限。
- 生产环境可改用 **STS 临时凭证**（需自行扩展脚本）。

## 与上传页集成（后端 API）

配置 `GPU_AUTOSTART_ENABLED=1` 及云厂商变量后，前端在 **GPU 确认弹窗打开时** 会请求 `POST /gpu/autostart/start`（与上传相同鉴权：`tenant.upload.write`）；用户点 **取消** 会请求 `POST /gpu/autostart/stop`。未启用时返回 503，前端静默忽略。

子账号需同时授权 **开机与关机**：腾讯云 `cvm:StopInstances`；阿里云 `ecs:StopInstances`。
