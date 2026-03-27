export const config = {
  runtime: "nodejs",
};

function joinUrl(base: string, path: string): string {
  const b = (base || "").replace(/\/+$/, "");
  const p = (path || "").replace(/^\/+/, "");
  return `${b}/${p}`;
}

export default async function handler(req: any, res: any) {
  const backendBase = process.env.BACKEND_URL || process.env.VITE_API_BASE || "";
  if (!backendBase) {
    res.statusCode = 500;
    res.setHeader("content-type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ detail: "BACKEND_URL 未配置" }));
    return;
  }

  const segments = Array.isArray(req.query?.path) ? req.query.path : [req.query?.path].filter(Boolean);
  const upstreamPath = "/" + segments.join("/");
  const upstream = joinUrl(backendBase, upstreamPath);

  const url = new URL(upstream);
  for (const [k, v] of Object.entries(req.query || {})) {
    if (k === "path") continue;
    if (Array.isArray(v)) v.forEach((vv) => url.searchParams.append(k, String(vv)));
    else if (v != null) url.searchParams.set(k, String(v));
  }

  const headers: Record<string, string> = {};
  for (const [k, v] of Object.entries(req.headers || {})) {
    if (typeof v === "string") headers[k] = v;
  }
  delete headers.host;
  delete headers.connection;
  delete headers["content-length"];

  const body = req.method && !["GET", "HEAD"].includes(req.method.toUpperCase()) ? req : undefined;

  const upstreamResp = await fetch(url.toString(), {
    method: req.method,
    headers,
    body,
    redirect: "manual",
  } as any);

  res.statusCode = upstreamResp.status;
  upstreamResp.headers.forEach((value, key) => {
    if (key.toLowerCase() === "transfer-encoding") return;
    res.setHeader(key, value);
  });
  const buf = Buffer.from(await upstreamResp.arrayBuffer());
  res.end(buf);
}

