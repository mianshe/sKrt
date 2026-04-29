declare const __API_BASE__: string | undefined;

function normalizeApiBase(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "/api";
  if (trimmed === "/") return "/api";
  return trimmed.endsWith("/") ? trimmed.slice(0, -1) : trimmed;
}

const configuredBase =
  (typeof __API_BASE__ === "string" && __API_BASE__.trim()) ||
  (import.meta.env.VITE_API_BASE?.trim() || "") ||
  "/api";

const normalizedBase = normalizeApiBase(configuredBase);

function resolveRuntimeApiBase(base: string): string {
  if (typeof window === "undefined") return base;
  const protocol = window.location.protocol;
  const hostname = window.location.hostname;
  const port = window.location.port;
  const isRelativeBase = base.startsWith("/");
  const isHttps = window.location.protocol === "https:";
  const isLocalHttpBase = /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/i.test(base);
  // Avoid mixed-content failures in HTTPS pages.
  if (isHttps && isLocalHttpBase) return "/api";
  // If opened as file:// (or local non-Vite preview) relative /api cannot hit Vite proxy.
  // Fallback to local backend directly for local debugging.
  if (isRelativeBase && protocol === "file:") return "http://127.0.0.1:8000";
  if (isRelativeBase && protocol === "http:" && (hostname === "localhost" || hostname === "127.0.0.1")) {
    const runningViaVite = port === "5173" || port === "4173";
    if (!runningViaVite) return "http://127.0.0.1:8000";
  }
  return base;
}

export const API_BASE: string = resolveRuntimeApiBase(normalizedBase);

