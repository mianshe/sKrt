from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


def _mask(value: str, keep_start: int = 4, keep_end: int = 3) -> str:
    v = (value or "").strip()
    if len(v) <= keep_start + keep_end:
        return "*" * len(v)
    return f"{v[:keep_start]}***{v[-keep_end:]}"


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=True)

    api_key = (os.getenv("BAIDU_OCR_API_KEY") or "").strip()
    secret = (os.getenv("BAIDU_OCR_SECRET_KEY") or "").strip()

    print(f"BAIDU_OCR_API_KEY={_mask(api_key)} len={len(api_key)}")
    print(f"BAIDU_OCR_SECRET_KEY={_mask(secret)} len={len(secret)}")

    if not api_key or not secret:
        print("FAIL: missing BAIDU_OCR_API_KEY or BAIDU_OCR_SECRET_KEY")
        return 2

    url = "https://aip.baidubce.com/oauth/2.0/token"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": api_key,
                    "client_secret": secret,
                },
            )
    except Exception as exc:
        print(f"FAIL: request error: {exc}")
        return 3

    print(f"HTTP {resp.status_code}")
    text = (resp.text or "").strip()
    if not text:
        print("FAIL: empty response body")
        return 4

    try:
        payload = resp.json()
    except Exception:
        print(f"FAIL: non-json response: {text[:500]}")
        return 5

    if resp.status_code == 200 and isinstance(payload, dict) and payload.get("access_token"):
        print("OK: token fetched")
        print(f"expires_in={payload.get('expires_in')}")
        print(f"scope={payload.get('scope')}")
        return 0

    print("FAIL: token not issued")
    print(json.dumps(payload, ensure_ascii=False)[:1000])
    return 6


if __name__ == "__main__":
    raise SystemExit(main())
