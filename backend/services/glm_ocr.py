from __future__ import annotations

import base64
import io
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import httpx

from .text_cleanup import strip_layout_noise

try:
    import pypdfium2 as pdfium  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pdfium = None  # type: ignore


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


def _safe_int(raw: str, default: int, *, min_value: int, max_value: int) -> int:
    try:
        value = int(str(raw).strip())
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


def _glm_ocr_retry_max() -> int:
    raw = (os.getenv("GLM_OCR_RETRY_MAX") or "2").strip() or "2"
    return _safe_int(raw, 2, min_value=0, max_value=8)


def _glm_ocr_retry_base_delay_ms() -> int:
    raw = (os.getenv("GLM_OCR_RETRY_BASE_DELAY_MS") or "800").strip() or "800"
    return _safe_int(raw, 800, min_value=0, max_value=60_000)


def _glm_ocr_retry_jitter_ms() -> int:
    raw = (os.getenv("GLM_OCR_RETRY_JITTER_MS") or "200").strip() or "200"
    return _safe_int(raw, 200, min_value=0, max_value=10_000)


def _glm_ocr_page_retry_max() -> int:
    raw = (os.getenv("GLM_OCR_PAGE_RETRY_MAX") or "2").strip() or "2"
    return _safe_int(raw, 2, min_value=0, max_value=8)


