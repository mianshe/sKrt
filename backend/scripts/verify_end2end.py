import io
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.main import app


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _assert_report_payload(label: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    _assert(isinstance(payload.get("report"), str), f"{label}: `report` should be string")
    _assert(bool(str(payload.get("report", "")).strip()), f"{label}: `report` should not be blank")
    _assert(isinstance(payload.get("sections"), list), f"{label}: `sections` should be a list")
    _assert(len(payload.get("sections", [])) >= 1, f"{label}: `sections` should not be empty")
    _assert(isinstance(payload.get("citations"), list), f"{label}: `citations` should be a list")
    _assert(len(payload.get("citations", [])) >= 1, f"{label}: `citations` should not be empty")

    for idx, item in enumerate(payload.get("sections", [])):
        _assert(isinstance(item, dict), f"{label}: `sections[{idx}]` should be object")
        _assert(bool(str(item.get("title", "")).strip()), f"{label}: `sections[{idx}].title` should not be blank")
        _assert(bool(str(item.get("content", "")).strip()), f"{label}: `sections[{idx}].content` should not be blank")

    for idx, item in enumerate(payload.get("citations", [])):
        _assert(isinstance(item, dict), f"{label}: `citations[{idx}]` should be object")
        _assert(bool(str(item.get("title", "")).strip()), f"{label}: `citations[{idx}].title` should not be blank")
        _assert(bool(str(item.get("discipline", "")).strip()), f"{label}: `citations[{idx}].discipline` should not be blank")
        _assert(bool(str(item.get("section_path", "")).strip()), f"{label}: `citations[{idx}].section_path` should not be blank")
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

        deep_report = client.post(
            "/insights/report",
            json={"query": "请对该资料做深度分析", "discipline": "all", "document_id": uploaded_doc_id},
        )
        _assert(deep_report.status_code == 200, f"report failed: {deep_report.text}")
        report_data = _assert_report_payload("report-hit-1", deep_report.json())
        _assert("fallback" not in report_data or isinstance(report_data.get("fallback"), bool), "report-hit-1: invalid fallback field")
        _assert("provider" in report_data and report_data.get("provider"), "report-hit-1: missing provider")
        report["steps"].append(
            {
                "report_hit": "ok",
                "section_count": len(report_data.get("sections", [])),
                "citation_count": len(report_data.get("citations", [])),
            }
        )

        deep_report_repeat = client.post(
            "/insights/report",
            json={"query": "请对该资料做深度分析", "discipline": "all", "document_id": uploaded_doc_id},
        )
        _assert(deep_report_repeat.status_code == 200, f"report repeat failed: {deep_report_repeat.text}")
        report_data_repeat = _assert_report_payload("report-hit-2", deep_report_repeat.json())
        _assert(
            set(report_data.keys()) == set(report_data_repeat.keys()),
            "report hit payload keys are unstable between two calls",
        )
        report["steps"].append(
            {
                "report_repeat": "ok",
                "repeat_section_count": len(report_data_repeat.get("sections", [])),
                "repeat_citation_count": len(report_data_repeat.get("citations", [])),
            }
        )

        graph = client.get("/knowledge-graph")
        _assert(graph.status_code == 200, f"knowledge graph failed: {graph.text}")
        graph_data = graph.json()
        _assert(isinstance(graph_data.get("insights"), list), "knowledge graph insights missing")
        report["steps"].append({"knowledge_graph": "ok", "insight_count": len(graph_data.get("insights", []))})

        if uploaded_doc_id is not None:
            cleanup = client.delete(f"/documents/{uploaded_doc_id}")
            _assert(cleanup.status_code == 200, f"cleanup failed: {cleanup.text}")
            report["steps"].append({"cleanup": "ok"})

    print("E2E verify passed")
    print(report)


if __name__ == "__main__":
    main()
