from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Dict, List
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from .base import PaymentCreateResult, PaymentNotifyResult, PaymentProvider


class EasyPayProvider(PaymentProvider):
    def __init__(self) -> None:
        self.api_base = (os.getenv("EASYPAY_API_BASE") or "").strip().rstrip("/")
        self.pid = (os.getenv("EASYPAY_PID") or "").strip()
        self.key = (os.getenv("EASYPAY_KEY") or "").strip()

    def _ensure_enabled(self) -> None:
        if not self.api_base or not self.pid or not self.key:
            raise RuntimeError("未配置 EASYPAY_API_BASE/EASYPAY_PID/EASYPAY_KEY")

    def _sign(self, payload: Dict[str, Any]) -> str:
        items: List[str] = []
        for k in sorted(payload.keys()):
            if k == "sign":
                continue
            v = payload.get(k)
            if v is None:
                continue
            sv = str(v).strip()
            if not sv:
                continue
            items.append(f"{k}={sv}")
        raw = "&".join(items) + self.key
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _post_form(self, endpoint: str, form: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        body = urlencode({k: str(v) for k, v in form.items() if v is not None}).encode("utf-8")
        req = UrlRequest(url=url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urlopen(req, timeout=15) as resp:  # nosec B310
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {"code": -1, "msg": "invalid_json", "raw": raw}

    def create_order(self, *, order_no: str, amount_fen: int, channel: str, subject: str, notify_url: str) -> PaymentCreateResult:
        self._ensure_enabled()
        amount_yuan = f"{max(1, int(amount_fen)) / 100:.2f}"
        pay_type = "wxpay" if channel == "wechat_native" else "alipay"
        form: Dict[str, Any] = {
            "pid": self.pid,
            "type": pay_type,
            "out_trade_no": order_no,
            "notify_url": notify_url,
            "name": subject,
            "money": amount_yuan,
            "clientip": "127.0.0.1",
            "device": "pc",
        }
        form["sign"] = self._sign(form)
        form["sign_type"] = "MD5"
        rsp = self._post_form("submit.php", form)
        trade_no = str(rsp.get("trade_no") or rsp.get("order_no") or "")
        code_url = str(rsp.get("code_url") or rsp.get("qrcode") or rsp.get("payurl") or "")
        status_ok = str(rsp.get("code") or "") == "1" or str(rsp.get("msg") or "").lower() in {"success", "ok"}
        if not status_ok or not code_url:
            msg = str(rsp.get("msg") or rsp.get("error") or "易支付下单失败")
            raise RuntimeError(msg)
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
        return PaymentNotifyResult(
            order_no=order_no,
            paid=paid,
            transaction_id=str(payload.get("trade_no") or payload.get("transaction_id") or ""),
            provider_order_id=str(payload.get("trade_no") or ""),
            raw=payload,
        )

    def refund(self, *, order_no: str, provider_order_id: str = "") -> Dict[str, Any]:
        self._ensure_enabled()
        # 许多易支付实现无统一退款接口，这里返回 no-op，由业务层做本地额度回退与人工退款。
        return {"ok": True, "order_no": order_no, "provider_order_id": provider_order_id, "noop": True}

