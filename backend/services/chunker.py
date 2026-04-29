import re
from typing import Any, Dict, List, Optional, Tuple


_PAGE_MARKER_RE = re.compile(r"\[\[PAGE:(\d+)\]\]")

_FILLER_SECTION_KEYWORDS = {
    "前言",
    "序言",
    "序",
    "版权",
    "出版",
    "编委",
    "目录",
    "致谢",
    "后记",
    "附录",
    "参考文献",
    "preface",
    "copyright",
    "foreword",
    "acknowledgment",
    "table-of-contents",
    "references",
    "appendix",
}

_FILLER_CONTENT_KEYWORDS = [
    "本书",
    "编者",
    "出版社",
    "出版",
    "版次",
    "印刷",
    "isbn",
    "审定",
    "编写",
    "主编",
    "特此感谢",
    "在此致谢",
    "承蒙",
    "鸣谢",
    "本教材",
    "教学大纲",
]

_EXAMPLE_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:"
    r"例\s*\d|例题|习题|练习|思考题|作业|课后题|"
    r"【例】|【题】|【练习】|【思考】|"
    r"(?:第?\d+[\.、)\)]\s*题)|"
    r"(?:Exercise|Example|Problem|Question)\s*\d"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

_SOLUTION_KEYWORDS = re.compile(r"(?:^|\n)\s*(?:解[:：]?|证明[:：]?|解答[:：]?|Solution)", re.MULTILINE)
_MATH_EXPR = re.compile(r"[=≈≠<>+\-*/×÷]|\d{2,}")
_DEFINITION_MARKERS = re.compile(r"定义为|是指|称为|叫做|即|指的是")

# ============ 概念感知分块增强 ============

# 概念段类型标记
_CONCEPT_MARKERS = {
    # 定义类标记
    "definition": re.compile(
        r"(?:定义为|是指|称为|叫做|即|指的是|定义为|定义作|定义为|Definition|Definition:|define[sd]?|defined as)",
        re.IGNORECASE
    ),
    # 示例类标记  
    "example": re.compile(
        r"(?:例如|比如|举例|例\d+||例题|示例|例如|比如|Example|Example:|example[s]?|for example|such as|e\.g\.)",
        re.IGNORECASE
    ),
    # 应用类标记
    "application": re.compile(
        r"(?:应用|运用|使用|可用于|适用于|应用领域|应用场景|Application|Application:|apply|applications|usage|use case)",
        re.IGNORECASE
    ),
    # 解释类标记
    "explanation": re.compile(
        r"(?:说明|解释|阐释|意思是|意味着|可以理解为|换言之|也就是说|Explanation|Explanation:|explain[s]?|meaning|in other words)",
        re.IGNORECASE
    ),
    # 总结类标记
    "summary": re.compile(
        r"(?:总结|结论|综上|总而言之|总之|Summary|Summary:|conclusion|in conclusion|to sum up)",
        re.IGNORECASE
    ),
    # 问题类标记
    "problem": re.compile(
        r"(?:问题|疑问|难点|挑战|Question|Problem|problem[s]?|question[s]?|issue[s]?)",
        re.IGNORECASE
    ),
}

# 概念段边界检测：段落分割和上下文保持
_CONCEPT_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


