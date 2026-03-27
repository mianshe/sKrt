import re
from typing import Dict, List, Tuple


_PAGE_MARKER_RE = re.compile(r"\[\[PAGE:(\d+)\]\]")

# ── 分块内容分类常量 ─────────────────────────────────────────────────────────

_FILLER_SECTION_KEYWORDS = {
    "前言", "序言", "序", "版权", "出版", "编委", "目录", "致谢", "后记",
    "附录", "参考文献", "preface", "copyright", "foreword",
    "acknowledgment", "table-of-contents", "references", "appendix",
}

_FILLER_CONTENT_KEYWORDS = [
    "本书", "编者", "出版社", "出版", "版次", "印刷", "isbn",
    "审定", "编写", "主编", "特此感谢", "在此致谢", "承蒙", "鸣谢",
    "本教材", "教学大纲",
]

_EXAMPLE_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:"
    r"例\s*\d|例题|习题|练习|思考题|作业|课后题|"
    r"【例】|【题】|【练习】|【思考】|"
    r"(?:第?\d+[\.、\)\）]?\s*题)|"
    r"(?:Exercise|Example|Problem|Question)\s*\d"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

_SOLUTION_KEYWORDS = re.compile(r"(?:^|\n)\s*(?:解[：:]?|证明[：:]?|解答[：:]?|Solution)", re.MULTILINE)
_MATH_EXPR = re.compile(r"[=≥≤∑∫±×÷]|\d{2,}")
_DEFINITION_MARKERS = re.compile(r"[：:]|是指|定义为|称为|叫做")


