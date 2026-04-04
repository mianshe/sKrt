import os
from typing import Optional


def _parse_env_bool(value: Optional[str]) -> Optional[bool]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def is_self_hosted_provider_billing_enabled() -> bool:
    return bool(_parse_env_bool(os.getenv("SELF_HOSTED_PROVIDER_BILLING")))


def is_ocr_internal_billing_enabled() -> bool:
    explicit = _parse_env_bool(os.getenv("OCR_INTERNAL_BILLING_ENABLED"))
    if explicit is not None:
        return explicit
    return not is_self_hosted_provider_billing_enabled()


def is_embedding_internal_billing_enabled() -> bool:
    explicit = _parse_env_bool(os.getenv("EMBEDDING_INTERNAL_BILLING_ENABLED"))
    if explicit is not None:
        return explicit
    return not is_self_hosted_provider_billing_enabled()


def is_self_hosted_ocr_billing() -> bool:
    return not is_ocr_internal_billing_enabled()


def is_self_hosted_embedding_billing() -> bool:
    return not is_embedding_internal_billing_enabled()
