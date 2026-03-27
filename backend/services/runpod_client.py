from __future__ import annotations

import os
from typing import Any, Dict

import httpx


def runpod_enabled() -> bool:
    return (os.getenv("RUNPOD_ENABLED") or "0").strip().lower() in {"1", "true", "yes", "on"}


def submit_ingestion_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = (os.getenv("RUNPOD_INGEST_ENDPOINT") or "").strip()
    api_key = (os.getenv("RUNPOD_API_KEY") or "").strip()
    if not endpoint or not api_key:
        raise RuntimeError("未配置 RUNPOD_INGEST_ENDPOINT/RUNPOD_API_KEY")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout_sec = float((os.getenv("RUNPOD_TIMEOUT_SEC") or "30").strip() or "30")
    with httpx.Client(timeout=timeout_sec) as client:
        resp = client.post(endpoint, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, dict) else {"raw": data}

