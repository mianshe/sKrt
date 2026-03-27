from backend.services.security_context import to_identity_context
from backend.runtime_config import RuntimeConfig


def test_identity_context_from_claims():
    claims = {
        "iss": "https://issuer.example.com",
        "sub": "user-1",
        "tenant_id": "tenant-a",
        "roles": ["tenant_admin"],
        "permissions": ["tenant.documents.read"],
    }
    identity = to_identity_context(claims)
    assert identity.tenant_id == "tenant-a"
    assert identity.user_id == "user-1"
    assert "tenant_admin" in identity.roles
    assert "tenant.documents.read" in identity.permissions


def test_runtime_config_auth_defaults(monkeypatch):
    monkeypatch.delenv("AUTH_JWT_ENABLED", raising=False)
    monkeypatch.delenv("AUTH_JWT_ISSUER", raising=False)
    monkeypatch.delenv("AUTH_JWT_AUDIENCE", raising=False)
    monkeypatch.delenv("AUTH_JWT_JWKS_URL", raising=False)
    cfg = RuntimeConfig.from_env()
    assert cfg.auth.enabled is False
    assert cfg.auth.tenant_claim_key == "tenant_id"
    assert cfg.auth.roles_claim_key == "roles"
    assert cfg.auth.permissions_claim_key == "permissions"
