from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class PaymentCreateResult:
    provider_order_id: str
    code_url: str
    raw: Dict[str, Any]
    payment_url: str = ""


@dataclass
class PaymentNotifyResult:
    order_no: str
    paid: bool
    transaction_id: str
    provider_order_id: str
    raw: Dict[str, Any]


class PaymentProvider:
    def create_order(self, *, order_no: str, amount_fen: int, channel: str, subject: str, notify_url: str) -> PaymentCreateResult:
        raise NotImplementedError

    def verify_notify(self, payload: Dict[str, Any]) -> PaymentNotifyResult:
        raise NotImplementedError

    def refund(self, *, order_no: str, provider_order_id: str = "") -> Dict[str, Any]:
        raise NotImplementedError
