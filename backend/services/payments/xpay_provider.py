from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from typing import Any, Dict
from urllib.parse import quote, urlencode
from urllib.request import Request as UrlRequest, urlopen

from .base import PaymentCreateResult, PaymentNotifyResult, PaymentProvider, PaymentSyncResult

logger = logging.getLogger(__name__)


class XPayProvider(PaymentProvider):
    def __init__(self) -> None:
        self.api_base = (os.getenv("XPAY_API_BASE") or "").strip().rstrip("/")
        self.notify_email = (os.getenv("XPAY_NOTIFY_EMAIL") or "").strip()
        self.nickname = (os.getenv("XPAY_NICKNAME") or "sKrt").strip() or "sKrt"
        self.info_prefix = (os.getenv("XPAY_INFO_PREFIX") or "sKrt").strip() or "sKrt"
        self.test_email = (os.getenv("XPAY_TEST_EMAIL") or "").strip()
        self.force_custom = (os.getenv("XPAY_FORCE_CUSTOM") or "1").strip().lower() in {"1", "true", "yes", "on"}

    def _ensure_enabled(self) -> None:
        missing = []
        if not self.api_base:
            missing.append("XPAY_API_BASE")
        if not self.notify_email:
            missing.append("XPAY_NOTIFY_EMAIL")
        if missing:
            raise RuntimeError(f"missing XPay env: {'/'.join(missing)}")

    def _post_form(self, path: str, form: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.api_base}/{path.lstrip('/')}"
        body = urlencode({k: str(v) for k, v in form.items() if v is not None}).encode("utf-8")
        req = UrlRequest(url=url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
        req.add_header("Accept", "application/json,text/plain,*/*")
        with urlopen(req, timeout=20) as resp:  # nosec B310
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw or "{}")
        except Exception as exc:
            raise RuntimeError(f"xpay_invalid_json: {(raw or '').strip()[:300]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("xpay_invalid_json")
        return parsed

    def _get_json(self, path: str) -> Dict[str, Any]:
        url = f"{self.api_base}/{path.lstrip('/')}"
        req = UrlRequest(url=url, method="GET")
        req.add_header("Accept", "application/json,text/plain,*/*")
        with urlopen(req, timeout=20) as resp:  # nosec B310
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw or "{}")
        except Exception as exc:
            raise RuntimeError(f"xpay_invalid_json: {(raw or '').strip()[:300]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("xpay_invalid_json")
        return parsed

    def _pay_type(self, channel: str) -> str:
        if channel == "alipay_qr":
            return "Alipay"
        if channel == "wechat_native":
            return "Wechat"
        raise RuntimeError(f"unsupported_xpay_channel:{channel}")

    def _build_alipay_code(self, *, amount_yuan: str, pay_num: str, provider_order_id: str) -> str:
        open_url = f"{self.api_base}/openAlipay?money={quote(amount_yuan)}&num={quote(pay_num)}&id={quote(provider_order_id)}"
        return "alipays://platformapi/startapp?appId=20000067&url=" + quote(open_url, safe="")

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
        pay_type = self._pay_type(channel)
        amount_yuan = format((Decimal(max(1, int(amount_fen))) / Decimal(100)).quantize(Decimal("0.01")), "f")
        info = f"{self.info_prefix}:{order_no}"[:50]
        form = {
            "nickName": self.nickname[:20],
            "money": amount_yuan,
            "email": self.notify_email,
            "testEmail": self.test_email,
            "payType": pay_type,
            "info": info,
            "custom": "true" if self.force_custom else "false",
            "mobile": "false",
            "device": "sKrt-server",
        }
        logger.info("xpay create order order_no=%s channel=%s amount=%s", order_no, channel, amount_yuan)
        rsp = self._post_form("/pay/add", form)
        if rsp.get("success") is not True:
            raise RuntimeError(str(rsp.get("message") or "xpay_create_failed"))
        result = rsp.get("result") or {}
        if not isinstance(result, dict):
            raise RuntimeError("xpay_create_failed")
        provider_order_id = str(result.get("id") or "").strip()
        pay_num = str(result.get("payNum") or "").strip()
        if not provider_order_id:
            raise RuntimeError("xpay_missing_order_id")

        code_url = ""
        qr_image_url = ""
        pay_hint = f"付款后需在 XPay 后台或邮件中人工确认。订单标识号：{pay_num}"
        if channel == "alipay_qr":
            code_url = self._build_alipay_code(amount_yuan=amount_yuan, pay_num=pay_num, provider_order_id=provider_order_id)
            pay_hint = f"请使用支付宝扫码支付，必要时在备注中填写订单标识号：{pay_num}"
        elif channel == "wechat_native":
            qr_image_url = f"{self.api_base}/assets/qr/wechat/custom.png"
            pay_hint = f"请使用微信扫码支付，并在备注中填写订单标识号：{pay_num}"

        raw = dict(rsp)
        raw["pay_num"] = pay_num
        raw["pay_hint"] = pay_hint
        raw["provider_order_id"] = provider_order_id
        return PaymentCreateResult(
            provider_order_id=provider_order_id,
            code_url=code_url,
            qr_image_url=qr_image_url,
            raw=raw,
        )

    def verify_notify(self, payload: Dict[str, Any]) -> PaymentNotifyResult:
        raise RuntimeError("xpay_v2_notify_not_supported")

    def sync_order_status(self, *, order_no: str, provider_order_id: str = "") -> PaymentSyncResult | None:
        self._ensure_enabled()
        if not provider_order_id:
            return None
        rsp = self._get_json(f"/pay/state/{provider_order_id}")
        if rsp.get("success") is not True:
            return PaymentSyncResult(status="pending", paid=False, transaction_id="", provider_order_id=provider_order_id, raw=rsp)
        state = int(rsp.get("result") or 0)
        if state in {1, 3}:
            return PaymentSyncResult(status="paid", paid=True, transaction_id=provider_order_id, provider_order_id=provider_order_id, raw=rsp)
        if state == 2:
            return PaymentSyncResult(status="failed", paid=False, transaction_id="", provider_order_id=provider_order_id, raw=rsp)
        if state == 4:
            return PaymentSyncResult(status="scanned", paid=False, transaction_id="", provider_order_id=provider_order_id, raw=rsp)
        return PaymentSyncResult(status="pending", paid=False, transaction_id="", provider_order_id=provider_order_id, raw=rsp)

    def refund(self, *, order_no: str, provider_order_id: str = "") -> Dict[str, Any]:
        return {"ok": True, "order_no": order_no, "provider_order_id": provider_order_id, "noop": True}
