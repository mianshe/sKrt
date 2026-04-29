import re

_GLM_LAYOUT_IMAGE_RE = re.compile(
    r"!\[[^\]]*]\(\s*PAGE\s*=\s*\d+\s*,\s*BBOX\s*=\s*\[[^\]]*]\s*\)",
    flags=re.IGNORECASE,
)
_GLM_LAYOUT_BARE_RE = re.compile(
    r"\(?\s*PAGE\s*=\s*\d+\s*,\s*BBOX\s*=\s*\[[^\]]*]\s*\)?",
    flags=re.IGNORECASE,
)
_OCR_WARN_RE = re.compile(r"^\[\[OCR_WARN:[^\]]+]]\s*$", flags=re.IGNORECASE)


def strip_layout_noise(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ""

    cleaned = _GLM_LAYOUT_IMAGE_RE.sub("", normalized)
    cleaned = _GLM_LAYOUT_BARE_RE.sub("", cleaned)

    lines = []
    for raw_line in cleaned.split("\n"):
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if _OCR_WARN_RE.match(line):
            continue
        if re.fullmatch(r"[!()\[\],=\-_.:;#/\\\s]+", line):
            continue
        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
