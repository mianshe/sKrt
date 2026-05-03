from __future__ import annotations

import base64
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .base import PaymentCreateResult, PaymentNotifyResult, PaymentProvider, PaymentSyncResult


class ManualQrProvider(PaymentProvider):
    def _resolve_image_path(self, raw: str) -> Path:
        value = str(raw or "").strip()
        path = Path(value).expanduser()
        root = Path(__file__).resolve().parents[3]
        if path.is_absolute():
            return path
        # Linux containers may receive a Windows absolute path via env vars.
        # In that case, fall back to the repo-local pay/ filename.
        if re.match(r"^[A-Za-z]:[\\/]", value):
            return root / "pay" / Path(value.replace("\\", "/")).name
        return root / path

    def _env_for_channel(self, channel: str) -> tuple[str, str]:
        if channel == "wechat_native":
            return (
                os.getenv("MANUAL_PAY_WECHAT_QR_IMAGE")
                or os.getenv("MANUAL_PAY_WECHAT_QR_URL")
                or os.getenv("PAY_WECHAT_QR_IMAGE")
                or "",
                "微信",
            )
        if channel == "alipay_qr":
            return (
                os.getenv("MANUAL_PAY_ALIPAY_QR_IMAGE")
                or os.getenv("MANUAL_PAY_ALIPAY_QR_URL")
                or os.getenv("PAY_ALIPAY_QR_IMAGE")
                or "",
                "支付宝",
            )
        raise RuntimeError(f"unsupported_manual_qr_channel:{channel}")

    def _image_to_src(self, raw: str) -> str:
        value = str(raw or "").strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://", "data:image/")):
            return value

        path = self._resolve_image_path(value)
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"manual_qr_image_not_found:{path}")

        content_type = mimetypes.guess_type(str(path))[0] or "image/png"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{content_type};base64,{data}"

    def create_order(
        self,
        *,
        order_no: str,
        amount_fen: int,
        channel: str,
        subject: str,
        notify_url: str,
    ) -> PaymentCreateResult:
        raw_image, label = self._env_for_channel(channel)
        qr_image_url = self._image_to_src(raw_image)
        if not qr_image_url:
            raise RuntimeError(
                "未配置手动收款码。请配置 MANUAL_PAY_WECHAT_QR_IMAGE 或 MANUAL_PAY_ALIPAY_QR_IMAGE，"
                "值可以是图片 URL 或本地图片路径。"
            )
        amount_yuan = max(1, int(amount_fen)) / 100
        pay_hint = f"请使用{label}扫码支付 ¥{amount_yuan:.2f}，付款备注建议填写订单号：{order_no}。到账需要管理员人工确认。"
        return PaymentCreateResult(
            provider_order_id=order_no,
            code_url="",
            qr_image_url=qr_image_url,
            raw={
                "provider": "manual_qr",
                "pay_hint": pay_hint,
                "manual_settlement": True,
            },
        )

    def verify_notify(self, payload: Dict[str, Any]) -> PaymentNotifyResult:
        raise RuntimeError("manual_qr_notify_not_supported")

    def sync_order_status(self, *, order_no: str, provider_order_id: str = "") -> Optional[PaymentSyncResult]:
        return PaymentSyncResult(
            status="pending",
            paid=False,
            transaction_id="",
            provider_order_id=provider_order_id or order_no,
            raw={"manual_settlement": True},
        )

    def refund(self, *, order_no: str, provider_order_id: str = "") -> Dict[str, Any]:
        return {"ok": True, "order_no": order_no, "provider_order_id": provider_order_id, "noop": True}
