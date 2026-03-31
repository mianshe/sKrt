from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


@dataclass
class GlmOcrResult:
    text: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    page_count: int
    model: str
    request_id: str
    layout_details: List[Any]


def glm_ocr_enabled() -> bool:
    return bool(os.getenv("ZHIPU_API_KEY", "").strip())


def _zhipu_base() -> str:
    return (os.getenv("ZHIPU_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")


def _glm_ocr_timeout_seconds() -> float:
    raw = (os.getenv("GLM_OCR_TIMEOUT_SEC") or "180").strip() or "180"
    try:
        value = float(raw)
    except ValueError:
        value = 180.0
    return max(30.0, min(900.0, value))


def _guess_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    return "application/octet-stream"


def _read_file_as_data_uri(path: Path) -> str:
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{_guess_media_type(path)};base64,{b64}"


def _flatten_layout_text(layout_details: Any) -> str:
    parts: List[str] = []
    if not isinstance(layout_details, list):
        return ""
    for page in layout_details:
        if not isinstance(page, list):
            continue
        for item in page:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if content:
                parts.append(content)
    return "\n".join(parts).strip()


def run_glm_layout_parsing(file_path: str) -> GlmOcrResult:
    token = os.getenv("ZHIPU_API_KEY", "").strip()
    if not token:
        raise RuntimeError("ZHIPU_API_KEY not configured")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(file_path)

    url = f"{_zhipu_base()}/layout_parsing"
    payload = {
        "model": "glm-ocr",
        "file": _read_file_as_data_uri(path),
        "need_layout_visualization": False,
        "return_crop_images": False,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=_glm_ocr_timeout_seconds()) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data: Dict[str, Any] = response.json()
    except Exception as exc:
        raise RuntimeError(f"glm_ocr_request_failed: {exc}") from exc

    usage = data.get("usage") if isinstance(data, dict) else {}
    data_info = data.get("data_info") if isinstance(data, dict) else {}
    layout_details = data.get("layout_details") if isinstance(data, dict) else []
    text = str(data.get("md_results") or "").strip()
    if not text:
        text = _flatten_layout_text(layout_details)
    if not text:
        raise RuntimeError("glm_ocr_empty_result")

    return GlmOcrResult(
        text=text,
        total_tokens=int(usage.get("total_tokens") or 0),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        page_count=int(data_info.get("num_pages") or 0),
        model=str(data.get("model") or "glm-ocr"),
        request_id=str(data.get("request_id") or ""),
        layout_details=layout_details if isinstance(layout_details, list) else [],
    )
