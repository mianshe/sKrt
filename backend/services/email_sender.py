from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send_plain_email(*, subject: str, body: str, to_email: str) -> None:
    host = (os.getenv("SMTP_HOST") or "").strip()
    port = int((os.getenv("SMTP_PORT") or "587").strip() or "587")
    username = (os.getenv("SMTP_USERNAME") or "").strip()
    password = (os.getenv("SMTP_PASSWORD") or "").strip()
    sender = (os.getenv("SMTP_FROM") or username or "").strip()
    use_tls = (os.getenv("SMTP_USE_TLS") or "1").strip().lower() in {"1", "true", "yes", "on"}
    if not host or not sender:
        raise RuntimeError("未配置 SMTP_HOST/SMTP_FROM")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=20) as client:
        if use_tls:
            client.starttls()
        if username:
            client.login(username, password)
        client.send_message(msg)

