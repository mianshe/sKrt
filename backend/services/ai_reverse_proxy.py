from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from runtime_config import ReverseProxyConfig

_REQUEST_TIMEOUT_BUFFER_SECONDS = 5.0
_HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class AIReverseProxyService:
    def __init__(
        self,
        config: ReverseProxyConfig,
        *,
        client_factory: Optional[Callable[[httpx.Timeout], httpx.AsyncClient]] = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory or self._default_client_factory
        self._rate_limit_lock = asyncio.Lock()
        self._rate_limit_buckets: Dict[str, Deque[float]] = {}

    @staticmethod
    def _default_client_factory(timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, follow_redirects=False)

    def summary(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.config.enabled),
            "upstream_base_url": self.config.upstream_base_url,
            "upstream_api_key_configured": bool(self.config.upstream_api_key),
            "access_key_configured": bool(self.config.access_key),
            "allowed_path_prefixes": list(self.config.allowed_path_prefixes),
            "max_requests_per_minute": int(self.config.max_requests_per_minute),
            "forward_client_authorization": bool(self.config.forward_client_authorization),
            "send_identity_headers": bool(self.config.send_identity_headers),
        }

    def _normalize_path(self, upstream_path: str) -> str:
        raw = str(upstream_path or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="proxy path is required")
        if raw.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="absolute upstream URLs are not allowed")
        parts = []
        for part in raw.split("/"):
            segment = part.strip()
            if not segment or segment == ".":
                continue
            if segment == "..":
                raise HTTPException(status_code=400, detail="path traversal is not allowed")
            parts.append(segment)
        normalized = "/".join(parts)
        if not normalized:
            raise HTTPException(status_code=400, detail="proxy path is required")
        return normalized

    def _assert_proxy_enabled(self) -> None:
        if not self.config.enabled:
            raise HTTPException(status_code=503, detail="reverse proxy is disabled")
        if not self.config.upstream_base_url:
            raise HTTPException(status_code=503, detail="reverse proxy upstream is not configured")

    def _assert_allowed_path(self, normalized_path: str) -> None:
        allowed = [item.strip().strip("/") for item in self.config.allowed_path_prefixes if item.strip().strip("/")]
        if not allowed:
            return
        if any(
            normalized_path == prefix or normalized_path.startswith(f"{prefix}/")
            for prefix in allowed
        ):
            return
        raise HTTPException(status_code=403, detail=f"path not allowed: {normalized_path}")

    def _assert_access_key(self, request: Request) -> None:
        expected = self.config.access_key
        if not expected:
            return
        supplied = str(request.headers.get("X-Proxy-Key", "")).strip()
        if supplied != expected:
            raise HTTPException(status_code=401, detail="invalid proxy access key")

    async def _check_rate_limit(self, identity: Dict[str, Any], request: Request) -> None:
        max_requests = max(1, int(self.config.max_requests_per_minute))
        now = time.time()
        tenant_id = str(identity.get("tenant_id") or "").strip()
        user_id = str(identity.get("user_id") or "").strip()
        auth_key = str(request.headers.get("X-Proxy-Key", "")).strip()
        client_host = request.client.host if request.client else "unknown"
        bucket_key = tenant_id or user_id or auth_key or client_host
        async with self._rate_limit_lock:
            bucket = self._rate_limit_buckets.setdefault(bucket_key, deque())
            while bucket and now - bucket[0] >= 60.0:
                bucket.popleft()
            if len(bucket) >= max_requests:
                raise HTTPException(
                    status_code=429,
                    detail=f"rate limit exceeded: {max_requests} requests/minute",
                )
            bucket.append(now)

    def _build_upstream_headers(
        self,
        request: Request,
        identity: Dict[str, Any],
        request_id: str,
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        for key, value in request.headers.items():
            lowered = key.lower()
            if lowered in _HOP_BY_HOP_HEADERS:
                continue
            if lowered == "authorization" and not self.config.forward_client_authorization:
                continue
            if lowered == "x-proxy-key":
                continue
            headers[key] = value

        if self.config.upstream_api_key:
            upstream_value = self.config.upstream_api_key
            if self.config.upstream_auth_scheme:
                upstream_value = f"{self.config.upstream_auth_scheme} {upstream_value}"
            headers[self.config.upstream_auth_header] = upstream_value

        headers["X-Forwarded-Host"] = request.headers.get("host", "")
        headers["X-Forwarded-Proto"] = request.url.scheme
        if request.client and request.client.host:
            headers["X-Forwarded-For"] = request.client.host
        headers["X-Proxy-Request-Id"] = request_id

        if self.config.send_identity_headers:
            tenant_id = str(identity.get("tenant_id") or "").strip()
            user_id = str(identity.get("user_id") or "").strip()
            auth_source = str(identity.get("auth_source") or "").strip()
            if tenant_id:
                headers["X-Proxy-Tenant-Id"] = tenant_id
            if user_id:
                headers["X-Proxy-User-Id"] = user_id
            if auth_source:
                headers["X-Proxy-Auth-Source"] = auth_source
        return headers

    def _build_upstream_url(self, normalized_path: str, query_string: str) -> str:
        base = self.config.upstream_base_url.rstrip("/")
        url = f"{base}/{normalized_path}"
        if query_string:
            return f"{url}?{query_string}"
        return url

    async def _close_stream(self, upstream_response: httpx.Response, client: httpx.AsyncClient) -> None:
        try:
            await upstream_response.aclose()
        finally:
            await client.aclose()

    async def handle(
        self,
        request: Request,
        upstream_path: str,
        identity: Dict[str, Any],
        request_id: str,
    ):
        self._assert_proxy_enabled()
        self._assert_access_key(request)
        normalized_path = self._normalize_path(upstream_path)
        self._assert_allowed_path(normalized_path)
        await self._check_rate_limit(identity, request)

        query_string = str(request.url.query or "")
        upstream_url = self._build_upstream_url(normalized_path, query_string)
        body = await request.body()
        headers = self._build_upstream_headers(request, identity, request_id)
        timeout = httpx.Timeout(
            connect=self.config.connect_timeout_seconds,
            read=self.config.timeout_seconds,
            write=self.config.timeout_seconds,
            pool=self.config.connect_timeout_seconds + _REQUEST_TIMEOUT_BUFFER_SECONDS,
        )
        client = self._client_factory(timeout)
        try:
            upstream_request = client.build_request(
                method=request.method,
                url=upstream_url,
                headers=headers,
                content=body,
            )
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.TimeoutException as exc:
            await client.aclose()
            return JSONResponse(
                status_code=504,
                content={
                    "detail": "upstream timeout",
                    "request_id": request_id,
                    "upstream_url": upstream_url,
                },
            )
        except httpx.HTTPError as exc:
            await client.aclose()
            return JSONResponse(
                status_code=502,
                content={
                    "detail": f"upstream request failed: {exc}",
                    "request_id": request_id,
                    "upstream_url": upstream_url,
                },
            )

        response_headers = {
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower() not in _HOP_BY_HOP_HEADERS and key.lower() != "set-cookie"
        }
        response_headers["X-Proxy-Upstream-Path"] = normalized_path
        response_headers["X-Proxy-Request-Id"] = request_id
        if response_headers.get("content-type", "").startswith("text/event-stream"):
            response_headers["Cache-Control"] = "no-cache"
            response_headers["X-Accel-Buffering"] = "no"

        return StreamingResponse(
            upstream_response.aiter_raw(),
            status_code=upstream_response.status_code,
            headers=response_headers,
            background=BackgroundTask(self._close_stream, upstream_response, client),
        )
