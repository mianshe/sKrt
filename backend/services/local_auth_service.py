"""本地邮箱注册、登录、找回密码与 HS256 JWT。"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from typing import Any, Dict

import jwt

from .email_sender import send_plain_email


DEFAULT_LOCAL_ADMIN_EMAIL_DOMAIN = "sciomenihilscire.com"


def password_pbkdf2_iterations() -> int:
    try:
        return max(120_000, int((os.getenv("AUTH_PASSWORD_PBKDF2_ITERATIONS") or "200000").strip() or "200000"))
    except ValueError:
        return 200_000


def _pbkdf2_hash(password: str, salt: bytes) -> str:
    iterations = password_pbkdf2_iterations()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return _pbkdf2_hash(password, salt)


def _parse_password_hash(stored: str) -> tuple[int, bytes, str]:
    parts = str(stored or "").split("$")
    if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
        try:
            return int(parts[1]), bytes.fromhex(parts[2]), parts[3]
        except Exception as exc:
            raise ValueError("invalid password hash") from exc
    if len(parts) == 2:
        # 兼容旧格式：salt$dk，历史固定 310000 次。
        try:
            return 310_000, bytes.fromhex(parts[0]), parts[1]
        except Exception as exc:
            raise ValueError("invalid legacy password hash") from exc
    raise ValueError("unsupported password hash format")


def verify_password(password: str, stored: str) -> bool:
    try:
        iterations, salt, dk_hex = _parse_password_hash(stored)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def password_hash_needs_rehash(stored: str) -> bool:
    try:
        iterations, _, _ = _parse_password_hash(stored)
        return iterations != password_pbkdf2_iterations() or not str(stored or "").startswith("pbkdf2_sha256$")
    except Exception:
        return True


def local_jwt_secret() -> str:
    return (os.getenv("AUTH_LOCAL_JWT_SECRET") or "").strip()


def local_jwt_enabled() -> bool:
    return bool(local_jwt_secret())


def _issuer() -> str:
    return (os.getenv("AUTH_LOCAL_JWT_ISSUER") or "xm1-local").strip() or "xm1-local"


def _audience() -> str:
    return (os.getenv("AUTH_LOCAL_JWT_AUDIENCE") or "xm1-web").strip() or "xm1-web"


def _ttl_seconds() -> int:
    try:
        return max(300, int((os.getenv("AUTH_LOCAL_JWT_TTL_SEC") or "604800").strip() or "604800"))
    except ValueError:
        return 604800


def _split_csv_env(value: str) -> list[str]:
    return [item.strip().lower() for item in str(value or "").split(",") if item.strip()]


def local_admin_email_domains() -> list[str]:
    raw = os.getenv("AUTH_LOCAL_ADMIN_EMAIL_DOMAINS")
    if raw is None:
        raw = DEFAULT_LOCAL_ADMIN_EMAIL_DOMAIN
    domains = _split_csv_env(raw)
    return domains or [DEFAULT_LOCAL_ADMIN_EMAIL_DOMAIN]


def local_admin_emails() -> list[str]:
    return _split_csv_env(os.getenv("AUTH_LOCAL_ADMIN_EMAILS") or "")


def issue_local_access_token(*, user_id: str, email: str) -> str:
    """tenant_id = user_id，保证知识库按账号隔离。"""
    secret = local_jwt_secret()
    if not secret:
        raise RuntimeError("AUTH_LOCAL_JWT_SECRET 未配置")
    now = int(time.time())
    ttl = _ttl_seconds()
    identity = local_identity_claims(email)
    payload = {
        "iss": _issuer(),
        "aud": _audience(),
        "sub": user_id,
        "tenant_id": user_id,
        "email": email,
        "roles": list(identity.get("roles") or []),
        "permissions": list(identity.get("permissions") or []),
        "is_admin": bool(identity.get("is_admin")),
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_local_access_token(token: str) -> Dict[str, Any]:
    secret = local_jwt_secret()
    if not secret:
        raise ValueError("local jwt not configured")
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience=_audience(),
        issuer=_issuer(),
        options={"require": ["exp", "iat", "iss", "sub"]},
    )


def signup_free_calls_limit() -> int:
    try:
        return max(
            0,
            int(
                (
                    os.getenv("SIGNUP_FREE_OCR_CALLS")
                    or os.getenv("SIGNUP_FREE_OCR_PAGES")
                    or os.getenv("GPU_OCR_INITIAL_FREE_CALLS")
                    or os.getenv("GPU_OCR_INITIAL_FREE_PAGES")
                    or "100"
                ).strip()
                or "100"
            ),
        )
    except ValueError:
        return 100


def signup_max_free_grants_per_ip() -> int:
    try:
        return max(0, int((os.getenv("SIGNUP_MAX_FREE_GRANTS_PER_IP_LIFETIME") or "3").strip() or "3"))
    except ValueError:
        return 3


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()[:320]


def is_local_admin_email(email: str) -> bool:
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        return False
    if normalized in set(local_admin_emails()):
        return True
    domain = normalized.split("@", 1)[1]
    return domain in set(local_admin_email_domains())


def local_identity_claims(email: str) -> Dict[str, Any]:
    normalized = normalize_email(email)
    if is_local_admin_email(normalized):
        return {
            "roles": ["tenant_admin"],
            "permissions": ["tenant.*"],
            "is_admin": True,
        }
    return {
        "roles": ["tenant_user"],
        "permissions": [
            "tenant.upload.write",
            "tenant.upload.read",
            "tenant.documents.read",
            "tenant.documents.delete",
            "tenant.metrics.read",
            "tenant.knowledge.read",
            "tenant.chat.write",
            "tenant.chat.clear",
            "tenant.insights.read",
            "tenant.pipeline.write",
            "tenant.pipeline.read",
            "tenant.exam.write",
            "tenant.generate.write",
        ],
        "is_admin": False,
    }


def _random_code() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"


def store_registration_code(conn: sqlite3.Connection, email: str, code: str, ttl_sec: int = 600) -> None:
    import hashlib as hl

    em = normalize_email(email)
    code_hash = hl.sha256(code.encode("utf-8")).hexdigest()
    exp = time.time() + ttl_sec
    conn.execute("DELETE FROM app_registration_codes WHERE email = ?", (em,))
    conn.execute(
        """
        INSERT INTO app_registration_codes(email, code_hash, expires_at_unix)
        VALUES(?,?,?)
        """,
        (em, code_hash, exp),
    )


def store_password_reset_code(conn: sqlite3.Connection, email: str, code: str, ttl_sec: int = 600) -> None:
    import hashlib as hl

    em = normalize_email(email)
    code_hash = hl.sha256(code.encode("utf-8")).hexdigest()
    exp = time.time() + ttl_sec
    conn.execute("DELETE FROM app_password_reset_codes WHERE email = ?", (em,))
    conn.execute(
        """
        INSERT INTO app_password_reset_codes(email, code_hash, expires_at_unix)
        VALUES(?,?,?)
        """,
        (em, code_hash, exp),
    )


def verify_registration_code(conn: sqlite3.Connection, email: str, code: str) -> bool:
    import hashlib as hl

    em = normalize_email(email)
    row = conn.execute(
        "SELECT code_hash, expires_at_unix FROM app_registration_codes WHERE email = ?",
        (em,),
    ).fetchone()
    if not row:
        return False
    if float(row[1]) < time.time():
        return False
    expect = row[0]
    got = hl.sha256(code.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(expect, got):
        return False
    conn.execute("DELETE FROM app_registration_codes WHERE email = ?", (em,))
    return True


def verify_password_reset_code(conn: sqlite3.Connection, email: str, code: str) -> bool:
    import hashlib as hl

    em = normalize_email(email)
    row = conn.execute(
        "SELECT code_hash, expires_at_unix FROM app_password_reset_codes WHERE email = ?",
        (em,),
    ).fetchone()
    if not row:
        return False
    if float(row[1]) < time.time():
        return False
    expect = row[0]
    got = hl.sha256(code.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(expect, got):
        return False
    conn.execute("DELETE FROM app_password_reset_codes WHERE email = ?", (em,))
    return True


def count_distinct_emails_granted_for_ip(conn: sqlite3.Connection, client_ip: str) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT email) FROM signup_ip_free_ocr_grants WHERE client_ip = ?",
        (client_ip,),
    ).fetchone()
    return int(row[0] if row else 0)


def email_already_granted_on_ip(conn: sqlite3.Connection, client_ip: str, email: str) -> bool:
    em = normalize_email(email)
    row = conn.execute(
        "SELECT 1 FROM signup_ip_free_ocr_grants WHERE client_ip = ? AND email = ?",
        (client_ip, em),
    ).fetchone()
    return row is not None


def record_signup_grant(conn: Any, client_ip: str, email: str) -> None:
    em = normalize_email(email)
    conn.execute(
        "INSERT OR IGNORE INTO signup_ip_free_ocr_grants(client_ip, email) VALUES(?, ?)",
        (client_ip, em),
    )


def decide_signup_free_calls(conn: sqlite3.Connection, client_ip: str, email: str) -> int:
    limit_ip = signup_max_free_grants_per_ip()
    calls = signup_free_calls_limit()
    if calls <= 0:
        return 0
    if email_already_granted_on_ip(conn, client_ip, email):
        return calls
    n = count_distinct_emails_granted_for_ip(conn, client_ip)
    if n >= limit_ip:
        return 0
    return calls


def send_register_code_email(to_email: str, code: str) -> None:
    send_plain_email(
        subject="资料解析 - 注册验证码",
        body=f"您的验证码是：{code}\n10 分钟内有效。\n如非本人操作请忽略。",
        to_email=to_email,
    )


def send_password_reset_code_email(to_email: str, code: str) -> None:
    send_plain_email(
        subject="资料解析 - 重置密码验证码",
        body=f"您的密码重置验证码是：{code}\n10 分钟内有效。\n如非本人操作请忽略。",
        to_email=to_email,
    )


def ingest_requires_login() -> bool:
    if not local_jwt_enabled():
        return False
    return (os.getenv("AUTH_INGEST_REQUIRES_LOGIN", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def is_anonymous_local_guest(identity: Dict[str, Any]) -> bool:
    return bool(local_jwt_enabled() and identity.get("auth_source") != "local_jwt")
