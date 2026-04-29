import json
import re
from typing import Any, Dict, List, TypedDict


class GraphState(TypedDict, total=False):
    tenant_id: str
    query: str
    mode: str
    embedding_mode: str
    discipline: str
    document_id: int
    summary_compact_level: int
    summary_mode: str
    doc_text: str
    document_type: str
    chunks: List[Dict[str, str]]
    retrieved: List[Dict[str, Any]]
    focus_blocks: List[str]
    compressed_context: str
    internal_reasoning: Dict[str, Any]
    evidence: List[Dict[str, str]]
    answer: str
    brief_reasoning: List[str]
    summary: Dict[str, Any]
    report: str
    report_sections: List[Dict[str, str]]
    report_profile: Dict[str, Any]
    document_tree: List[Dict[str, Any]]
    chapter_summaries: List[Dict[str, Any]]
    answer_strategy: Dict[str, str]
    qa_regression_gates: Dict[str, Any]
    quality_gates: Dict[str, Any]
    fallback_reason: str
    provider: str
    cost_profile: Dict[str, Any]
    coverage_stats: Dict[str, Any]
    agent_trace: List[str]
    five_dimensions: Dict[str, Any]
    five_dimensions_meta: Dict[str, Any]
    question_type: str
    question_context: str
    top_k: int


def parse_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    parsed = _safe_json(raw)
    if isinstance(parsed, dict):
        return parsed
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 2:
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            parsed = _safe_json("\n".join(lines).strip())
            if isinstance(parsed, dict):
                return parsed
    return {}


def sanitize_answer(value: Any, max_len: int = 800) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    leakage_flags = ["chain of thought", "let's think step by step", "逐步推理", "完整推理", "内部思考"]
    if any(flag in lowered for flag in leakage_flags):
        return "已根据证据生成结论，为避免泄露完整推理链，仅保留答案与简版思路。"
    return text[:max_len]


def sanitize_brief_reasoning(value: Any, max_items: int = 3) -> List[str]:
    if isinstance(value, list):
        items = [str(x).strip() for x in value]
    elif isinstance(value, str):
        items = [x.strip() for x in re.split(r"[\n;；]+", value)]
    else:
        items = []
    out: List[str] = []
    for item in items:
        if not item:
            continue
        lowered = item.lower()
        if any(flag in lowered for flag in ["chain of thought", "let's think step by step", "逐步推理", "完整推理"]):
            continue
        out.append(item[:120])
        if len(out) >= max_items:
            break
    if not out:
        out = ["已基于检索证据完成信息压缩并给出可验证结论。"]
    return out


def normalize_evidence(items: List[Dict[str, Any]], limit: int = 6) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for row in items:
        item = {
            "title": str(row.get("title", "未命名来源")).strip() or "未命名来源",
            "discipline": str(row.get("discipline", "all")).strip() or "all",
            "section_path": str(row.get("section_path", "N/A")).strip() or "N/A",
            "document_type": str(row.get("document_type", "")).strip(),
        }
        key = (item["title"], item["discipline"], item["section_path"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def build_reasoning_gates(answer: str, brief_reasoning: List[str], evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    consistency = bool((answer or "").strip())
    evidence_traceable = bool(evidence) and all(
        str(item.get("title", "")).strip() and str(item.get("section_path", "")).strip() for item in evidence
    )
    reasoning_visibility = 1 <= len(brief_reasoning) <= 3 and all(
        "逐步推理" not in x and "完整推理" not in x and "chain of thought" not in x.lower() for x in brief_reasoning
    )
    failed_checks: List[str] = []
    if not consistency:
        failed_checks.append("consistency")
    if not evidence_traceable:
        failed_checks.append("evidence_traceable")
    if not reasoning_visibility:
        failed_checks.append("reasoning_visibility")
    return {
        "consistency": consistency,
        "evidence_traceable": evidence_traceable,
        "reasoning_visibility": reasoning_visibility,
        "passed": len(failed_checks) == 0,
        "failed_checks": failed_checks,
    }


def _safe_json(value: str) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return None
