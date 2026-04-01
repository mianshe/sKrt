from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List
from urllib.request import Request as UrlRequest, urlopen

from .base import PaymentCreateResult, PaymentNotifyResult, PaymentProvider

logger = logging.getLogger(__name__)


class JeepayProvider(PaymentProvider):
    def __init__(self) -> None:
        self.api_base = (os.getenv("JEEPAY_API_BASE") or "").strip().rstrip("/")
        self.mch_no = (os.getenv("JEEPAY_MCH_NO") or "").strip()
        self.app_id = (os.getenv("JEEPAY_APP_ID") or "").strip()
        self.api_key = (os.getenv("JEEPAY_API_KEY") or os.getenv("JEEPAY_APP_SECRET") or "").strip()
        self.notify_sign_secret = (os.getenv("JEEPAY_NOTIFY_SIGN_SECRET") or self.api_key).strip()
        self.client_ip = (os.getenv("JEEPAY_CLIENT_IP") or "127.0.0.1").strip() or "127.0.0.1"
        self.return_url = (os.getenv("PAY_RETURN_URL") or "").strip()

    def _ensure_enabled(self) -> None:
        missing = [
            name
            for name, value in {
                "JEEPAY_API_BASE": self.api_base,
                "JEEPAY_MCH_NO": self.mch_no,
                "JEEPAY_APP_ID": self.app_id,
                "JEEPAY_API_KEY/JEEPAY_APP_SECRET": self.api_key,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"missing Jeepay env: {'/'.join(missing)}")

    def _sign(self, payload: Dict[str, Any], *, secret: str | None = None) -> str:
        items: List[str] = []
        for key in sorted(payload.keys()):
            if key == "sign":
                continue
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, bool):
                text = "true" if value else "false"
            else:
                text = str(value).strip()
            if not text:
                continue
            items.append(f"{key}={text}")
        raw = "&".join(items) + f"&key={secret or self.api_key}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()

    def _verify_sign(self, payload: Dict[str, Any], *, secret: str | None = None) -> bool:
        sign = str(payload.get("sign") or "").strip().upper()
        if not sign:
            return False
        local_sign = self._sign(payload, secret=secret)
        return hmac.compare_digest(local_sign, sign)

    def _post_json(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = UrlRequest(url=url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json,text/plain,*/*")
        with urlopen(req, timeout=20) as resp:  # nosec B310
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw or "{}")
        except Exception as exc:
            raise RuntimeError(f"invalid_json: {(raw or '').strip()[:300]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"invalid_json: {(raw or '').strip()[:300]}")
        return parsed

    def _validate_response_sign(self, response: Dict[str, Any]) -> None:
        sign = str(response.get("sign") or "").strip()
        data = response.get("data")
        if not sign:
            return
        if not isinstance(data, dict):
            raise RuntimeError("jeepay_response_invalid_data")
        signed_payload = dict(data)
        signed_payload["sign"] = sign
        if not self._verify_sign(signed_payload):
            raise RuntimeError("jeepay_response_invalid_sign")

    def _way_code(self, channel: str) -> str:
        if channel == "wechat_native":
            return "WX_NATIVE"
        if channel == "alipay_qr":
            return "ALI_QR"
        raise RuntimeError(f"unsupported_jeepay_channel:{channel}")

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
        way_code = self._way_code(channel)
        payload: Dict[str, Any] = {
            "mchNo": self.mch_no,
            "appId": self.app_id,
            "mchOrderNo": order_no,
            "wayCode": way_code,
            "amount": max(1, int(amount_fen)),
            "currency": "cny",
            "clientIp": self.client_ip,
            "subject": subject,
            "body": subject,
            "notifyUrl": notify_url,
            "returnUrl": self.return_url,
            "reqTime": str(int(time.time() * 1000)),
            "version": "1.0",
            "signType": "MD5",
            "channelExtra": json.dumps({"payDataType": "codeImgUrl"}, ensure_ascii=False, separators=(",", ":")),
        }
        payload["sign"] = self._sign(payload)
        logger.info(
            "jeepay create order order_no=%s channel=%s way_code=%s api_base=%s",
            order_no,
            channel,
            way_code,
            self.api_base,
        )
        rsp = self._post_json("/api/pay/unifiedOrder", payload)
        self._validate_response_sign(rsp)
        code = int(rsp.get("code") or -1)
        msg = str(rsp.get("msg") or "")
        data = rsp.get("data")
        if code != 0 or not isinstance(data, dict):
            raise RuntimeError(msg or "jeepay_create_failed")
        order_state = str(data.get("orderState") or data.get("state") or "")
        if order_state and order_state not in {"0", "1", "2"}:
            err_msg = str(data.get("errMsg") or data.get("errCode") or msg or "jeepay_create_failed")
            raise RuntimeError(err_msg)
        pay_data_type = str(data.get("payDataType") or "").strip()
        pay_data = str(data.get("payData") or "").strip()
        code_url = ""
        qr_image_url = ""
        payment_url = ""
        if pay_data_type == "codeImgUrl":
            qr_image_url = pay_data
        elif pay_data_type == "codeUrl":
            code_url = pay_data
        elif pay_data_type in {"payUrl", "form"}:
            payment_url = pay_data
        else:
            code_url = pay_data
        logger.info(
            "jeepay create order ok order_no=%s pay_order_id=%s pay_data_type=%s order_state=%s",
            order_no,
            str(data.get("payOrderId") or ""),
            pay_data_type,
            order_state,
        )
        return PaymentCreateResult(
            provider_order_id=str(data.get("payOrderId") or ""),
            code_url=code_url,
            payment_url=payment_url,
            qr_image_url=qr_image_url,
            raw=rsp,
        )

    def verify_notify(self, payload: Dict[str, Any]) -> PaymentNotifyResult:
        self._ensure_enabled()
        normalized = {str(k): str(v) for k, v in payload.items()}
        if not self._verify_sign(normalized, secret=self.notify_sign_secret):
            raise RuntimeError("invalid_sign")
        order_no = str(normalized.get("mchOrderNo") or "")
        state = str(normalized.get("state") or normalized.get("orderState") or "")
        paid = state == "2"
        logger.info(
            "jeepay notify verified order_no=%s state=%s pay_order_id=%s channel_order_no=%s",
            order_no,
            state,
            str(normalized.get("payOrderId") or ""),
            str(normalized.get("channelOrderNo") or ""),
        )
        return PaymentNotifyResult(
            order_no=order_no,
            paid=paid,
            transaction_id=str(normalized.get("channelOrderNo") or normalized.get("payOrderId") or ""),
            provider_order_id=str(normalized.get("payOrderId") or ""),
            raw=normalized,
        )

    def refund(self, *, order_no: str, provider_order_id: str = "") -> Dict[str, Any]:
        self._ensure_enabled()
        logger.info("jeepay refund fallback to local settlement order_no=%s provider_order_id=%s", order_no, provider_order_id)
        return {"ok": True, "order_no": order_no, "provider_order_id": provider_order_id, "noop": True}