def _glm_ocr_allow_partial_pages() -> bool:
    raw = (os.getenv("GLM_OCR_ALLOW_PARTIAL_PAGES") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _is_transient_glm_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    msg = str(exc).lower()
    # Our own errors include "glm_ocr_request_failed:http-<status>:..."
    for code in ("http-429", "http-500", "http-501", "http-502", "http-503", "http-504"):
        if code in msg:
            return True
    return False


def _sleep_backoff(attempt: int) -> None:
    base_ms = _glm_ocr_retry_base_delay_ms()
    if base_ms <= 0:
        return
    jitter_ms = _glm_ocr_retry_jitter_ms()
    exp = max(0, int(attempt) - 1)
    delay_ms = int(base_ms * (2**exp))
    if jitter_ms > 0:
        delay_ms += int(random.uniform(0, float(jitter_ms)))
    time.sleep(max(0.0, delay_ms / 1000.0))


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


def _response_detail(resp: httpx.Response) -> str:
    try:
        text = (resp.text or "").strip()
    except Exception:
        text = ""
    if not text:
        return "empty-response"
    text = " ".join(text.split())
    return text[:600]


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


def _extract_text_from_response_data(data: Dict[str, Any]) -> str:
    text = str(data.get("md_results") or "").strip()
    if text:
        return strip_layout_noise(text)
    return strip_layout_noise(_flatten_layout_text(data.get("layout_details")))


def _request_layout_parsing(
    client: httpx.Client,
    *,
    url: str,
    headers: Dict[str, str],
    model: str,
    file_data_uri: str,
) -> Dict[str, Any]:
    payload = {
        "model": model,
        "file": file_data_uri,
        "need_layout_visualization": False,
        "return_crop_images": False,
    }
    response = client.post(url, headers=headers, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(
            f"glm_ocr_request_failed:http-{response.status_code}:{_response_detail(response)}"
        )
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"glm_ocr_request_failed:bad-json:{exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("glm_ocr_request_failed:unexpected-response-shape")
    return data


def _iter_pdf_page_data_uris(path: Path) -> Iterable[tuple[int, str]]:
    if pdfium is None:
        raise RuntimeError("glm_ocr_page_fallback_unavailable:pypdfium2-not-installed")
    dpi = _safe_int(os.getenv("GLM_OCR_PAGE_DPI", "144"), 144, min_value=96, max_value=300)
    max_pages = _safe_int(os.getenv("GLM_OCR_MAX_PAGES", "0"), 0, min_value=0, max_value=5000)
    quality = _safe_int(os.getenv("GLM_OCR_PAGE_JPEG_QUALITY", "85"), 85, min_value=40, max_value=95)
    scale = max(1.0, float(dpi) / 72.0)

    document = pdfium.PdfDocument(str(path))
    try:
        total = len(document)
        limit = min(total, max_pages) if max_pages > 0 else total
        for idx in range(limit):
            page = document[idx]
            try:
                rendered = page.render(scale=scale)
                image = rendered.to_pil()
            finally:
                page.close()

            try:
                buf = io.BytesIO()
                image.convert("RGB").save(buf, format="JPEG", quality=quality)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                yield idx + 1, f"data:image/jpeg;base64,{b64}"
            finally:
                try:
                    image.close()
                except Exception:
                    pass
    finally:
        document.close()


def _should_use_pdf_page_fallback(path: Path, error_message: str) -> bool:
    if path.suffix.lower() != ".pdf":
        return False
    enabled = (os.getenv("GLM_OCR_PDF_PAGE_FALLBACK_ON_400") or "1").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return False
    msg = error_message.lower()
    return (
        ("http-400" in msg)
        or ("http-413" in msg)
        or ("write operation timed out" in msg)
        or ("read operation timed out" in msg)
        or ("read timeout" in msg)
    )


def _should_use_pdf_page_mode_by_file_size(path: Path) -> bool:
    if path.suffix.lower() != ".pdf":
        return False
    threshold_mb = _safe_int(
        os.getenv("GLM_OCR_DIRECT_PAGE_MODE_MIN_MB", "25"),
        25,
        min_value=1,
        max_value=1024,
    )
    try:
        size_bytes = int(path.stat().st_size)
    except Exception:
        return False
    return size_bytes >= threshold_mb * 1024 * 1024


def _run_pdf_page_fallback(
    client: httpx.Client,
    *,
    url: str,
    headers: Dict[str, str],
    model: str,
    path: Path,
) -> GlmOcrResult:
    page_delay_ms = _safe_int(os.getenv("GLM_OCR_PAGE_DELAY_MS", "0"), 0, min_value=0, max_value=10_000)
    page_retry_max = _glm_ocr_page_retry_max()
    allow_partial = _glm_ocr_allow_partial_pages()
    parts: List[str] = []
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    request_ids: List[str] = []
    page_count = 0
    failed_pages = 0

    for page_num, data_uri in _iter_pdf_page_data_uris(path):
        page_count += 1
        if page_delay_ms > 0 and page_count > 1:
            time.sleep(page_delay_ms / 1000.0)
        data: Dict[str, Any] | None = None
        for attempt in range(1, page_retry_max + 2):
            try:
                data = _request_layout_parsing(client, url=url, headers=headers, model=model, file_data_uri=data_uri)
                break
            except Exception as exc:
                if attempt <= page_retry_max and _is_transient_glm_error(exc):
                    _sleep_backoff(attempt)
                    continue
                if allow_partial:
                    failed_pages += 1
                    data = None
                    break
                raise
        if data is None:
            continue
        text = _extract_text_from_response_data(data)
        if text:
            parts.append(f"[[PAGE:{page_num}]]")
            parts.append(text)

        usage = data.get("usage") if isinstance(data, dict) else {}
        if isinstance(usage, dict):
            total_tokens += int(usage.get("total_tokens") or 0)
            prompt_tokens += int(usage.get("prompt_tokens") or 0)
            completion_tokens += int(usage.get("completion_tokens") or 0)
        req_id = str(data.get("request_id") or "").strip()
        if req_id:
            request_ids.append(req_id)

    merged_text = "\n".join(parts).strip()
    if not merged_text:
        raise RuntimeError("glm_ocr_empty_result:page-fallback")
    if failed_pages > 0:
        merged_text = f"[[OCR_WARN:failed_pages={failed_pages}]]\n" + merged_text

    return GlmOcrResult(
        text=merged_text,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        page_count=page_count,
        model=model,
        request_id=",".join(request_ids[:5]),
        layout_details=[],
    )


def run_glm_layout_parsing(file_path: str) -> GlmOcrResult:
    token = os.getenv("ZHIPU_API_KEY", "").strip()
    if not token:
        raise RuntimeError("ZHIPU_API_KEY not configured")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(file_path)

    model = (os.getenv("GLM_OCR_MODEL") or "glm-ocr").strip() or "glm-ocr"
    url = f"{_zhipu_base()}/layout_parsing"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=_glm_ocr_timeout_seconds()) as client:
        if _should_use_pdf_page_mode_by_file_size(path):
            return _run_pdf_page_fallback(
                client,
                url=url,
                headers=headers,
                model=model,
                path=path,
            )
        max_retry = _glm_ocr_retry_max()
        attempt = 0
        while True:
            attempt += 1
            try:
                data = _request_layout_parsing(
                    client,
                    url=url,
                    headers=headers,
                    model=model,
                    file_data_uri=_read_file_as_data_uri(path),
                )
                break
            except Exception as exc:
                err = str(exc)
                if _should_use_pdf_page_fallback(path, err):
                    return _run_pdf_page_fallback(
                        client,
                        url=url,
                        headers=headers,
                        model=model,
                        path=path,
                    )
                if attempt <= max_retry and _is_transient_glm_error(exc):
                    _sleep_backoff(attempt)
                    continue
                raise RuntimeError(f"glm_ocr_request_failed: {err}") from exc

    usage = data.get("usage") if isinstance(data, dict) else {}
    data_info = data.get("data_info") if isinstance(data, dict) else {}
    layout_details = data.get("layout_details") if isinstance(data, dict) else []
    text = _extract_text_from_response_data(data)
    if not text:
        raise RuntimeError("glm_ocr_empty_result")

    return GlmOcrResult(
        text=text,
        total_tokens=int(usage.get("total_tokens") or 0) if isinstance(usage, dict) else 0,
        prompt_tokens=int(usage.get("prompt_tokens") or 0) if isinstance(usage, dict) else 0,
        completion_tokens=int(usage.get("completion_tokens") or 0) if isinstance(usage, dict) else 0,
        page_count=int(data_info.get("num_pages") or 0) if isinstance(data_info, dict) else 0,
        model=str(data.get("model") or model),
        request_id=str(data.get("request_id") or ""),
        layout_details=layout_details if isinstance(layout_details, list) else [],
    )
