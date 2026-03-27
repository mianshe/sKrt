from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import jwt
from jwt import PyJWKClient


@dataclass(frozen=True)
class IdentityContext:
    tenant_id: str
    user_id: str
    roles: List[str]
    permissions: List[str]
    token_issuer: str


class JwtValidator:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str,
        leeway_seconds: int = 60,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.jwks_url = jwks_url
        self.leeway_seconds = max(0, int(leeway_seconds))
        self.cache_ttl_seconds = max(30, int(cache_ttl_seconds))
        self._jwk_client = PyJWKClient(jwks_url) if jwks_url else None
        self._cached_raw_token = ""
        self._cached_claims: Dict[str, Any] = {}
        self._cached_until = 0.0

    def validate(self, bearer_token: str) -> Dict[str, Any]:
        raw = str(bearer_token or "").strip()
        if not raw:
            raise ValueError("missing bearer token")
        now = time.time()
        if raw == self._cached_raw_token and self._cached_claims and now < self._cached_until:
            return dict(self._cached_claims)
        if self._jwk_client is None:
            raise ValueError("jwks url not configured")
        signing_key = self._jwk_client.get_signing_key_from_jwt(raw).key
        claims = jwt.decode(
            raw,
            signing_key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
            audience=self.audience,
            issuer=self.issuer,
            leeway=self.leeway_seconds,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
        self._cached_raw_token = raw
        self._cached_claims = dict(claims)
        self._cached_until = now + self.cache_ttl_seconds
        return dict(claims)


def to_identity_context(
    claims: Dict[str, Any],
    tenant_claim_key: str = "tenant_id",
    roles_claim_key: str = "roles",
    permissions_claim_key: str = "permissions",
) -> IdentityContext:
    tenant_id = str(claims.get(tenant_claim_key, "")).strip()
    user_id = str(claims.get("sub", "")).strip()
    if not tenant_id:
        raise ValueError(f"missing tenant claim: {tenant_claim_key}")
    if not user_id:
        raise ValueError("missing subject claim: sub")
    roles_raw = claims.get(roles_claim_key, [])
    perms_raw = claims.get(permissions_claim_key, [])
    if isinstance(roles_raw, str):
        roles = [x.strip() for x in roles_raw.split(",") if x.strip()]
    else:
        roles = [str(x).strip() for x in (roles_raw or []) if str(x).strip()]
    if isinstance(perms_raw, str):
        permissions = [x.strip() for x in perms_raw.split(",") if x.strip()]
    else:
        permissions = [str(x).strip() for x in (perms_raw or []) if str(x).strip()]
    return IdentityContext(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=roles,
        permissions=permissions,
        token_issuer=str(claims.get("iss", "")).strip(),
    )

