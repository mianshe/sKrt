# SaaS Security Baseline

## Production must-have

- Set `APP_ENV=production`.
- Enable JWT verification: `AUTH_JWT_ENABLED=1`.
- Configure OIDC fields: `AUTH_JWT_ISSUER`, `AUTH_JWT_AUDIENCE`, `AUTH_JWT_JWKS_URL`.
- Enable membership check: `AUTH_REQUIRE_MEMBERSHIP_CHECK=1`.
- Configure PostgreSQL with RLS-capable role via `DATABASE_URL`.

## Tenant isolation

- Every request must carry a valid Bearer token in production.
- API extracts `tenant_id`, `sub(user_id)`, roles and permissions from JWT claims.
- Membership is checked against `tenant_users` table before serving business routes.
- PostgreSQL session context is set through:
  - `app.tenant_id`
  - `app.user_id`
  - `app.roles`

## Auditing and security events

- High-risk actions are recorded in `audit_logs`, including:
  - document deletion
  - chat memory clear
- Security events are recorded in `security_events`, including:
  - authn/authz failures
  - server errors

## Quota guardrails

- Tenant quota defaults:
  - documents: `TENANT_DEFAULT_MAX_DOCUMENTS`
  - vectors: `TENANT_DEFAULT_MAX_VECTORS`
  - storage bytes: `TENANT_DEFAULT_MAX_STORAGE_BYTES`
- Upload/write routes reject requests when limits are exceeded.

## Codespaces notes

- Use GitHub Codespaces Secrets for all tokens and DB credentials.
- Do not commit `.env` with secrets.
- Keep `AUTH_JWT_ENABLED=0` only for local development.
