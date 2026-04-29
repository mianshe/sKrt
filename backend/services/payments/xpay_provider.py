from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import time
import base64
import mimetypes
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from .base import PaymentCreateResult, PaymentNotifyResult, PaymentProvider, PaymentSyncResult

logger = logging.getLogger(__name__)


class XPayProvider(PaymentProvider):
    def __init__(self) -> None:
        self.api_base = (os.getenv("XPAY_API_BASE") or "").strip().rstrip("/")
        self.notify_email = (os.getenv("XPAY_NOTIFY_EMAIL") or "").strip()
        self.nickname = (os.getenv("XPAY_NICKNAME") or "sKrt").strip() or "sKrt"
        self.info_prefix = (os.getenv("XPAY_INFO_PREFIX") or "sKrt").strip() or "sKrt"
        self.test_email = (os.getenv("XPAY_TEST_EMAIL") or "").strip()
        self.force_custom = (os.getenv("XPAY_FORCE_CUSTOM") or "1").strip().lower() in {"1", "true", "yes", "on"}
        self.create_timeout = max(3, int((os.getenv("XPAY_CREATE_TIMEOUT") or "12").strip() or "12"))
        self.status_timeout = max(2, int((os.getenv("XPAY_STATUS_TIMEOUT") or "6").strip() or "6"))
        self.local_mock_enabled = (os.getenv("XPAY_LOCAL_MOCK_ENABLED") or "0").strip().lower() in {"1", "true", "yes", "on"}
        self.local_mock_auto_paid_after_sec = max(2, int((os.getenv("XPAY_LOCAL_AUTO_PAID_AFTER_SEC") or "8").strip() or "8"))
        self.local_mock_scanned_after_sec = max(1, min(self.local_mock_auto_paid_after_sec - 1, int((os.getenv("XPAY_LOCAL_SCANNED_AFTER_SEC") or "3").strip() or "3")))
        self.local_mock_db_path = Path(os.getenv("XPAY_LOCAL_MOCK_DB") or (Path(__file__).resolve().parents[3] / ".cache" / "xpay_local_mock.db"))

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    def _mock_conn(self) -> sqlite3.Connection:
        self.local_mock_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.local_mock_db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS xpay_local_mock_orders (
                provider_order_id TEXT PRIMARY KEY,
                order_no TEXT NOT NULL,
                pay_num TEXT NOT NULL,
                channel TEXT NOT NULL,
                amount_cny REAL NOT NULL DEFAULT 0,
                created_at_unix REAL NOT NULL,
                paid_at_unix REAL DEFAULT NULL
            )
            """
        )
        return conn

    def _read_qr_data_url(self, *, env_key: str, fallback_filename: str) -> str:
        raw = str(os.getenv(env_key) or "").strip()
        value = raw or str(self._repo_root() / "pay" / fallback_filename)
        if value.startswith(("http://", "https://", "data:image/")):
            return value
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self._repo_root() / path
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"xpay_local_mock_image_not_found:{path}")
        content_type = mimetypes.guess_type(str(path))[0] or "image/png"
        return f"data:{content_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

    def _mock_create_order(
        self,
        *,
        order_no: str,
        amount_fen: int,
        channel: str,
    ) -> PaymentCreateResult:
        provider_order_id = f"MOCK{secrets.token_hex(6).upper()}"
        pay_num = f"{secrets.randbelow(900000) + 100000}"
        amount_yuan = float((Decimal(max(1, int(amount_fen))) / Decimal(100)).quantize(Decimal("0.01")))
        conn = self._mock_conn()
        try:
            conn.execute(
                """
                INSERT INTO xpay_local_mock_orders(provider_order_id, order_no, pay_num, channel, amount_cny, created_at_unix)
                VALUES(?,?,?,?,?,?)
                """,
                (provider_order_id, order_no, pay_num, channel, amount_yuan, time.time()),
            )
            conn.commit()
        finally:
            conn.close()
        qr_image_url = ""
        code_url = ""
        if channel == "wechat_native":
            qr_image_url = self._read_qr_data_url(env_key="MANUAL_PAY_WECHAT_QR_IMAGE", fallback_filename="wechat.png")
        elif channel == "alipay_qr":
            qr_image_url = self._read_qr_data_url(env_key="MANUAL_PAY_ALIPAY_QR_IMAGE", fallback_filename="alipay.png")
        pay_hint = f"本地 XPay 模拟已启用。请在 {self.local_mock_auto_paid_after_sec} 秒后等待自动到账。订单标识号：{pay_num}"
        return PaymentCreateResult(
            provider_order_id=provider_order_id,
            code_url=code_url,
            qr_image_url=qr_image_url,
            raw={
                "success": True,
                "pay_num": pay_num,
                "pay_hint": pay_hint,
                "provider_order_id": provider_order_id,
                "local_mock": True,
            },
        )

    def _mock_sync_order_status(self, *, provider_order_id: str) -> PaymentSyncResult | None:
        conn = self._mock_conn()
        try:
            row = conn.execute(
                "SELECT * FROM xpay_local_mock_orders WHERE provider_order_id = ?",
                (provider_order_id,),
            ).fetchone()
            if not row:
                return None
            created_at = float(row["created_at_unix"] or 0)
            now = time.time()
            elapsed = max(0.0, now - created_at)
            paid_at = row["paid_at_unix"]
            if paid_at is None and elapsed >= self.local_mock_auto_paid_after_sec:
                conn.execute(
                    "UPDATE xpay_local_mock_orders SET paid_at_unix = ? WHERE provider_order_id = ?",
                    (now, provider_order_id),
                )
                conn.commit()
                paid_at = now
            if paid_at is not None:
                return PaymentSyncResult(
                    status="paid",
                    paid=True,
                    transaction_id=provider_order_id,
                    provider_order_id=provider_order_id,
                    raw={"local_mock": True, "result": 1},
                )
            if elapsed >= self.local_mock_scanned_after_sec:
                return PaymentSyncResult(
                    status="scanned",
                    paid=False,
                    transaction_id="",
                    provider_order_id=provider_order_id,
                    raw={"local_mock": True, "result": 4},
                )
            return PaymentSyncResult(
                status="pending",
                paid=False,
                transaction_id="",
                provider_order_id=provider_order_id,
                raw={"local_mock": True, "result": 0},
            )
        finally:
            conn.close()

    def _add_common_headers(self, req: UrlRequest) -> None:
        req.add_header("Accept", "application/json,text/plain,*/*")
        req.add_header("User-Agent", "Mozilla/5.0")
        req.add_header("Referer", f"{self.api_base}/")

    def _ensure_enabled(self) -> None:
        missing = []
        if not self.api_base:
            missing.append("XPAY_API_BASE")
        if not self.notify_email:
            missing.append("XPAY_NOTIFY_EMAIL")
        if missing:
            raise RuntimeError(f"missing XPay env: {'/'.join(missing)}")

    def _post_form(self, path: str, form: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.api_base}/{path.lstrip('/')}"
        body = urlencode({k: str(v) for k, v in form.items() if v is not None}).encode("utf-8")
        req = UrlRequest(url=url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
        self._add_common_headers(req)
        try:
            with urlopen(req, timeout=self.create_timeout) as resp:  # nosec B310
                raw = resp.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="ignore").strip()
            if body_text:
                raise RuntimeError(f"xpay_http_{exc.code}: {body_text[:300]}") from exc
            raise RuntimeError(f"xpay_http_{exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(
                f"xpay_unreachable: 无法连接 XPAY_API_BASE={self.api_base}。"
                "如果只是想展示自己的微信/支付宝收款码，请改用 manual_qr 并配置 MANUAL_PAY_WECHAT_QR_IMAGE / MANUAL_PAY_ALIPAY_QR_IMAGE。"
            ) from exc
        try:
            parsed = json.loads(raw or "{}")
        except Exception as exc:
            raise RuntimeError(f"xpay_invalid_json: {(raw or '').strip()[:300]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("xpay_invalid_json")
        return parsed

    def _get_json(self, path: str) -> Dict[str, Any]:
        url = f"{self.api_base}/{path.lstrip('/')}"
        req = UrlRequest(url=url, method="GET")
        self._add_common_headers(req)
        try:
            with urlopen(req, timeout=self.status_timeout) as resp:  # nosec B310
                raw = resp.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="ignore").strip()
            if body_text:
                raise RuntimeError(f"xpay_http_{exc.code}: {body_text[:300]}") from exc
            raise RuntimeError(f"xpay_http_{exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"xpay_unreachable: 无法连接 XPAY_API_BASE={self.api_base}") from exc
        try:
            parsed = json.loads(raw or "{}")
        except Exception as exc:
            raise RuntimeError(f"xpay_invalid_json: {(raw or '').strip()[:300]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("xpay_invalid_json")
        return parsed

    def _pay_type(self, channel: str) -> str:
        if channel == "alipay_qr":
            return "Alipay"
        if channel == "wechat_native":
            return "Wechat"
        raise RuntimeError(f"unsupported_xpay_channel:{channel}")

    def _build_alipay_code(self, *, amount_yuan: str, pay_num: str, provider_order_id: str) -> str:
        open_url = f"{self.api_base}/openAlipay?money={quote(amount_yuan)}&num={quote(pay_num)}&id={quote(provider_order_id)}"
        return "alipays://platformapi/startapp?appId=20000067&url=" + quote(open_url, safe="")

    def create_order(
        self,
        *,
        order_no: str,
        amount_fen: int,
        channel: str,
        subject: str,
        notify_url: str,
    ) -> PaymentCreateResult:
        if self.local_mock_enabled:
            return self._mock_create_order(order_no=order_no, amount_fen=amount_fen, channel=channel)
        self._ensure_enabled()
        pay_type = self._pay_type(channel)
        amount_yuan = format((Decimal(max(1, int(amount_fen))) / Decimal(100)).quantize(Decimal("0.01")), "f")
        info = f"{self.info_prefix}:{order_no}"[:50]
        form = {
            "nickName": self.nickname[:20],
            "money": amount_yuan,
            "email": self.notify_email,
            "testEmail": self.test_email,
            "payType": pay_type,
            "info": info,
            "custom": "true" if self.force_custom else "false",
            "mobile": "false",
            "device": "sKrt-server",
        }
        logger.info("xpay create order order_no=%s channel=%s amount=%s", order_no, channel, amount_yuan)
        rsp = self._post_form("/pay/add", form)
        if rsp.get("success") is not True:
            raise RuntimeError(str(rsp.get("message") or "xpay_create_failed"))
        result = rsp.get("result") or {}
        if not isinstance(result, dict):
            raise RuntimeError("xpay_create_failed")
        provider_order_id = str(result.get("id") or "").strip()
        pay_num = str(result.get("payNum") or "").strip()
        if not provider_order_id:
            raise RuntimeError("xpay_missing_order_id")

        code_url = ""
        qr_image_url = ""
        pay_hint = f"付款后需在 XPay 后台或邮件中人工确认。订单标识号：{pay_num}"
        if channel == "alipay_qr":
            code_url = self._build_alipay_code(amount_yuan=amount_yuan, pay_num=pay_num, provider_order_id=provider_order_id)
            pay_hint = f"请使用支付宝扫码支付，必要时在备注中填写订单标识号：{pay_num}"
        elif channel == "wechat_native":
            qr_image_url = f"{self.api_base}/assets/qr/wechat/custom.png"
            pay_hint = f"请使用微信扫码支付，并在备注中填写订单标识号：{pay_num}"

        raw = dict(rsp)
        raw["pay_num"] = pay_num
        raw["pay_hint"] = pay_hint
        raw["provider_order_id"] = provider_order_id
        return PaymentCreateResult(
            provider_order_id=provider_order_id,
            code_url=code_url,
            qr_image_url=qr_image_url,
            raw=raw,
        )

    def verify_notify(self, payload: Dict[str, Any]) -> PaymentNotifyResult:
        raise RuntimeError("xpay_v2_notify_not_supported")

    def sync_order_status(self, *, order_no: str, provider_order_id: str = "") -> PaymentSyncResult | None:
        if self.local_mock_enabled:
            return self._mock_sync_order_status(provider_order_id=provider_order_id)
        self._ensure_enabled()
        if not provider_order_id:
            return None
        rsp = self._get_json(f"/pay/state/{provider_order_id}")
        if rsp.get("success") is not True:
            return PaymentSyncResult(status="pending", paid=False, transaction_id="", provider_order_id=provider_order_id, raw=rsp)
        state = int(rsp.get("result") or 0)
        if state in {1, 3}:
            return PaymentSyncResult(status="paid", paid=True, transaction_id=provider_order_id, provider_order_id=provider_order_id, raw=rsp)
        if state == 2:
            return PaymentSyncResult(status="failed", paid=False, transaction_id="", provider_order_id=provider_order_id, raw=rsp)
        if state == 4:
            return PaymentSyncResult(status="scanned", paid=False, transaction_id="", provider_order_id=provider_order_id, raw=rsp)
        return PaymentSyncResult(status="pending", paid=False, transaction_id="", provider_order_id=provider_order_id, raw=rsp)

    def refund(self, *, order_no: str, provider_order_id: str = "") -> Dict[str, Any]:
        return {"ok": True, "order_no": order_no, "provider_order_id": provider_order_id, "noop": True}
