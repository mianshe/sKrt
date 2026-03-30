"""邮箱注册、登录与本地 HS256 JWT（与外部 JWKS 可并存）。"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any, Dict

import jwt

from backend.services.email_sender import send_plain_email


def _pbkdf2_hash(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return salt.hex() + "$" + dk.hex()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return _pbkdf2_hash(password, salt)


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


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


def issue_local_access_token(*, user_id: str, email: str) -> str:
    """tenant_id = user_id，保证知识库按账号隔离。"""
    secret = local_jwt_secret()
    if not secret:
        raise RuntimeError("AUTH_LOCAL_JWT_SECRET 未配置")
    now = int(time.time())
    ttl = _ttl_seconds()
    payload = {
        "iss": _issuer(),
        "aud": _audience(),
        "sub": user_id,
        "tenant_id": user_id,
        "email": email,
        "roles": ["tenant_admin"],
        "permissions": ["tenant.*"],
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
    """注册赠送的外部 OCR 次数（与余额扣减单位一致）。兼容旧环境变量名 *_PAGES。"""
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
    """同一 IP 终身最多 SIGNUP_MAX_FREE_GRANTS_PER_IP_LIFETIME 个不同邮箱各送一份。"""
    limit_ip = signup_max_free_grants_per_ip()
    calls = signup_free_calls_limit()
    if calls <= 0:
        return 0
    if email_already_granted_on_ip(conn, client_ip, email):
        return calls  # 同一邮箱重注册仍按「已有行」处理，见 register_user
    n = count_distinct_emails_granted_for_ip(conn, client_ip)
    if n >= limit_ip:
        return 0
    return calls


def send_register_code_email(to_email: str, code: str) -> None:
    send_plain_email(
        subject="资料解析 — 注册验证码",
        body=f"您的验证码是：{code}\n10 分钟内有效。\n如非本人操作请忽略。",
        to_email=to_email,
    )


def ingest_requires_login() -> bool:
    if not local_jwt_enabled():
        return False
    return (os.getenv("AUTH_INGEST_REQUIRES_LOGIN", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def is_anonymous_local_guest(identity: Dict[str, Any]) -> bool:
    return bool(local_jwt_enabled() and identity.get("auth_source") != "local_jwt")
