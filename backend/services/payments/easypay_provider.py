from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, List
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from .base import PaymentCreateResult, PaymentNotifyResult, PaymentProvider

logger = logging.getLogger(__name__)


class EasyPayProvider(PaymentProvider):
    def __init__(self) -> None:
        self.api_base = (os.getenv("EASYPAY_API_BASE") or "").strip().rstrip("/")
        self.pid = (os.getenv("EASYPAY_PID") or "").strip()
        self.key = (os.getenv("EASYPAY_KEY") or "").strip()

    def _ensure_enabled(self) -> None:
        if not self.api_base or not self.pid or not self.key:
            raise RuntimeError("未配置 EASYPAY_API_BASE/EASYPAY_PID/EASYPAY_KEY")

    def _masked_pid(self) -> str:
        if len(self.pid) <= 4:
            return self.pid
        return f"{self.pid[:2]}***{self.pid[-2:]}"

    def _sign(self, payload: Dict[str, Any]) -> str:
        items: List[str] = []
        for key in sorted(payload.keys()):
            if key == "sign":
                continue
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            items.append(f"{key}={text}")
        raw = "&".join(items) + self.key
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _normalize_response(self, parsed: Any, raw_text: str) -> Dict[str, Any]:
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            text = parsed.strip()
            return {"code": -1, "msg": "invalid_json", "raw": text[:1000], "parsed_type": "string"}
        return {
            "code": -1,
            "msg": "invalid_json",
            "raw": (raw_text or "").strip()[:1000],
            "parsed_type": type(parsed).__name__,
        }

    def _post_form(self, endpoint: str, form: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        body = urlencode({k: str(v) for k, v in form.items() if v is not None}).encode("utf-8")
        req = UrlRequest(url=url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json,text/plain,*/*")
        with urlopen(req, timeout=15) as resp:  # nosec B310
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            return self._normalize_response(json.loads(raw or "{}"), raw)
        except Exception:
            text = (raw or "").strip()
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end > start:
                try:
                    return self._normalize_response(json.loads(text[start : end + 1]), text)
                except Exception:
                    pass
            logger.warning("easypay returned non-json response: %s", text[:300])
            return {"code": -1, "msg": "invalid_json", "raw": text[:1000]}

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
        amount_yuan = f"{max(1, int(amount_fen)) / 100:.2f}"
        pay_type = "wxpay" if channel == "wechat_native" else "alipay"
        return_url = (os.getenv("PAY_RETURN_URL") or os.getenv("EASYPAY_RETURN_URL") or "").strip()
        form: Dict[str, Any] = {
            "pid": self.pid,
            "type": pay_type,
            "out_trade_no": order_no,
            "notify_url": notify_url,
            "name": subject,
            "money": amount_yuan,
            "format": "json",
            "clientip": "127.0.0.1",
            "device": "pc",
        }
        if return_url:
            form["return_url"] = return_url
        form["sign"] = self._sign(form)
        form["sign_type"] = "MD5"
        logger.info(
            "easypay create order start order_no=%s api_base=%s pid=%s type=%s amount=%s notify_url=%s return_url=%s",
            order_no,
            self.api_base,
            self._masked_pid(),
            pay_type,
            amount_yuan,
            notify_url,
            return_url,
        )
        rsp = self._post_form("submit.php", form)
        if not isinstance(rsp, dict):
            logger.warning("easypay create order normalized non-dict response order_no=%s type=%s", order_no, type(rsp).__name__)
            rsp = {"code": -1, "msg": "invalid_json", "raw": str(rsp)[:1000]}
        trade_no = str(rsp.get("trade_no") or rsp.get("order_no") or "")
        code_url = str(rsp.get("code_url") or rsp.get("qrcode") or rsp.get("payurl") or "")
        status_ok = str(rsp.get("code") or "") == "1" or str(rsp.get("msg") or "").lower() in {"success", "ok"}
        if not status_ok or not code_url:
            msg = str(rsp.get("msg") or rsp.get("error") or "easypay_create_failed")
            raw_preview = str(rsp.get("raw") or "").strip().replace("\r", " ").replace("\n", " ")
            if msg == "invalid_json" and raw_preview:
                msg = f"invalid_json: {raw_preview[:160]}"
            logger.warning(
                "easypay create order failed order_no=%s code=%s msg=%s trade_no=%s raw=%s",
                order_no,
                str(rsp.get("code") or ""),
                msg,
                trade_no,
                raw_preview[:300],
            )
            raise RuntimeError(msg)
        logger.info(
            "easypay create order ok order_no=%s trade_no=%s code_url_present=%s",
            order_no,
            trade_no,
            bool(code_url),
        )
        return PaymentCreateResult(provider_order_id=trade_no, code_url=code_url, raw=rsp)

    def verify_notify(self, payload: Dict[str, Any]) -> PaymentNotifyResult:
        self._ensure_enabled()
        order_no = str(payload.get("out_trade_no") or "")
        sign = str(payload.get("sign") or "")
        local_sign = self._sign({k: v for k, v in payload.items() if k not in {"sign", "sign_type"}})
        if not order_no or not sign or not hmac.compare_digest(local_sign.lower(), sign.lower()):
            raise RuntimeError("invalid_sign")
        paid_status = str(payload.get("trade_status") or payload.get("status") or "").upper()
        paid = paid_status in {"TRADE_SUCCESS", "SUCCESS", "1"}
        logger.info(
            "easypay notify verified order_no=%s paid=%s trade_status=%s provider_order_id=%s",
            order_no,
            paid,
            paid_status,
            str(payload.get("trade_no") or ""),
        )
        return PaymentNotifyResult(
            order_no=order_no,
            paid=paid,
            transaction_id=str(payload.get("trade_no") or payload.get("transaction_id") or ""),
            provider_order_id=str(payload.get("trade_no") or ""),
            raw=payload,
        )

    def refund(self, *, order_no: str, provider_order_id: str = "") -> Dict[str, Any]:
        self._ensure_enabled()
        # Many EasyPay deployments do not expose a unified refund API.
        # Keep local balance settlement in the business layer.
        return {"ok": True, "order_no": order_no, "provider_order_id": provider_order_id, "noop": True}
