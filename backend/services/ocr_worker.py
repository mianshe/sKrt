"""
Child-process entry for Paddle PDF OCR. Invoked as:
  python -m backend.services.ocr_worker paddle <file_path> <dpi> <max_pages>

Must be run with cwd / PYTHONPATH such that `backend` is importable (same as uvicorn).
Stdout: a single JSON line {"ok": true, "text": "..."} or {"ok": false, "error": "..."}.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    # Prevent recursive subprocess when DocumentParser loads; child always runs in-process OCR.
    os.environ["PDF_OCR_PADDLE_SUBPROCESS"] = "0"
    # Skip Paddle model-host connectivity check in worker (faster, less stderr noise).
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "1")
    # Keep CPU execution conservative to reduce native crashes on some Windows stacks.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
    os.environ.setdefault("PADDLEOCR_ENABLE_MKLDNN", "0")

    if len(sys.argv) < 5:
        msg = json.dumps(
            {"ok": False, "error": "usage: python -m backend.services.ocr_worker paddle <file_path> <dpi> <max_pages>"},
            ensure_ascii=False,
        )
        print(msg, flush=True)
        return 1

    engine = (sys.argv[1] or "").strip().lower()
    file_path = sys.argv[2]
    try:
        dpi = int(sys.argv[3])
        max_pages = int(sys.argv[4])
    except ValueError:
        print(json.dumps({"ok": False, "error": "invalid dpi or max_pages"}, ensure_ascii=False), flush=True)
        return 1

    if engine != "paddle":
        print(json.dumps({"ok": False, "error": f"unsupported engine: {engine}"}, ensure_ascii=False), flush=True)
        return 1

    from .document_parser import DocumentParser

    parser = DocumentParser()
    txt, err = parser._ocr_pdf_pages_paddle_inprocess(file_path=file_path, dpi=dpi, max_pages=max_pages)
    if txt:
        print(json.dumps({"ok": True, "text": txt}, ensure_ascii=False), flush=True)
        return 0
    print(json.dumps({"ok": False, "error": err or "empty"}, ensure_ascii=False), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
