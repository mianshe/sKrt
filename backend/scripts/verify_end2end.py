import io
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.main import app

ORAL_FILLERS = ("就是说", "然后呢", "其实", "这个", "那个", "嗯", "啊")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _assert_summary_payload(label: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    required_keys = ("highlights", "conclusions", "actions", "citations")
    for key in required_keys:
        _assert(key in payload, f"{label}: missing key `{key}`")
        _assert(isinstance(payload.get(key), list), f"{label}: `{key}` should be a list")
        _assert(len(payload.get(key, [])) >= 1, f"{label}: `{key}` should not be empty")

    for key in ("highlights", "conclusions", "actions"):
        for idx, item in enumerate(payload.get(key, [])):
            _assert(isinstance(item, str), f"{label}: `{key}[{idx}]` should be string")
            text = item.strip()
            _assert(bool(text), f"{label}: `{key}[{idx}]` should not be blank")
            _assert("\n" not in text, f"{label}: `{key}[{idx}]` should be single line")
            _assert("```" not in text, f"{label}: `{key}[{idx}]` should not contain markdown fences")
            _assert(len(text) <= 140, f"{label}: `{key}[{idx}]` too long ({len(text)} chars)")

    oral_filler_hits = 0
    for key in ("highlights", "conclusions", "actions"):
        for item in payload.get(key, []):
            oral_filler_hits += sum(1 for filler in ORAL_FILLERS if filler in item)
    _assert(oral_filler_hits <= 2, f"{label}: too many colloquial fillers ({oral_filler_hits})")

    citations = payload.get("citations", [])
    for idx, item in enumerate(citations):
        _assert(isinstance(item, dict), f"{label}: `citations[{idx}]` should be object")
        _assert(bool(str(item.get("title", "")).strip()), f"{label}: `citations[{idx}].title` should not be blank")
        _assert(
            bool(str(item.get("discipline", "")).strip()),
            f"{label}: `citations[{idx}].discipline` should not be blank",
        )
        _assert(
            bool(str(item.get("section_path", "")).strip()),
            f"{label}: `citations[{idx}].section_path` should not be blank",
        )
    return payload


def main() -> None:
    report: Dict[str, Any] = {"steps": []}
    uploaded_doc_id = None

    with TestClient(app) as client:
        health = client.get("/health")
        _assert(health.status_code == 200, "health check failed")
        report["steps"].append({"health": "ok"})

        sample_text = (
            "内部审计流程要点：风险识别、控制测试、整改跟踪。"
            "在跨学科场景下可结合项目管理里程碑与技术接口稳定性评估。"
        )
        files = [("files", ("e2e-audit-note.txt", io.BytesIO(sample_text.encode("utf-8")), "text/plain"))]
        upload = client.post("/upload?discipline=audit&document_type=academic", files=files)
        _assert(upload.status_code == 200, f"upload failed: {upload.text}")
        uploaded = upload.json().get("uploaded", [])
        _assert(len(uploaded) >= 1, "upload returned empty result")
        uploaded_doc_id = int(uploaded[0]["document_id"])
        report["steps"].append({"upload": "ok", "document_id": uploaded_doc_id})

        chat = client.post("/chat", json={"query": "给我内部审计执行建议", "discipline": "all", "mode": "free"})
        _assert(chat.status_code == 200, f"chat failed: {chat.text}")
        chat_data = chat.json()
        _assert("answer" in chat_data and chat_data["answer"], "chat answer missing")
        _assert(isinstance(chat_data.get("sources"), list), "chat sources missing")
        report["steps"].append({"chat": "ok", "source_count": len(chat_data.get("sources", []))})

        summary = client.post("/insights/summary", json={"query": "内部审计执行建议", "discipline": "all"})
        _assert(summary.status_code == 200, f"summary failed: {summary.text}")
        summary_data = _assert_summary_payload("summary-hit-1", summary.json())
        _assert("fallback" in summary_data, "summary-hit-1: missing fallback flag")
        _assert("provider" in summary_data and summary_data.get("provider"), "summary-hit-1: missing provider")
        _assert(
            any(c.get("title") != "基于当前检索未命中" for c in summary_data.get("citations", [])),
            "summary-hit-1: should include matched citations",
        )

        summary_repeat = client.post("/insights/summary", json={"query": "内部审计执行建议", "discipline": "all"})
        _assert(summary_repeat.status_code == 200, f"summary repeat failed: {summary_repeat.text}")
        summary_data_repeat = _assert_summary_payload("summary-hit-2", summary_repeat.json())
        _assert(
            set(summary_data.keys()) == set(summary_data_repeat.keys()),
            "summary hit payload keys are unstable between two calls",
        )
        report["steps"].append(
            {
                "summary_hit": "ok",
                "stability_check": "ok",
                "fallback": bool(summary_data.get("fallback")),
                "citation_count": len(summary_data.get("citations", [])),
                "repeat_citation_count": len(summary_data_repeat.get("citations", [])),
            }
        )

        graph = client.get("/knowledge-graph")
        _assert(graph.status_code == 200, f"knowledge graph failed: {graph.text}")
        graph_data = graph.json()
        _assert(isinstance(graph_data.get("insights"), list), "knowledge graph insights missing")
        report["steps"].append({"knowledge_graph": "ok", "insight_count": len(graph_data.get("insights", []))})

        miss = client.post("/insights/summary", json={"query": "不存在检索命中的冷门话题", "discipline": "astronomy"})
        _assert(miss.status_code == 200, f"summary fallback failed: {miss.text}")
        miss_data = _assert_summary_payload("summary-miss", miss.json())
        _assert(bool(miss_data.get("fallback")), "fallback should be true for miss case")
        _assert(
            any(c.get("title") == "基于当前检索未命中" for c in miss_data.get("citations", [])),
            "summary-miss: should preserve miss fallback citation",
        )
        report["steps"].append({"summary_miss": "ok", "fallback_provider": miss_data.get("provider", "unknown")})

        if uploaded_doc_id is not None:
            cleanup = client.delete(f"/documents/{uploaded_doc_id}")
            _assert(cleanup.status_code == 200, f"cleanup failed: {cleanup.text}")
            report["steps"].append({"cleanup": "ok"})

    print("E2E verify passed")
    print(report)


if __name__ == "__main__":
    main()
