import { API_BASE } from "../config/apiBase";

function buildFetchFailedHint(): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "unknown-origin";
  return (
    "无法连接后端，请检查网络与 API 地址：生产构建需设置 VITE_API_BASE（推荐与站点同源 /api），" +
    "并确认反代把 /api 转到 uvicorn；HTTPS 站点不可请求 http://localhost。" +
    ` 当前 origin=${origin}，API_BASE=${API_BASE}。` +
    " 本地调试修改 .env.local 后需重启 Vite，并强刷或清理旧缓存。"
  );
}

export function formatApiFetchError(err: unknown, fallback: string): string {
  if (!(err instanceof Error)) return fallback;
  const message = err.message || "";
  if (
    err instanceof TypeError ||
    message === "Failed to fetch" ||
    message.includes("Failed to fetch") ||
    message.includes("NetworkError when attempting to fetch") ||
    /load failed/i.test(message)
  ) {
    return buildFetchFailedHint();
  }
  return message || fallback;
}
