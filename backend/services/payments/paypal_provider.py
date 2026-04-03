from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any, ClassVar, Dict, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from .base import PaymentCreateResult, PaymentNotifyResult, PaymentProvider, PaymentSyncResult

logger = logging.getLogger(__name__)


class PayPalProvider(PaymentProvider):
    _token_cache: ClassVar[Dict[str, tuple[str, float]]] = {}

    def __init__(self) -> None:
        mode = (os.getenv("PAYPAL_MODE") or "live").strip().lower()
        default_api_base = "https://api-m.sandbox.paypal.com" if mode == "sandbox" else "https://api-m.paypal.com"
        self.api_base = (os.getenv("PAYPAL_API_BASE") or default_api_base).strip().rstrip("/")
        self.client_id = (os.getenv("PAYPAL_CLIENT_ID") or "").strip()
        self.client_secret = (os.getenv("PAYPAL_CLIENT_SECRET") or "").strip()
        self.currency = (os.getenv("PAYPAL_CURRENCY") or "CNY").strip().upper() or "CNY"
        self.brand_name = (os.getenv("PAYPAL_BRAND_NAME") or "sKrt").strip() or "sKrt"
        self.return_url = (os.getenv("PAY_RETURN_URL") or os.getenv("PAYPAL_RETURN_URL") or "").strip()
        self.cancel_url = (os.getenv("PAYPAL_CANCEL_URL") or self.return_url).strip()

    def _ensure_enabled(self) -> None:
        missing = [
            name
            for name, value in {
                "PAYPAL_CLIENT_ID": self.client_id,
                "PAYPAL_CLIENT_SECRET": self.client_secret,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"missing PayPal env: {'/'.join(missing)}")

    def _token_cache_key(self) -> str:
        return f"{self.api_base}|{self.client_id}"

    def _clear_cached_token(self) -> None:
        self._token_cache.pop(self._token_cache_key(), None)

    def _parse_http_error(self, exc: HTTPError) -> Dict[str, Any]:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            parsed = {"message": (raw or "").strip()[:500]}
        if not isinstance(parsed, dict):
            parsed = {"message": str(parsed)[:500]}
        return parsed

    def _access_token(self) -> str:
        self._ensure_enabled()
        cache_key = self._token_cache_key()
        cached = self._token_cache.get(cache_key)
        now = time.time()
        if cached and cached[1] > now + 30:
            return cached[0]

        creds = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        auth = base64.b64encode(creds).decode("ascii")
        body = urlencode({"grant_type": "client_credentials"}).encode("utf-8")
        req = UrlRequest(url=f"{self.api_base}/v1/oauth2/token", data=body, method="POST")
        req.add_header("Authorization", f"Basic {auth}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=20) as resp:  # nosec B310
                raw = resp.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            self._clear_cached_token()
            parsed = self._parse_http_error(exc)
            error_code = str(parsed.get("error") or parsed.get("name") or "").strip()
            description = str(parsed.get("error_description") or parsed.get("message") or "").strip()
            if exc.code == 401 or error_code in {"invalid_client", "invalid_token"}:
                hint = "请检查 PAYPAL_MODE、PAYPAL_API_BASE、PAYPAL_CLIENT_ID、PAYPAL_CLIENT_SECRET 是否匹配同一套环境"
                raise RuntimeError(f"PayPal 鉴权失败: {description or error_code or '401 Unauthorized'}；{hint}") from exc
            raise RuntimeError(f"paypal_oauth_http_{exc.code}:{description or error_code or 'oauth_failed'}") from exc
        try:
            parsed = json.loads(raw or "{}")
        except Exception as exc:
            raise RuntimeError(f"paypal_oauth_invalid_json: {(raw or '').strip()[:300]}") from exc
        token = str(parsed.get("access_token") or "").strip()
        expires_in = int(parsed.get("expires_in") or 0)
        if not token:
            raise RuntimeError(str(parsed.get("error_description") or parsed.get("error") or "paypal_oauth_failed"))
        self._token_cache[cache_key] = (token, now + max(60, expires_in))
        return token

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        last_error: Optional[Exception] = None
        for attempt in range(2):
            token = self._access_token()
            req_headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                **(headers or {}),
            }
            if data is not None:
                req_headers["Content-Type"] = "application/json"
            req = UrlRequest(url=f"{self.api_base}/{path.lstrip('/')}", data=data, method=method.upper())
            for key, value in req_headers.items():
                req.add_header(key, value)
            try:
                with urlopen(req, timeout=25) as resp:  # nosec B310
                    raw = resp.read().decode("utf-8", errors="ignore")
                break
            except HTTPError as exc:
                parsed = self._parse_http_error(exc)
                message = (
                    parsed.get("message")
                    or parsed.get("error_description")
                    or parsed.get("error")
                    or parsed.get("name")
                    or f"http_{exc.code}"
                )
                if exc.code == 401 and attempt == 0:
                    logger.warning("paypal api got 401, clearing cached token and retrying path=%s", path)
                    self._clear_cached_token()
                    last_error = RuntimeError(f"paypal_api_error:{message}")
                    continue
                raise RuntimeError(f"paypal_api_error:{message}") from exc
        else:
            if last_error is not None:
                raise last_error
            raise RuntimeError("paypal_api_error:request_failed")
        try:
            parsed = json.loads(raw or "{}")
        except Exception as exc:
            raise RuntimeError(f"paypal_invalid_json: {(raw or '').strip()[:300]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("paypal_invalid_json")
        return parsed

    def _extract_approval_url(self, payload: Dict[str, Any]) -> str:
        for item in payload.get("links") or []:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("rel") or "").strip().lower()
            href = str(item.get("href") or "").strip()
            if rel in {"approve", "payer-action"} and href:
                return href
        return ""

    def _extract_capture_id(self, payload: Dict[str, Any]) -> str:
        purchase_units = payload.get("purchase_units") or []
        if isinstance(purchase_units, list):
            for unit in purchase_units:
                if not isinstance(unit, dict):
                    continue
                payments = unit.get("payments") or {}
                captures = payments.get("captures") or []
                if isinstance(captures, list):
                    for capture in captures:
                        if isinstance(capture, dict):
                            capture_id = str(capture.get("id") or "").strip()
                            if capture_id:
                                return capture_id
        return ""

    def create_order(
        self,
        *,
        order_no: str,
        amount_fen: int,
        channel: str,
        subject: str,
        notify_url: str,
    ) -> PaymentCreateResult:
        self._ensure_enabled()
        if channel != "paypal":
            raise RuntimeError(f"unsupported_paypal_channel:{channel}")

        purchase_unit: Dict[str, Any] = {
            "reference_id": order_no,
            "custom_id": order_no,
            "invoice_id": order_no,
            "description": subject[:127],
            "amount": {
                "currency_code": self.currency,
                "value": f"{max(1, int(amount_fen)) / 100:.2f}",
            },
        }
        payload: Dict[str, Any] = {
            "intent": "CAPTURE",
            "purchase_units": [purchase_unit],
        }
        if self.return_url or self.cancel_url:
            payload["application_context"] = {
                "brand_name": self.brand_name,
                "landing_page": "LOGIN",
                "user_action": "PAY_NOW",
                "return_url": self.return_url or self.cancel_url,
                "cancel_url": self.cancel_url or self.return_url,
            }

        logger.info("paypal create order order_no=%s amount_fen=%s currency=%s", order_no, amount_fen, self.currency)
        rsp = self._request_json(
            method="POST",
            path="/v2/checkout/orders",
            payload=payload,
            headers={"PayPal-Request-Id": order_no},
        )
        provider_order_id = str(rsp.get("id") or "").strip()
        payment_url = self._extract_approval_url(rsp)
        if not provider_order_id or not payment_url:
            raise RuntimeError(str(rsp.get("message") or "paypal_create_failed"))
        return PaymentCreateResult(
            provider_order_id=provider_order_id,
            code_url=payment_url,
            payment_url=payment_url,
            raw=rsp,
        )

    def verify_notify(self, payload: Dict[str, Any]) -> PaymentNotifyResult:
        raise RuntimeError("paypal_webhook_not_configured")

    def sync_order_status(self, *, order_no: str, provider_order_id: str = "") -> Optional[PaymentSyncResult]:
        if not provider_order_id:
            return None

        order_data = self._request_json(method="GET", path=f"/v2/checkout/orders/{provider_order_id}")
        status = str(order_data.get("status") or "").strip().upper()
        if status == "COMPLETED":
            capture_id = self._extract_capture_id(order_data)
            return PaymentSyncResult(
                status="paid",
                paid=True,
                transaction_id=capture_id,
                provider_order_id=provider_order_id,
                raw=order_data,
            )

        if status == "APPROVED":
            capture_data = self._request_json(
                method="POST",
                path=f"/v2/checkout/orders/{provider_order_id}/capture",
                payload={},
                headers={"PayPal-Request-Id": f"{order_no}-capture"},
            )
            capture_status = str(capture_data.get("status") or "").strip().upper()
            capture_id = self._extract_capture_id(capture_data)
            return PaymentSyncResult(
                status="paid" if capture_status == "COMPLETED" else "pending",
                paid=capture_status == "COMPLETED",
                transaction_id=capture_id,
                provider_order_id=provider_order_id,
                raw=capture_data,
            )

        pending_status = status.lower() or "pending"
        return PaymentSyncResult(
            status="pending" if pending_status in {"created", "saved", "payer_action_required"} else pending_status,
            paid=False,
            transaction_id="",
            provider_order_id=provider_order_id,
            raw=order_data,
        )

    def refund(self, *, order_no: str, provider_order_id: str = "") -> Dict[str, Any]:
        logger.info("paypal refund fallback to local settlement order_no=%s provider_order_id=%s", order_no, provider_order_id)
        return {"ok": True, "order_no": order_no, "provider_order_id": provider_order_id, "noop": True}