class DocumentChunker:
    def __init__(self, chunk_size: int = 900, overlap: int = 180) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    def _split_by_page_markers(self, text: str) -> List[Tuple[int, str]]:
        segments: List[Tuple[int, str]] = []
        current_page = 0
        current_parts: List[str] = []
        for line in text.split("\n"):
            marker = _PAGE_MARKER_RE.match(line.strip())
            if marker:
                if current_parts:
                    segments.append((current_page, "\n".join(current_parts)))
                    current_parts = []
                current_page = int(marker.group(1))
            else:
                current_parts.append(line)
        if current_parts:
            segments.append((current_page, "\n".join(current_parts)))
        return segments

    def _has_page_markers(self, text: str) -> bool:
        return "[[PAGE:" in text

    def _chunk_markdown(self, text: str, title: str) -> List[Dict[str, Any]]:
        """
        基于 Markdown 标题层级进行切片。
        识别 #, ##, ### 等标题作为天然的逻辑分界点。
        """
        chunks = []
        lines = text.split("\n")
        current_header = title
        current_content = []
        current_page = 0
        
        for line in lines:
            # 兼容页码标记
            marker = _PAGE_MARKER_RE.match(line.strip())
            if marker:
                current_page = int(marker.group(1))
                continue

            # 识别 Markdown 标题
            if line.startswith("#"):
                # 如果当前已有内容，先存为一个块
                if current_content:
                    chunk_content = "\n".join(current_content)
                    chunks.append({
                        "chunk_id": f"md_{len(chunks)}",
                        "content": chunk_content,
                        "section_path": current_header,
                        "page_num": current_page,
                        "chunk_type": self._classify_chunk(chunk_content, current_header, "academic"),
                        "title": title,
                        "document_type": "academic"
                    })
                    current_content = []
                # 更新标题（去掉 # 号）
                current_header = line.lstrip("# ").strip()
                current_content.append(line)
            else:
                current_content.append(line)
                
            # 如果单块过大，进行强制切分（防止超出 LLM 窗口）
            if sum(len(c) for c in current_content) > self.chunk_size * 2:
                chunk_content = "\n".join(current_content)
                chunks.append({
                    "chunk_id": f"md_{len(chunks)}",
                    "content": chunk_content,
                    "section_path": current_header,
                    "page_num": current_page,
                    "chunk_type": self._classify_chunk(chunk_content, current_header, "academic"),
                    "title": title,
                    "document_type": "academic"
                })
                current_content = []

        if current_content:
            chunk_content = "\n".join(current_content)
            chunks.append({
                "chunk_id": f"md_final",
                "content": chunk_content,
                "section_path": current_header,
                "page_num": current_page,
                "chunk_type": self._classify_chunk(chunk_content, current_header, "academic"),
                "title": title,
                "document_type": "academic"
            })
            
        return chunks

    def chunk_document(
        self,
        text: str,
        document_type: str,
        title: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        concept_aware: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        分块文档文本。
        
        参数:
            text: 文档文本内容
            document_type: 文档类型 (exam, technical, project, academic)
            title: 文档标题
            metadata: 元数据字典
            concept_aware: 是否启用概念感知分块（实验性功能）
            
        返回:
            分块列表，每个分块包含chunk_id、content、section_path等字段
        """
        # 优先检测是否为 Docling 导出的 Markdown 格式（含标题标记）
        is_markdown = "\n# " in text or "\n## " in text
        if is_markdown:
            return self._chunk_markdown(text, title)

        if self._has_page_markers(text):
            return self._chunk_with_pages(text, document_type, title, metadata=metadata)

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

        chunks: List[Dict[str, Any]] = []
        for i, section in enumerate(sections):
            path = section.get("section_path", f"section/{i + 1}")
            
            # 根据是否启用概念感知分块选择不同的分块方式
            if concept_aware:
                window_texts = self._concept_aware_windows(section["content"])
            else:
                window_texts = self._semantic_windows(section["content"])
                
            for w_idx, window_text in enumerate(window_texts):
                chunks.append(
                    {
                        "chunk_id": f"{i + 1}-{w_idx + 1}",
                        "title": title,
                        "content": window_text,
                        "section_path": path,
                        "document_type": document_type,
                        "page_num": 0,
                        "chunk_type": self._classify_chunk(window_text, path, document_type),
                    }
                )
        return chunks
    
    def chunk_document_with_concepts(
        self,
        text: str,
        document_type: str,
        title: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        概念感知分块文档文本（实验性功能）。
        基于概念边界（定义、示例、应用、解释）进行智能分块，而不是简单的固定长度分割。
        
        返回的chunk_type会更细化，包括：definition, example, application, explanation等。
        """
        # 调用chunk_document并启用概念感知
        chunks = self.chunk_document(
            text=text,
            document_type=document_type,
            title=title,
            metadata=metadata,
            concept_aware=True
        )
        
        # 对每个chunk进行概念类型细化分类
        for chunk in chunks:
            chunk["chunk_type"] = self._classify_concept_chunk(chunk["content"])
            
        return chunks

    def _chunk_with_pages(
        self,
        text: str,
        document_type: str,
        title: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        page_segments = self._split_by_page_markers(text)
        page_groups = self._build_page_groups(page_segments, metadata=metadata)
        chunks: List[Dict[str, Any]] = []
        chunk_seq = 0

        # 对有页码的长文档用更大的窗口，避免逐页碎片化。
        window_chunk_size = max(self.chunk_size * 4, 2200)
        window_overlap = max(self.overlap * 2, 240)

        for group in page_groups:
            normalized = self._normalize(str(group.get("content") or ""))
            if not normalized:
                continue
            page_num = int(group.get("page_start", 0) or 0)
            if document_type == "exam":
                sections = self._chunk_exam(normalized)
            elif document_type == "technical":
                sections = self._chunk_technical(normalized)
            elif document_type == "project":
                sections = self._chunk_project(normalized)
            elif str(group.get("section_path") or "").startswith(("toc/", "pages/")):
                sections = [{"section_path": str(group.get("section_path") or f"page{page_num}"), "content": normalized}]
            else:
                sections = self._chunk_academic(normalized)
            for i, section in enumerate(sections):
                path = section.get("section_path", str(group.get("section_path") or f"page{page_num}/section/{i + 1}"))
                for w_idx, window_text in enumerate(
                    self._semantic_windows(
                        section["content"],
                        chunk_size=window_chunk_size,
                        overlap=window_overlap,
                    )
                ):
                    chunk_seq += 1
                    chunks.append(
                        {
                            "chunk_id": f"{chunk_seq}-{w_idx + 1}",
                            "title": title,
                            "content": window_text,
                            "section_path": path,
                            "document_type": document_type,
                            "page_num": page_num,
                            "chunk_type": self._classify_chunk(window_text, path, document_type),
                        }
                    )
        return chunks

    def _build_page_groups(
        self,
        page_segments: List[Tuple[int, str]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        pages: List[Tuple[int, str]] = []
        for page_num, content in page_segments:
            normalized = self._normalize(content)
            if normalized:
                pages.append((page_num, normalized))
        if not pages:
            return []

        toc_groups = self._build_groups_from_toc(pages, metadata=metadata)
        if toc_groups:
            return toc_groups
        return self._build_groups_by_page_window(pages)

    def _build_groups_from_toc(
        self,
        pages: List[Tuple[int, str]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        toc = metadata.get("toc") if isinstance(metadata, dict) else None
        if not isinstance(toc, list):
            return []

        page_map = {page_num: content for page_num, content in pages if page_num > 0}
        ordered_pages = sorted(page_map)
        if not ordered_pages:
            return []

        toc_items: List[Dict[str, Any]] = []
        for entry in toc:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            page = entry.get("page") if "page" in entry else entry.get("page_num")
            level = entry.get("level", 1)
            try:
                page_num = int(page or 0)
                level_num = int(level or 1)
            except Exception:
                continue
            if not title or page_num <= 0:
                continue
            toc_items.append({"title": title, "page": page_num, "level": level_num})

        if not toc_items:
            return []

        # 只取较粗层级，避免把目录三级、四级节点也拿来继续打碎。
        min_level = min(int(item["level"]) for item in toc_items)
        coarse_items = [item for item in toc_items if int(item["level"]) <= min_level + 1]
        coarse_items.sort(key=lambda item: int(item["page"]))
        if not coarse_items:
            return []

        groups: List[Dict[str, Any]] = []
        for index, item in enumerate(coarse_items):
            start_page = int(item["page"])
            next_page = int(coarse_items[index + 1]["page"]) if index + 1 < len(coarse_items) else ordered_pages[-1] + 1
            page_numbers = [page for page in ordered_pages if start_page <= page < next_page]
            if not page_numbers:
                continue
            body = "\n\n".join(page_map[page] for page in page_numbers if page_map.get(page))
            if not body.strip():
                continue
            groups.append(
                {
                    "page_start": page_numbers[0],
                    "page_end": page_numbers[-1],
                    "section_path": f"toc/{self._slug(str(item['title']))}",
                    "content": body,
                }
            )
        return groups

    def _build_groups_by_page_window(self, pages: List[Tuple[int, str]]) -> List[Dict[str, Any]]:
        page_window = 36
        groups: List[Dict[str, Any]] = []
        for index in range(0, len(pages), page_window):
            bucket = pages[index : index + page_window]
            if not bucket:
                continue
            start_page = bucket[0][0]
            end_page = bucket[-1][0]
            groups.append(
                {
                    "page_start": start_page,
                    "page_end": end_page,
                    "section_path": f"pages/{start_page}-{end_page}",
                    "content": "\n\n".join(content for _, content in bucket if content),
                }
            )
        return groups

    def _normalize(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _semantic_windows(
        self,
        text: str,
        chunk_size: Optional[int] = None,
        overlap: Optional[int] = None,
    ) -> List[str]:
        target_chunk_size = max(100, int(chunk_size or self.chunk_size))
        target_overlap = max(0, int(overlap if overlap is not None else self.overlap))
        if len(text) <= target_chunk_size:
            return [text]
        windows: List[str] = []
        start = 0
        while start < len(text):
            end = min(start + target_chunk_size, len(text))
            window = text[start:end]
            split_at = max(window.rfind("\n"), window.rfind("。"), window.rfind("."))
            if split_at > int(target_chunk_size * 0.6):
                end = start + split_at + 1
                window = text[start:end]
            windows.append(window.strip())
            if end >= len(text):
                break
            start = max(0, end - target_overlap)
        return [w for w in windows if w]

    def _is_visual_heading(self, line: str, next_line: Optional[str] = None) -> bool:
        """启发式检测视觉标题（大字、短行、无标点）。"""
        line = line.strip()
        if not line or len(line) > 50:
            return False
        
        # 排除包含常见句子结尾标点的行
        if any(line.endswith(p) for p in ("。", "？", "！", "：", "；", "...", "”", ")", "）")):
            return False
            
        # 如果包含 Markdown 加粗标记，极大概率是标题
        if line.startswith("**") and line.endswith("**"):
            return True
            
        # 排除纯数字或页码标记
        if line.isdigit() or _PAGE_MARKER_RE.match(line):
            return False

        # 如果下一行是长文本，当前行是短行且独立，可能是标题
        if next_line and len(next_line.strip()) > 100:
            return True
            
        # 常见白皮书关键词前缀
        if line.startswith(("第一", "第二", "第三", "核心", "关键", "结论", "建议")):
            return True

        return False

    def _split_by_visual_structure(self, text: str, default_prefix: str) -> List[Dict[str, str]]:
        """基于视觉和语义特征进行通用分段。"""
        lines = text.split("\n")
        sections: List[Dict[str, str]] = []
        current_heading = f"{default_prefix}/intro"
        buffer: List[str] = []

        def flush() -> None:
            if buffer:
                content = "\n".join(buffer).strip()
                if content:
                    sections.append({"section_path": current_heading, "content": content})

        for i, line in enumerate(lines):
            next_line = lines[i+1] if i + 1 < len(lines) else None
            if self._is_visual_heading(line, next_line):
                flush()
                # 提取标题作为路径，去除干扰字符
                heading_text = line.strip("*# \t")
                current_heading = f"{default_prefix}/{self._slug(heading_text)}"
                buffer = [line]
            else:
                buffer.append(line)
        flush()
        return sections

    def _chunk_academic(self, text: str) -> List[Dict[str, str]]:
        # 尝试视觉分段，解决白皮书无固定标题问题
        sections = self._split_by_visual_structure(text, "academic")
        if len(sections) <= 1:
            # 如果视觉分段失败（比如标题太隐蔽），回退到关键词模式
            heading_pattern = re.compile(
                r"(?im)^(abstract|introduction|related work|method(?:ology)?|experiment(?:s)?|result(?:s)?|discussion|conclusion|references|摘要|引言|相关工作|方法|实验|结果|讨论|结论|参考文献)\b.*$"
            )
            return self._split_by_heading(text, heading_pattern, default_prefix="academic")
        return sections

    def _chunk_exam(self, text: str) -> List[Dict[str, str]]:
        blocks = re.split(r"(?m)^\s*(?:第?\d+[题、.\)]|Q\d+[\.\):]|【题目】)", text)
        sections: List[Dict[str, str]] = []
        for i, block in enumerate(blocks):
            block = block.strip()
            if not block:
                continue
            sections.append({"section_path": f"exam/question/{i + 1}", "content": block})
        if not sections:
            sections = [{"section_path": "exam/overall", "content": text}]
        return sections

    def _chunk_technical(self, text: str) -> List[Dict[str, str]]:
        heading_pattern = re.compile(
            r"(?im)^(overview|architecture|api|endpoint|installation|configuration|usage|example|troubleshooting|概述|架构|接口|安装|配置|示例)\b.*$"
        )
        return self._split_by_heading(text, heading_pattern, default_prefix="technical")

    def _chunk_project(self, text: str) -> List[Dict[str, str]]:
        heading_pattern = re.compile(
            r"(?im)^(goal|scope|timeline|milestone|task|risk|owner|status|目标|范围|里程碑|任务|风险|负责人|进度)\b.*$"
        )
        return self._split_by_heading(text, heading_pattern, default_prefix="project")

    def _split_by_heading(self, text: str, pattern: re.Pattern[str], default_prefix: str) -> List[Dict[str, str]]:
        lines = text.split("\n")
        sections: List[Dict[str, str]] = []
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
        return [section for section in sections if section["content"]]

    def _slug(self, value: str) -> str:
        value = value.lower()
        value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
        return value.strip("-")[:80] or "section"

    @staticmethod
    def _classify_chunk(content: str, section_path: str, document_type: str) -> str:
        sp_lower = section_path.lower()
        content_lower = content.lower()

        for kw in _FILLER_SECTION_KEYWORDS:
            if kw in sp_lower:
                return "filler"

        head = content_lower[:200]
        filler_hits = sum(1 for kw in _FILLER_CONTENT_KEYWORDS if kw in head)
        if filler_hits >= 2:
            return "filler"

        stripped = content.strip()
        if len(stripped) < 60 and not _MATH_EXPR.search(stripped) and not _DEFINITION_MARKERS.search(stripped) and not _EXAMPLE_PATTERN.search(content):
            return "filler"

        if document_type == "exam":
            return "example"

        if "exam/question" in sp_lower:
            return "example"

        if _EXAMPLE_PATTERN.search(content):
            return "example"

        if _SOLUTION_KEYWORDS.search(content) and _MATH_EXPR.search(content) and len(stripped) > 80:
            return "example"

        return "knowledge"
    
    # ============ 概念感知分块核心方法 ============
    
    def _concept_aware_windows(
        self,
        text: str,
        chunk_size: Optional[int] = None,
        overlap: Optional[int] = None,
    ) -> List[str]:
        """
        概念感知分块：基于概念边界（定义、示例、应用等）进行智能分块。
        
        策略：
        1. 首先尝试基于概念边界分块
        2. 如果概念分块太大，再在概念内部进行语义分割
        3. 保持概念的完整性（尽量不切分同一个概念）
        """
        target_chunk_size = max(100, int(chunk_size or self.chunk_size))
        
        # 1. 基于概念边界进行初步分块
        concept_segments = self._split_by_concept_boundaries(text)
        
        # 如果只有一个概念段，直接使用语义窗口
        if len(concept_segments) <= 1:
            return self._semantic_windows(text, chunk_size, overlap)
        
        windows: List[str] = []
        
        # 2. 处理每个概念段
        for segment in concept_segments:
            segment_content = segment["content"]
            segment_type = segment.get("concept_type", "general")
            
            # 如果概念段太大，进行语义分割
            if len(segment_content) > target_chunk_size * 1.5:
                sub_windows = self._semantic_windows(
                    segment_content, 
                    chunk_size=target_chunk_size,
                    overlap=overlap or self.overlap
                )
                windows.extend(sub_windows)
            else:
                # 保持概念段的完整性
                windows.append(segment_content)
        
        # 3. 合并过小的相邻窗口
        merged_windows = self._merge_small_windows(windows, target_chunk_size)
        
        return merged_windows
    
    def _split_by_concept_boundaries(self, text: str) -> List[Dict[str, str]]:
        """
        基于概念标记进行文本分割。
        
        识别概念标记（定义、示例、应用、解释等）作为分块边界。
        """
        segments: List[Dict[str, str]] = []
        lines = text.split("\n")
        current_segment: List[str] = []
        current_type = "general"
        
        def flush_segment() -> None:
            if current_segment:
                content = "\n".join(current_segment).strip()
                if content:
                    segments.append({
                        "content": content,
                        "concept_type": current_type
                    })
        
        for line in lines:
            # 检查当前行是否包含概念标记
            detected_type = self._detect_concept_type(line)
            
            if detected_type != "general" and current_segment:
                # 找到新的概念段，刷新前一个段
                flush_segment()
                current_segment = [line]
                current_type = detected_type
            else:
                current_segment.append(line)
                # 如果当前段还没有类型，但新行有类型，更新类型
                if current_type == "general" and detected_type != "general":
                    current_type = detected_type
        
        # 刷新最后一个段
        flush_segment()
        
        return segments
    
    def _detect_concept_type(self, line: str) -> str:
        """
        检测单行文本中的概念类型。
        
        返回: "definition", "example", "application", "explanation", "summary", "problem", 或 "general"
        """
        line_lower = line.lower()
        
        # 检查每个概念标记
        for concept_type, pattern in _CONCEPT_MARKERS.items():
            if pattern.search(line_lower):
                return concept_type
        
        # 检查现有的定义标记
        if _DEFINITION_MARKERS.search(line):
            return "definition"
        
        # 检查示例标记
        if _EXAMPLE_PATTERN.search(line):
            return "example"
        
        return "general"
    
    def _classify_concept_chunk(self, content: str) -> str:
        """
        对chunk进行概念类型细化分类。
        
        返回: 
        - 概念相关类型: "definition", "example", "application", "explanation", "summary", "problem"
        - 传统类型: "knowledge", "example", "filler"
        """
        content_lower = content.lower()
        
        # 首先检查传统分类（保持向后兼容性）
        traditional_type = self._classify_chunk(content, "", "academic")
        
        # 如果被分类为filler，直接返回
        if traditional_type == "filler":
            return "filler"
        
        # 概念类型检测
        concept_scores = {}
        for concept_type, pattern in _CONCEPT_MARKERS.items():
            matches = list(pattern.finditer(content_lower))
            if matches:
                # 分数基于匹配次数和匹配位置的集中程度
                score = len(matches) * 10
                # 如果有多个匹配，假设这个概念类型更显著
                if len(matches) > 1:
                    score += 5
                concept_scores[concept_type] = score
        
        # 检查定义标记
        definition_matches = list(_DEFINITION_MARKERS.finditer(content))
        if definition_matches:
            concept_scores["definition"] = concept_scores.get("definition", 0) + len(definition_matches) * 15
        
        # 检查示例标记
        example_matches = list(_EXAMPLE_PATTERN.finditer(content))
        if example_matches:
            concept_scores["example"] = concept_scores.get("example", 0) + len(example_matches) * 12
        
        # 如果有概念类型分数高于阈值，返回最高分的类型
        if concept_scores:
            max_type = max(concept_scores.items(), key=lambda x: x[1])[0]
            max_score = concept_scores[max_type]
            
            # 阈值：至少需要一定的概念显著性
            if max_score >= 10:
                return max_type
        
        # 回退到传统分类
        return traditional_type
    
    def _merge_small_windows(self, windows: List[str], target_size: int) -> List[str]:
        """
        合并过小的相邻窗口，避免碎片化。
        """
        if not windows:
            return []
        
        merged: List[str] = []
        current = windows[0]
        
        for i in range(1, len(windows)):
            next_window = windows[i]
            
            # 如果合并后的长度不超过目标大小的1.2倍，且两个窗口有语义连续性
            if len(current) + len(next_window) <= target_size * 1.2:
                current = current + "\n\n" + next_window
            else:
                merged.append(current)
                current = next_window
        
        merged.append(current)
        return merged
