#!/usr/bin/env bash
# 在云主机项目根执行，用于确认「Git 已更新 + 前端已构建」与关键改动是否在磁盘上。
# 用法：bash scripts/verify-frontend-deploy.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== Git 最新提交 =="
git log -1 --oneline || true

echo ""
echo "== GpuQuotaWidget 是否含 quotaLoadError（源码）==="
if grep -q "quotaLoadError" frontend/src/components/GpuQuotaWidget.tsx 2>/dev/null; then
  echo "OK: 已找到 quotaLoadError"
else
  echo "FAIL: 未找到，请 git pull 并确认路径为 $ROOT"
  exit 1
fi

echo ""
echo "== dist 内最新 JS（按时间）==="
ls -lt frontend/dist/assets/*.js 2>/dev/null | head -5 || echo "(无 dist，请在 frontend 目录执行 npm run build)"

echo ""
echo "== dist/index.html 引用的入口 JS（前 800 字节）==="
if [[ -f frontend/dist/index.html ]]; then
  head -c 800 frontend/dist/index.html
  echo ""
else
  echo "(无 frontend/dist/index.html)"
fi

echo ""
echo "== 提示 =="
echo "请在浏览器 DevTools → Network 中查看实际加载的 /assets/index-*.js 是否与上面 index.html 中一致。"
echo "若不一致：Application → Service Workers → Unregister，并清除站点数据后重试。"
