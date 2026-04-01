from __future__ import annotations

import logging
import os
from typing import Any, Dict

from .base import PaymentCreateResult, PaymentNotifyResult, PaymentProvider

logger = logging.getLogger(__name__)


class JeepayProvider(PaymentProvider):
    """
    Project-side scaffold for a self-hosted Jeepay gateway.

    The current repository changes focus on making the business layer provider-
    agnostic first. Once a Jeepay instance and merchant parameters are ready,
    the actual API calls can be implemented here without touching order logic.
    """

    def __init__(self) -> None:
        self.api_base = (os.getenv("JEEPAY_API_BASE") or "").strip().rstrip("/")
        self.mch_no = (os.getenv("JEEPAY_MCH_NO") or "").strip()
        self.app_id = (os.getenv("JEEPAY_APP_ID") or "").strip()
        self.api_key = (os.getenv("JEEPAY_API_KEY") or "").strip()
        self.notify_sign_secret = (os.getenv("JEEPAY_NOTIFY_SIGN_SECRET") or self.api_key).strip()

    def _ensure_enabled(self) -> None:
        missing = [
            name
            for name, value in {
                "JEEPAY_API_BASE": self.api_base,
                "JEEPAY_MCH_NO": self.mch_no,
                "JEEPAY_APP_ID": self.app_id,
                "JEEPAY_API_KEY": self.api_key,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"未配置 {'/'.join(missing)}")

    def _not_ready(self) -> RuntimeError:
        return RuntimeError("Jeepay 支付网关骨架已接入项目，但当前部署尚未完成真实下单/回调配置")

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
        logger.warning(
            "jeepay create order requested before gateway wiring is completed order_no=%s channel=%s api_base=%s",
            order_no,
            channel,
            self.api_base,
        )
        raise self._not_ready()

    def verify_notify(self, payload: Dict[str, Any]) -> PaymentNotifyResult:
        self._ensure_enabled()
        logger.warning("jeepay notify received before gateway wiring is completed payload_keys=%s", ",".join(sorted(payload.keys())))
        raise self._not_ready()

    def refund(self, *, order_no: str, provider_order_id: str = "") -> Dict[str, Any]:
        self._ensure_enabled()
        logger.warning("jeepay refund requested before gateway wiring is completed order_no=%s", order_no)
        raise self._not_ready()
