from __future__ import annotations

from fastapi import HTTPException

from .base import PaymentProvider
from .easypay_provider import EasyPayProvider
from .jeepay_provider import JeepayProvider
from .paypal_provider import PayPalProvider
from .xpay_provider import XPayProvider


def get_payment_provider(provider_name: str) -> PaymentProvider:
    provider = (provider_name or "").strip().lower()
    if provider == "easypay":
        return EasyPayProvider()
    if provider == "jeepay":
        return JeepayProvider()
    if provider == "paypal":
        return PayPalProvider()
    if provider == "xpay":
        return XPayProvider()
    raise HTTPException(status_code=503, detail=f"不支持的支付通道: {provider}")
