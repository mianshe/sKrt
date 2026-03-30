"""从请求解析客户端 IP（支持受信反代 X-Forwarded-For）。"""
from __future__ import annotations

import os
from typing import Optional

from starlette.requests import Request


def client_ip_from_request(request: Request) -> str:
    """
    优先使用直连地址；若 TRUST_PROXY_HEADERS=1，则使用 X-Forwarded-For 最左侧（原始客户端）。
    仅在部署于受信反代后开启 TRUST_PROXY_HEADERS，否则客户端可伪造 X-Forwarded-For。
    """
    trust = (os.getenv("TRUST_PROXY_HEADERS", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
    if trust:
        xff = (request.headers.get("x-forwarded-for") or "").strip()
        if xff:
            # "client, proxy1, proxy2" — 取第一个非空段
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                return parts[0][:128]
    try:
        host = request.client.host if request.client else ""
    except Exception:
        host = ""
    return (host or "unknown")[:128]


def normalized_signup_ip(request: Request) -> str:
    """与存储用 IP 键一致：仅保留安全字符。"""
    import re

    raw = client_ip_from_request(request)
    return re.sub(r"[^0-9A-Za-z\.\:]", "-", raw).strip("-")[:128] or "unknown"
