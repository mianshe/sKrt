from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
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

    def _resolve_url(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        lowered = text.lower()
        if lowered.startswith(("http://", "https://", "wxp://", "weixin://", "alipay://")):
            return text
        return urljoin(f"{self.api_base}/", text.lstrip("/"))

    def _extract_script_redirect_response(self, raw_text: str) -> Dict[str, Any] | None:
        text = (raw_text or "").strip()
        if not text:
            return None
        match = re.search(r"window\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", text, flags=re.IGNORECASE)
        if not match:
            return None
        redirect_url = urljoin(f"{self.api_base}/", match.group(1).strip())
        parsed = urlparse(redirect_url)
        query = parse_qs(parsed.query)
        trade_no = ""
        for key in ("trade_no", "order_no", "out_trade_no"):
            values = query.get(key)
            if values and values[0]:
                trade_no = values[0]
                break
        logger.info("easypay returned script redirect payment_url=%s trade_no=%s", redirect_url, trade_no)
        return {
            "code": 1,
            "msg": "success",
            "trade_no": trade_no,
            "payurl": redirect_url,
            "payment_url": redirect_url,
            "response_mode": "script_redirect",
            "raw": text[:1000],
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
            redirect_rsp = self._extract_script_redirect_response(text)
            if redirect_rsp is not None:
                return redirect_rsp
            logger.warning("easypay returned non-json response: %s", text[:300])
            return {"code": -1, "msg": "invalid_json", "raw": text[:1000]}

    def _api_response_ok(self, rsp: Dict[str, Any]) -> bool:
        code = str(rsp.get("code") or "").strip()
        msg = str(rsp.get("msg") or "").strip().lower()
        if code in {"200", "1"}:
            return True
        return "成功" in str(rsp.get("msg") or "") or msg in {"success", "ok"}

    def _create_order_via_mapi(
        self,
        *,
        order_no: str,
        amount_yuan: str,
        pay_type: str,
        subject: str,
        notify_url: str,
        return_url: str,
    ) -> PaymentCreateResult:
        form: Dict[str, Any] = {
            "pid": self.pid,
            "type": pay_type,
            "out_trade_no": order_no,
            "notify_url": notify_url,
            "return_url": return_url,
            "name": subject,
            "money": amount_yuan,
            "format": "json",
        }
        form["sign"] = self._sign(form)
        form["sign_type"] = "MD5"
        logger.info(
            "easypay create order try mapi order_no=%s api_base=%s pid=%s type=%s amount=%s notify_url=%s return_url=%s",
            order_no,
            self.api_base,
            self._masked_pid(),
            pay_type,
            amount_yuan,
            notify_url,
            return_url,
        )
        rsp = self._post_form("mapi.php", form)
        if not isinstance(rsp, dict):
            rsp = {"code": -1, "msg": "invalid_json", "raw": str(rsp)[:1000]}
        trade_no = str(rsp.get("trade_no") or rsp.get("order_no") or rsp.get("out_trade_no") or "")
        qr_image_url = self._resolve_url(rsp.get("code_url") or "")
        qrcode = self._resolve_url(rsp.get("qrcode") or "")
        status_ok = self._api_response_ok(rsp)
        if not status_ok or (not qrcode and not qr_image_url):
            raw_preview = str(rsp.get("raw") or "").strip().replace("\r", " ").replace("\n", " ")
            logger.warning(
                "easypay mapi create order failed order_no=%s code=%s msg=%s trade_no=%s raw=%s",
                order_no,
                str(rsp.get("code") or ""),
                str(rsp.get("msg") or rsp.get("error") or ""),
                trade_no,
                raw_preview[:300],
            )
            raise RuntimeError(str(rsp.get("msg") or rsp.get("error") or "easypay_mapi_create_failed"))
        logger.info(
            "easypay mapi create order ok order_no=%s trade_no=%s qrcode_present=%s qr_image_present=%s",
            order_no,
            trade_no,
            bool(qrcode),
            bool(qr_image_url),
        )
        return PaymentCreateResult(
            provider_order_id=trade_no,
            code_url=qrcode,
            qr_image_url=qr_image_url,
            raw=rsp,
        )

    def _create_order_via_submit(
        self,
        *,
        order_no: str,
        amount_yuan: str,
        pay_type: str,
        subject: str,
        notify_url: str,
        return_url: str,
    ) -> PaymentCreateResult:
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
            "easypay create order fallback submit order_no=%s api_base=%s pid=%s type=%s amount=%s notify_url=%s return_url=%s",
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
        payment_url = self._resolve_url(rsp.get("payment_url") or rsp.get("payurl") or "")
        code_url = self._resolve_url(rsp.get("code_url") or rsp.get("qrcode") or payment_url or "")
        status_ok = str(rsp.get("code") or "") == "1" or str(rsp.get("msg") or "").lower() in {"success", "ok"}
        if not status_ok or not code_url:
            msg = str(rsp.get("msg") or rsp.get("error") or "easypay_create_failed")
            raw_preview = str(rsp.get("raw") or "").strip().replace("\r", " ").replace("\n", " ")
            if msg == "invalid_json" and raw_preview:
                msg = f"invalid_json: {raw_preview[:160]}"
            logger.warning(
                "easypay submit create order failed order_no=%s code=%s msg=%s trade_no=%s raw=%s",
                order_no,
                str(rsp.get("code") or ""),
                msg,
                trade_no,
                raw_preview[:300],
            )
            raise RuntimeError(msg)
        logger.info(
            "easypay submit create order ok order_no=%s trade_no=%s code_url_present=%s payment_url_present=%s",
            order_no,
            trade_no,
            bool(code_url),
            bool(payment_url),
        )
        return PaymentCreateResult(provider_order_id=trade_no, code_url=code_url, payment_url=payment_url, raw=rsp)

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
        try:
            return self._create_order_via_mapi(
                order_no=order_no,
                amount_yuan=amount_yuan,
                pay_type=pay_type,
                subject=subject,
                notify_url=notify_url,
                return_url=return_url,
            )
        except Exception as exc:
            logger.warning("easypay mapi create order fallback to submit order_no=%s reason=%s", order_no, exc)
        return self._create_order_via_submit(
            order_no=order_no,
            amount_yuan=amount_yuan,
            pay_type=pay_type,
            subject=subject,
            notify_url=notify_url,
            return_url=return_url,
        )

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
