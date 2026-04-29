"""客户端 IP 解析（受信反代）。"""
import os

import pytest
from starlette.requests import Request

from .client_ip import client_ip_from_request, normalized_signup_ip


class _DummyClient:
    def __init__(self, host: str) -> None:
        self.host = host


def _make_request(*, client_host: str, headers: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (client_host, 12345),
        "server": ("test", 80),
    }
    return Request(scope)


def test_direct_ip_without_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    req = _make_request(client_host="203.0.113.5")
    assert client_ip_from_request(req) == "203.0.113.5"


def test_x_forwarded_for_when_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    req = _make_request(client_host="10.0.0.1", headers={"X-Forwarded-For": "198.51.100.2, 10.0.0.1"})
    assert client_ip_from_request(req) == "198.51.100.2"


def test_normalized_signup_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    req = _make_request(client_host="10.0.0.1", headers={"X-Forwarded-For": "2001:db8::1"})
    assert "2001" in normalized_signup_ip(req)