class DocumentChunker:
    def __init__(self, chunk_size: int = 900, overlap: int = 180) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    # ------------------------------------------------------------------
    # Page-marker helpers
    # ------------------------------------------------------------------

    def _split_by_page_markers(self, text: str) -> List[Tuple[int, str]]:
        """把带 [[PAGE:N]] 标记的文本拆成 [(page_num, segment), ...] 列表。
        段落保留原始内容（已去除标记行）。page_num=0 表示标记前的文本。
        """
        segments: List[Tuple[int, str]] = []
        current_page = 0
        current_parts: List[str] = []
        for line in text.split("\n"):
            m = _PAGE_MARKER_RE.match(line.strip())
            if m:
                if current_parts:
                    segments.append((current_page, "\n".join(current_parts)))
                    current_parts = []
                current_page = int(m.group(1))
            else:
                current_parts.append(line)
        if current_parts:
            segments.append((current_page, "\n".join(current_parts)))
        return segments

    def _has_page_markers(self, text: str) -> bool:
        return "[[PAGE:" in text

    def chunk_document(self, text: str, document_type: str, title: str = "") -> List[Dict]:
        # ── 预处理：如果文本带 [[PAGE:N]] 标记则按页分段 ──────────────
        if self._has_page_markers(text):
            return self._chunk_with_pages(text, document_type, title)

        normalized = self._normalize(text)
        if not normalized:
            return []

        if document_type == "exam":
            sections = self._chunk_exam(normalized)
        elif document_type == "technical":
            sections = self._chunk_technical(normalized)
        elif document_type == "project":
            sections = self._chunk_project(normalized)
        else:
            sections = self._chunk_academic(normalized)

        chunks = []
        for i, section in enumerate(sections):
            path = section.get("section_path", f"section/{i+1}")
            for w_idx, window_text in enumerate(self._semantic_windows(section["content"])):
                chunks.append(
                    {
                        "chunk_id": f"{i+1}-{w_idx+1}",
                        "title": title,
                        "content": window_text,
                        "section_path": path,
                        "document_type": document_type,
                        "page_num": 0,
                        "chunk_type": self._classify_chunk(window_text, path, document_type),
                    }
                )
        return chunks

    def _chunk_with_pages(self, text: str, document_type: str, title: str) -> List[Dict]:
        """按页标记先分页，再在每页内做语义切块，chunk 携带 page_num。"""
        page_segments = self._split_by_page_markers(text)
        chunks: List[Dict] = []
        chunk_seq = 0
        for page_num, seg_text in page_segments:
            normalized = self._normalize(seg_text)
            if not normalized:
                continue
            if document_type == "exam":
                sections = self._chunk_exam(normalized)
            elif document_type == "technical":
                sections = self._chunk_technical(normalized)
            elif document_type == "project":
                sections = self._chunk_project(normalized)
            else:
                sections = self._chunk_academic(normalized)
            for i, section in enumerate(sections):
                path = section.get("section_path", f"page{page_num}/section/{i+1}")
                for w_idx, window_text in enumerate(self._semantic_windows(section["content"])):
                    chunk_seq += 1
                    chunks.append(
                        {
                            "chunk_id": f"{chunk_seq}-{w_idx+1}",
                            "title": title,
                            "content": window_text,
                            "section_path": path,
                            "document_type": document_type,
                            "page_num": page_num,
                            "chunk_type": self._classify_chunk(window_text, path, document_type),
                        }
                    )
        return chunks

    def _normalize(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _semantic_windows(self, text: str) -> List[str]:
        if len(text) <= self.chunk_size:
            return [text]
        windows: List[str] = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            window = text[start:end]
            split_at = max(window.rfind("\n"), window.rfind("。"), window.rfind("."))
            if split_at > int(self.chunk_size * 0.6):
                end = start + split_at + 1
                window = text[start:end]
            windows.append(window.strip())
            if end >= len(text):
                break
            start = max(0, end - self.overlap)
        return [w for w in windows if w]

    def _chunk_academic(self, text: str) -> List[Dict]:
        heading_pattern = re.compile(
            r"(?im)^(abstract|introduction|related work|method(?:ology)?|experiment(?:s)?|result(?:s)?|discussion|conclusion|references|摘要|引言|方法|实验|结果|结论)\b.*$"
        )
        return self._split_by_heading(text, heading_pattern, default_prefix="academic")

    def _chunk_exam(self, text: str) -> List[Dict]:
        blocks = re.split(r"(?m)^\s*(?:第?\d+[题、\.\)]|Q\d+[\.\):]|【题目】)", text)
        sections: List[Dict] = []
        for i, block in enumerate(blocks):
            block = block.strip()
            if not block:
                continue
            sections.append({"section_path": f"exam/question/{i+1}", "content": block})
        if not sections:
            sections = [{"section_path": "exam/overall", "content": text}]
        return sections

    def _chunk_technical(self, text: str) -> List[Dict]:
        heading_pattern = re.compile(
            r"(?im)^(overview|architecture|api|endpoint|installation|configuration|usage|example|troubleshooting|概述|架构|接口|安装|配置|示例)\b.*$"
        )
        return self._split_by_heading(text, heading_pattern, default_prefix="technical")

    def _chunk_project(self, text: str) -> List[Dict]:
        heading_pattern = re.compile(
            r"(?im)^(goal|scope|timeline|milestone|task|risk|owner|status|目标|范围|里程碑|任务|风险|负责人|进度)\b.*$"
        )
        return self._split_by_heading(text, heading_pattern, default_prefix="project")

    def _split_by_heading(self, text: str, pattern: re.Pattern, default_prefix: str) -> List[Dict]:
        lines = text.split("\n")
        sections: List[Dict] = []
        current_heading = f"{default_prefix}/intro"
        buffer: List[str] = []

        def flush() -> None:
            if buffer:
                sections.append({"section_path": current_heading, "content": "\n".join(buffer).strip()})

        for line in lines:
            if pattern.match(line.strip()):
                flush()
                current_heading = f"{default_prefix}/{self._slug(line.strip())}"
                buffer = [line]
            else:
                buffer.append(line)
        flush()
        return [s for s in sections if s["content"]]

    def _slug(self, value: str) -> str:
        value = value.lower()
        value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
        return value.strip("-")[:80] or "section"

    @staticmethod
    def _classify_chunk(content: str, section_path: str, document_type: str) -> str:
        """将 chunk 分类为 knowledge / example / filler。"""
        sp_lower = section_path.lower()
        content_lower = content.lower()

        # ── Filler 检测 ──────────────────────────────────────────────
        # 规则 A: section_path 命中废话章节
        for kw in _FILLER_SECTION_KEYWORDS:
            if kw in sp_lower:
                return "filler"

        # 规则 B: 内容前 200 字命中 2+ 出版/编写关键词
        head = content_lower[:200]
        filler_hits = sum(1 for kw in _FILLER_CONTENT_KEYWORDS if kw in head)
        if filler_hits >= 2:
            return "filler"

        # 规则 C: 信息密度过低（短文本、无数学/定义标记、也非例题）
        stripped = content.strip()
        if (len(stripped) < 60
                and not _MATH_EXPR.search(stripped)
                and not _DEFINITION_MARKERS.search(stripped)
                and not _EXAMPLE_PATTERN.search(content)):
            return "filler"

        # ── exam 文档通过 filler 筛选后全判 example ──────────────────
        if document_type == "exam":
            return "example"

        # ── Example 检测 ─────────────────────────────────────────────
        # 规则 D: section_path 含题目路径
        if "exam/question" in sp_lower:
            return "example"

        # 规则 E: 题目标记正则
        if _EXAMPLE_PATTERN.search(content):
            return "example"

        # 规则 F: 解题模式（解/证明 + 数学表达式 + 足够长度）
        if (_SOLUTION_KEYWORDS.search(content)
                and _MATH_EXPR.search(content)
                and len(stripped) > 80):
            return "example"

        return "knowledge"
