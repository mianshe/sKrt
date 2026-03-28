/** 浏览器在 CORS、混合内容、错误 API 地址等情况下 fetch 的典型失败形态 */
const FETCH_FAILED_HINT =
  "无法连接后端，请检查网络与 API 地址：生产构建需设置 VITE_API_BASE（推荐与站点同源 /api），并确认反代把 /api 转到 uvicorn；HTTPS 站点不可请求 http://localhost。";

export function formatApiFetchError(err: unknown, fallback: string): string {
  if (!(err instanceof Error)) return fallback;
  const m = err.message || "";
  if (
    err instanceof TypeError ||
    m === "Failed to fetch" ||
    m.includes("Failed to fetch") ||
    m.includes("NetworkError when attempting to fetch") ||
    /load failed/i.test(m)
  ) {
    return FETCH_FAILED_HINT;
  }
  return m || fallback;
}
