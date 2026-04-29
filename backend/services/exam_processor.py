import asyncio
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .free_ai_router import FreeAIRouter
from .rag_engine import RAGEngine

try:
    from langchain_core.prompts import PromptTemplate
except Exception:  # pragma: no cover - optional dependency
    PromptTemplate = None  # type: ignore


class ExamProcessor:
    def __init__(self, rag_engine: RAGEngine, ai_router: Optional[FreeAIRouter] = None, agent_chains: Optional[Any] = None) -> None:
        self.rag_engine = rag_engine
        self.ai_router = ai_router
        self.agent_chains = agent_chains

    async def analyze_exam(
        self,
        exam_text: str,
        discipline: str = "all",
        tenant_id: str = "public",
        billing_client_id: str = "",
        billing_exempt: bool = False,
    ) -> Dict[str, Any]:
        heuristic_questions = self._split_questions(exam_text)
        agent_questions = await self._split_questions_with_agent(exam_text, heuristic_questions)
        questions = self._choose_best_question_split(heuristic_questions, agent_questions)
        stats = self._difficulty_stats(questions)
        question_tree = self._build_question_tree(questions)
        structure_summary = self._summarize_exam_structure(questions, question_tree)
        exam_profile = self._infer_exam_profile(questions, question_tree, discipline)
        recommendations = await self._recommend(
            questions,
            discipline,
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
        )
        return {
            "question_count": len(questions),
            "difficulty": stats,
            "questions": questions,
            "question_tree": question_tree,
            "structure_summary": structure_summary,
            "exam_profile": exam_profile,
            "recommendations": recommendations,
        }

    async def analyze_and_answer_exam(
        self,
        exam_text: str,
        discipline: str = "all",
        tenant_id: str = "public",
        billing_client_id: str = "",
        billing_exempt: bool = False,
    ) -> Dict[str, Any]:
        analysis = await self.analyze_exam(
            exam_text,
            discipline,
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
        )
        answered_questions = await self._answer_questions(
            analysis.get("questions", []),
            discipline,
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
        )
        analysis["questions"] = answered_questions
        analysis["question_tree"] = self._build_question_tree(answered_questions)
        analysis["structure_summary"] = self._summarize_exam_structure(answered_questions, analysis["question_tree"])
        analysis["exam_profile"] = self._infer_exam_profile(
            answered_questions,
            analysis["question_tree"],
            discipline,
        )
        analysis["qa_regression_gates"] = self._aggregate_regression_gates(answered_questions)
        return analysis

    _MARKER_PATTERNS: List[Tuple[str, Any]] = [
        ("zh_big", re.compile(r"^\s*([一二三四五六七八九十百千]{1,4})[、\.．]\s*")),
        ("chapter", re.compile(r"^\s*第\s*(\d{1,3})\s*[题章节]\s*[、\.．:：)]?\s*")),
        ("q_num", re.compile(r"^\s*Q\s*(\d{1,3})\s*[\.\):：]?\s*", re.IGNORECASE)),
        ("num_dot", re.compile(r"^\s*(\d{1,3})[\.、．]\s+")),
        ("paren_num", re.compile(r"^\s*[（(]\s*(\d{1,3})\s*[）)]\s*")),
        ("circled", re.compile(r"^\s*([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])\s*")),
    ]
    _DESIGN_KEYWORDS = ("题目设计", "设计题", "方案设计", "实验设计", "系统设计", "课程设计")

    # ── 9 种题型分类规则 ─────────────────────────────────────────────
    _TYPE_SECTION_KW: Dict[str, List[str]] = {
        "choice": ["选择", "单选", "多选", "不定项选择"],
        "fill_blank": ["填空"],
        "true_false": ["判断", "是非"],
        "short_answer": ["简答", "简述", "名词解释"],
        "essay": ["论述", "问答"],
        "calculation": ["计算", "运算"],
        "proof": ["证明"],
        "design": ["设计", "方案"],
        "material_analysis": ["材料分析", "案例分析", "综合分析", "阅读理解"],
    }
    _TYPE_BODY_PATTERNS: Dict[str, Any] = {
        "choice": re.compile(r"(?:^|\n)\s*[A-Ha-h]\s*[.、．:：)）]\s*\S", re.MULTILINE),
        "fill_blank": re.compile(r"_{2,}|（\s*）|\(\s*\)|【\s*】"),
        "true_false": re.compile(r"[（(]\s*[)）]\s*$|[（(]\s*[对错√×TF]\s*[)）]", re.MULTILINE),
        "short_answer": re.compile(r"(?:^|\n)\s*(?:简述|简要说明|简要分析|列举|说明.{0,6}的含义|什么是)", re.MULTILINE),
        "essay": re.compile(r"试论|论述|详细阐述|分析并评价|谈谈你的看法|结合实际|如何理解"),
        "calculation": re.compile(r"(?:计算|求解|算出|求.{0,8}的值|解方程|列式)"),
        "proof": re.compile(r"(?:^|\n)\s*(?:证明|证：|Prove|证明题)", re.MULTILINE | re.IGNORECASE),
    }

    _MATERIAL_PATTERNS = [
        re.compile(r"阅读[下以]?[面列]?(?:的)?材料"),
        re.compile(r"材料[一二三四五六七八九十\d]"),
        re.compile(r"根据[下以]?[面列]?(?:的)?(?:材料|资料|图表|数据)"),
        re.compile(r"案例[：:]"),
        re.compile(r"背景(?:材料|资料|介绍)[：:]"),
    ]

    _OPTION_PATTERN = re.compile(
        r"(?:^|\n)\s*([A-Ha-h])\s*[.、．:：)）]\s*(.+?)(?=\n\s*[A-Ha-h]\s*[.、．:：)）]|\Z)",
        re.DOTALL,
    )

    _SAFE_MARKER_PATTERNS: List[Tuple[str, Any]] = [
        ("chapter", re.compile(r"^\s*第\s*([0-9\u4e00-\u9fff]{1,4})\s*[题章节]\s*[\.\u3001\uff0e:：\)]?\s*")),
        ("q_num", re.compile(r"^\s*Q\s*(\d{1,3})\s*[\.\):：\uff09]?\s*", re.IGNORECASE)),
        ("paren_num", re.compile(r"^\s*[\(\uff08]\s*(\d{1,3})\s*[\)\uff09]\s*")),
        ("num_dot", re.compile(r"^\s*(\d{1,3})\s*[\.\u3001\uff0e:：\)\uff09]\s*")),
        ("circled", re.compile(r"^\s*([\u2460-\u2473])\s*")),
        ("zh_big", re.compile(r"^\s*([一二三四五六七八九十百千零]{1,4})\s*[、\.\uff0e]\s*")),
    ]
    _INLINE_QUESTION_BOUNDARY = re.compile(
        r"(?<!\n)(?:(?<=[\s\u3000])|(?<=[。！？；;:：]))"
        r"(?=(?:第\s*[0-9\u4e00-\u9fff]{1,4}\s*[题章节]|Q\s*\d{1,3}\s*[\.\):：\uff09]?|(?:\d{1,3}|[\u2460-\u2473])\s*[\.\u3001\uff0e:：\)\uff09]|[一二三四五六七八九十百千零]{1,4}\s*[、\.\uff0e]))",
        re.IGNORECASE,
    )
    _CN_NUMBER_MAP: Dict[str, int] = {
        "\u96f6": 0,
        "\u4e00": 1,
        "\u4e8c": 2,
        "\u4e09": 3,
        "\u56db": 4,
        "\u4e94": 5,
        "\u516d": 6,
        "\u4e03": 7,
        "\u516b": 8,
        "\u4e5d": 9,
    }
    _ANSWER_RATE_LIMIT_RETRY_DELAY_SECONDS = 2.0

    _ANSWER_STRATEGY_HINTS: Dict[str, str] = {
        "choice": (
            "【题型：选择题】\n"
            "1) 逐选项分析正误，明确排除理由；\n"
            "2) 最终给出所选选项字母（如 A）；\n"
            "3) answer 字段第一个字符必须是所选选项字母。"
        ),
        "fill_blank": (
            "【题型：填空题】\n"
            "1) 直接给出填空答案，多空用分号分隔；\n"
            "2) 每空给出简要推导依据。"
        ),
        "true_false": (
            "【题型：判断题】\n"
            "1) 明确判断正确或错误；\n"
            "2) 给出判断理由（一句话）。"
        ),
        "short_answer": (
            "【题型：简答题】\n"
            "1) 分要点列出答案，每要点一句话；\n"
            "2) 要点应涵盖定义、特征、意义等核心维度。"
        ),
        "essay": (
            "【题型：论述题】\n"
            "1) 先亮明核心论点；\n"
            "2) 分层展开论证（背景→论据→推理→结论）；\n"
            "3) 字数允许适当展开，但保持逻辑连贯。"
        ),
        "calculation": (
            "【题型：计算题】\n"
            "1) 写出关键公式和代入过程；\n"
            "2) 给出最终数值结果及单位；\n"
            "3) 注意有效数字和近似精度。"
        ),
        "proof": (
            "【题型：证明题】\n"
            "1) 明确证明目标和已知条件；\n"
            "2) 按演绎链给出完整步骤；\n"
            "3) 每步标注所用定理/公理。"
        ),
        "design": (
            "【题型：设计题】\n"
            "1) 明确目标与约束条件；\n"
            "2) 提供评估指标与验收标准；\n"
            "3) 给出可实施步骤并对应证据。"
        ),
        "material_analysis": (
            "【题型：材料分析题】\n"
            "1) 先概括材料主旨；\n"
            "2) 结合材料中的具体数据/事实回答；\n"
            "3) 引用材料原文作为论据时需标注出处。"
        ),
        "standard": "",
    }

    def _split_questions(self, text: str) -> List[Dict[str, Any]]:
        blocks = self._split_blocks(text)
        if not blocks:
            return []

        marker_items: List[Dict[str, Any]] = []
        marker_counter: Counter = Counter()
        for block in blocks:
            marker = self._parse_leading_marker(block)
            marker_items.append({"block": block, "marker": marker})
            if marker:
                marker_counter[marker["type"]] += 1

        marker_priority = {
            "zh_big": 1,
            "chapter": 1,
            "q_num": 1,
            "num_dot": 2,
            "paren_num": 3,
            "circled": 3,
        }
        # 序号重复越少越偏上层，重复越多越偏下层；频次相同按题号语义优先级排序。
        marker_rank = sorted(marker_counter.items(), key=lambda x: (x[1], marker_priority.get(x[0], 99), x[0]))
        marker_level_map = {name: idx + 1 for idx, (name, _) in enumerate(marker_rank)}

        out: List[Dict[str, Any]] = []
        level_numbers: Dict[int, int] = {}
        path: List[int] = []
        current_section_title = ""
        parent_paths: Dict[int, str] = {}  # level -> number_path of that level

        for item in marker_items:
            clean = str(item["block"]).strip()
            if not clean:
                continue
            marker = item["marker"]

            if marker is None:
                if out:
                    out[-1]["text"] = f"{out[-1]['text']}\n{clean}"[:1200]
                continue

            marker_type = marker["type"]
            level = marker_level_map.get(marker_type, 1)
            marker_number = marker.get("number")
            if marker_number is None:
                marker_number = level_numbers.get(level, 0) + 1
            level_numbers[level] = marker_number
            for k in list(level_numbers.keys()):
                if k > level:
                    del level_numbers[k]

            while len(path) < level:
                path.append(1)
            path = path[:level]
            path[level - 1] = marker_number
            number_path = ".".join(str(x) for x in path if isinstance(x, int) and x > 0)

            # 追踪大题标题
            if level == 1:
                current_section_title = clean.split("\n")[0][:80]
            if self._is_section_header_block(clean, marker_type=marker_type, level=level):
                for k in list(parent_paths.keys()):
                    if k >= level:
                        del parent_paths[k]
                continue
            parent_paths[level] = number_path

            question_type = self._infer_question_type(clean, section_context=current_section_title)
            score = self._difficulty_score(clean, question_type=question_type)
            options = self._extract_options(clean) if question_type == "choice" else []

            # 计算 parent_path（上一级的 number_path）
            parent_path = None
            if level > 1:
                for plv in range(level - 1, 0, -1):
                    if plv in parent_paths:
                        parent_path = parent_paths[plv]
                        break

            out.append(
                {
                    "id": len(out) + 1,
                    "text": clean[:1200],
                    "difficulty_score": score,
                    "difficulty_level": self._level(score),
                    "level": level,
                    "number_path": number_path or str(len(out) + 1),
                    "marker_type": marker_type,
                    "question_type": question_type,
                    "options": options,
                    "material_id": None,
                    "material_text": None,
                    "parent_path": parent_path,
                    "section_title": current_section_title if level > 1 else None,
                }
            )

        if not out and text.strip():
            score = self._difficulty_score(text)
            out = [
                {
                    "id": 1,
                    "text": text.strip()[:1200],
                    "difficulty_score": score,
                    "difficulty_level": self._level(score),
                    "level": 1,
                    "number_path": "1",
                    "marker_type": "none",
                    "question_type": self._infer_question_type(text),
                    "options": [],
                    "material_id": None,
                    "material_text": None,
                    "parent_path": None,
                    "section_title": None,
                }
            ]

        out = self._detect_material_groups(out)
        return out

    async def _split_questions_with_agent(
        self,
        text: str,
        heuristic_questions: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        if self.ai_router is None:
            return None
        snippet = (text or "").strip()
        if not snippet:
            return None
        prompt = self._build_question_split_prompt(
            snippet[:12000],
            heuristic_count=len(heuristic_questions or []),
        )
        try:
            resp = await self.ai_router.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are an exam structure parser. Return JSON only. "
                            "Do not answer the exam. Do not include markdown fences."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1800,
                temperature=0.1,
            )
        except Exception:
            return None
        content = str(resp.get("content", "")).strip()
        return self._parse_question_split_contract(content)

    def _choose_best_question_split(
        self,
        heuristic_questions: List[Dict[str, Any]],
        agent_questions: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        if not agent_questions:
            return heuristic_questions
        if not heuristic_questions:
            return agent_questions
        if len(agent_questions) == 1 and len(heuristic_questions) >= 3:
            return heuristic_questions
        if len(agent_questions) < max(2, len(heuristic_questions) // 3):
            return heuristic_questions
        return agent_questions

    def _split_blocks(self, text: str) -> List[str]:
        normalized = self._normalize_text_for_question_split(text)
        lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
        if not lines:
            return []
        blocks: List[str] = []
        current = ""
        for ln in lines:
            is_new_block = self._parse_leading_marker(ln) is not None
            if is_new_block and current:
                blocks.append(current.strip())
                current = ln
            else:
                current = ln if not current else f"{current}\n{ln}"
        if current.strip():
            blocks.append(current.strip())
        return blocks

    def _parse_leading_marker(self, block: str) -> Optional[Dict[str, Any]]:
        for marker_type, pattern in self._SAFE_MARKER_PATTERNS:
            m = pattern.match(block)
            if not m:
                continue
            raw = (m.group(1) or "").strip()
            return {
                "type": marker_type,
                "raw": raw,
                "number": self._marker_to_number(raw),
            }
        for marker_type, pattern in self._MARKER_PATTERNS:
            m = pattern.match(block)
            if not m:
                continue
            raw = (m.group(1) or "").strip()
            return {
                "type": marker_type,
                "raw": raw,
                "number": self._marker_to_number(raw),
            }
        return None

    def _marker_to_number(self, raw: str) -> Optional[int]:
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        circled_map = {
            "①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5,
            "⑥": 6, "⑦": 7, "⑧": 8, "⑨": 9, "⑩": 10,
            "⑪": 11, "⑫": 12, "⑬": 13, "⑭": 14, "⑮": 15,
            "⑯": 16, "⑰": 17, "⑱": 18, "⑲": 19, "⑳": 20,
        }
        if raw in circled_map:
            return circled_map[raw]
        safe_cn_number = self._parse_cn_number(raw)
        if safe_cn_number is not None:
            return safe_cn_number
        zh_digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if all(ch in "一二三四五六七八九十百千零" for ch in raw):
            if raw == "十":
                return 10
            if "十" in raw:
                left, right = raw.split("十", 1)
                tens = zh_digits.get(left, 1 if left == "" else 0)
                ones = zh_digits.get(right, 0) if right else 0
                val = tens * 10 + ones
                return val if val > 0 else None
            return zh_digits.get(raw)
        return None

    def _normalize_text_for_question_split(self, text: str) -> str:
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"[ \t\u3000]+", " ", normalized)
        normalized = self._INLINE_QUESTION_BOUNDARY.sub("\n", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized

    def _parse_cn_number(self, raw: str) -> Optional[int]:
        token = str(raw or "").strip()
        if not token:
            return None
        if token == "\u5341":
            return 10
        if any(ch not in "\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343\u96f6" for ch in token):
            return None
        if "\u5341" in token:
            left, right = token.split("\u5341", 1)
            tens = self._CN_NUMBER_MAP.get(left, 1 if left == "" else 0)
            ones = self._CN_NUMBER_MAP.get(right, 0) if right else 0
            value = tens * 10 + ones
            return value if value > 0 else None
        return self._CN_NUMBER_MAP.get(token)

    def _build_question_split_prompt(self, text: str, heuristic_count: int = 0) -> str:
        return (
            "请把下面整份试卷拆分成“真正需要作答的题目”列表，并只返回 JSON。\n"
            "要求：\n"
            "1. 大题标题如“单项选择题、简答题、材料分析题”不算题目，不要单独输出。\n"
            "2. 真正需要回答的题目才输出；如果某题包含小题，使用 number_path 表示层级，如 4.1、4.2。\n"
            "3. section_title 填所属大题标题；parent_path 填上级题号，没有则为 null。\n"
            "4. question_type 只能取 choice/fill_blank/true_false/short_answer/essay/calculation/proof/design/material_analysis/standard。\n"
            f"5. 当前本地规则初判题数约为 {heuristic_count}，但你要以试卷真实结构为准，不要机械沿用。\n"
            "6. 返回格式：\n"
            "{\n"
            '  "questions": [\n'
            '    {"number_path":"1","parent_path":null,"section_title":"单项选择题","text":"题目全文","question_type":"choice","material_text":null},\n'
            '    {"number_path":"4","parent_path":null,"section_title":"简答题","text":"审核选择题题目设计……","question_type":"design","material_text":null}\n'
            "  ]\n"
            "}\n\n"
            "试卷原文如下：\n"
            f"{text}"
        )

    def _parse_question_split_contract(self, text: str) -> Optional[List[Dict[str, Any]]]:
        raw = (text or "").strip()
        if not raw:
            return None
        cleaned = raw
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 2 and lines[0].strip().startswith("```"):
                lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()
        parsed = FreeAIRouter.safe_json_loads(cleaned, None)
        if not isinstance(parsed, dict):
            return None
        rows = parsed.get("questions")
        if not isinstance(rows, list):
            return None

        out: List[Dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            text_value = str(row.get("text", "")).strip()
            if not text_value:
                continue
            if bool(row.get("is_section_header")):
                continue
            number_path = self._normalize_number_path(row.get("number_path") or row.get("number") or str(idx))
            parent_path = self._normalize_number_path(row.get("parent_path"))
            if not parent_path and "." in number_path:
                parent_path = number_path.rsplit(".", 1)[0]
            section_title = str(row.get("section_title", "")).strip() or None
            question_type = str(row.get("question_type", "")).strip()
            if question_type not in {
                "choice",
                "fill_blank",
                "true_false",
                "short_answer",
                "essay",
                "calculation",
                "proof",
                "design",
                "material_analysis",
                "standard",
            }:
                question_type = self._infer_question_type(text_value, section_context=section_title or "")
            score = self._difficulty_score(text_value, question_type=question_type)
            options = self._extract_options(text_value) if question_type == "choice" else []
            out.append(
                {
                    "id": len(out) + 1,
                    "text": text_value[:1200],
                    "difficulty_score": score,
                    "difficulty_level": self._level(score),
                    "level": number_path.count(".") + 1,
                    "number_path": number_path,
                    "marker_type": "agent",
                    "question_type": question_type,
                    "options": options,
                    "material_id": None,
                    "material_text": str(row.get("material_text", "")).strip() or None,
                    "parent_path": parent_path or None,
                    "section_title": section_title,
                }
            )
        return out or None

    def _normalize_number_path(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        normalized = raw.replace("．", ".").replace("。", ".").replace("、", ".")
        normalized = normalized.replace("（", "(").replace("）", ")")
        normalized = re.sub(r"[()]", "", normalized)
        normalized = re.sub(r"[^0-9A-Za-z.\-]+", ".", normalized)
        normalized = re.sub(r"\.{2,}", ".", normalized).strip(".")
        return normalized

    def _is_section_header_block(self, text: str, marker_type: str, level: int) -> bool:
        if level != 1 and marker_type not in {"zh_big", "chapter"}:
            return False
        compact = re.sub(r"\s+", " ", str(text or "")).strip()
        if not compact:
            return False
        first_line = compact.split("\n", 1)[0].strip()
        if len(compact) > 90 or len(first_line) > 50:
            return False
        if any(token in compact for token in ["?", "？", "A.", "B.", "C.", "D.", "A、", "B、", "C、", "D、"]):
            return False
        question_cues = ["请", "说明", "分析", "计算", "证明", "回答", "作答", "根据", "为什么", "如何", "是否"]
        if any(cue in compact for cue in question_cues):
            return False
        header_keywords = [
            "选择题",
            "单项选择",
            "多项选择",
            "判断题",
            "填空题",
            "简答题",
            "论述题",
            "计算题",
            "证明题",
            "材料分析",
            "案例分析",
            "综合题",
            "阅读理解",
            "作文题",
        ]
        if any(keyword in compact for keyword in header_keywords):
            return True
        if any(token in compact for token in ["本题", "每题", "共", "分"]) and len(compact) <= 60:
            return True
        return False

    def _infer_question_type(self, text: str, section_context: str = "") -> str:
        sc_lower = (section_context or "").lower()
        text_lower = (text or "").lower()

        # 优先级 1: section_context（大题标题）关键词命中
        for qtype, keywords in self._TYPE_SECTION_KW.items():
            if any(kw in sc_lower for kw in keywords):
                return qtype

        # 优先级 2: body 正则特征
        # 选择题需要 >=3 个不同选项标签
        choice_pat = self._TYPE_BODY_PATTERNS["choice"]
        option_labels = set(m.group(0).strip()[0].upper() for m in choice_pat.finditer(text))
        if len(option_labels) >= 3:
            return "choice"

        for qtype in ("fill_blank", "true_false", "proof", "calculation", "essay", "short_answer"):
            pat = self._TYPE_BODY_PATTERNS.get(qtype)
            if pat and pat.search(text):
                return qtype

        # 优先级 3: 旧逻辑回退
        if any(k in text_lower for k in self._DESIGN_KEYWORDS):
            return "design"
        return "standard"

    def _extract_options(self, text: str) -> List[Dict[str, str]]:
        """从题干中提取 A/B/C/D 等选项。"""
        matches = list(self._OPTION_PATTERN.finditer(text))
        if len(matches) < 2:
            return []
        seen: Dict[str, str] = {}
        for m in matches:
            label = m.group(1).upper()
            opt_text = m.group(2).strip().split("\n")[0].strip()
            if label not in seen and opt_text:
                seen[label] = opt_text
        if len(seen) < 2:
            return []
        return [{"label": k, "text": v} for k, v in sorted(seen.items())]

    def _detect_material_groups(self, questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """后处理：识别材料段，为子题注入 material_id/material_text，移除纯材料段。"""
        material_ids_to_remove: set = set()
        mat_counter = 0
        i = 0
        while i < len(questions):
            q = questions[i]
            # 只检查 level==1 的块
            if q.get("level", 0) != 1:
                i += 1
                continue
            text = q.get("text", "")
            is_material = any(p.search(text) for p in self._MATERIAL_PATTERNS)
            if not is_material:
                i += 1
                continue
            mat_counter += 1
            mat_id = f"mat_{mat_counter}"
            mat_text = text[:1200]
            material_ids_to_remove.add(q["id"])
            # 向后扫描同组子题
            j = i + 1
            while j < len(questions):
                sub = questions[j]
                if sub.get("level", 0) <= q.get("level", 1):
                    break  # 遇到同级或更高级则停止
                sub["material_id"] = mat_id
                sub["material_text"] = mat_text
                if sub.get("question_type") == "standard":
                    sub["question_type"] = "material_analysis"
                j += 1
            i = j

        return [q for q in questions if q["id"] not in material_ids_to_remove]

    def _difficulty_score(self, question: str, question_type: str = "standard") -> float:
        length_factor = min(len(question) / 220.0, 1.0)
        keyword_bonus = 0.0
        hard_keywords = ["证明", "推导", "复杂度", "optimize", "derive", "证明题", "设计并实现"]
        for kw in hard_keywords:
            if kw.lower() in question.lower():
                keyword_bonus += 0.13
        option_penalty = 0.15 if re.search(r"\bA[\.\)]|\bB[\.\)]|\bC[\.\)]|\bD[\.\)]", question) else 0.0
        # 题型微调
        type_adjust = {"proof": 0.08, "calculation": 0.08, "essay": 0.05, "design": 0.05,
                        "choice": -0.08, "true_false": -0.10, "fill_blank": -0.05}.get(question_type, 0.0)
        score = max(0.0, min(1.0, 0.35 + length_factor * 0.5 + keyword_bonus - option_penalty + type_adjust))
        return round(score, 3)

    def _level(self, score: float) -> str:
        if score < 0.45:
            return "easy"
        if score < 0.72:
            return "medium"
        return "hard"

    def _difficulty_stats(self, questions: List[Dict[str, Any]]) -> Dict[str, Any]:
        counter = {"easy": 0, "medium": 0, "hard": 0}
        avg = 0.0
        if not questions:
            return {"average_score": avg, "distribution": counter}
        for q in questions:
            counter[q["difficulty_level"]] += 1
            avg += q["difficulty_score"]
        avg /= len(questions)
        return {"average_score": round(avg, 3), "distribution": counter}

    async def _recommend(
        self,
        questions: List[Dict[str, Any]],
        discipline: str,
        tenant_id: str = "public",
        billing_client_id: str = "",
        billing_exempt: bool = False,
    ) -> List[Dict[str, Any]]:
        if not questions:
            return []
        query = " ".join(q["text"] for q in questions[:3])[:1200]
        try:
            found = await self.rag_engine.hybrid_search(
                query=query,
                discipline_filter=discipline,
                top_k=5,
                tenant_id=tenant_id,
                billing_client_id=billing_client_id,
                billing_exempt=billing_exempt,
            )
        except Exception as exc:
            return self._build_recommendation_failure_result(exc)
        recs = []
        for i, row in enumerate(found["results"]):
            recs.append(
                {
                    "rank": i + 1,
                    "title": row.get("title"),
                    "section_path": row.get("section_path"),
                    "reason": "该资料与题目语义相关，可用于查漏补缺。",
                }
            )
        return recs

    def _build_recommendation_failure_result(self, exc: Exception) -> List[Dict[str, Any]]:
        message = str(exc).strip()
        lowered = message.lower()
        status_code = getattr(exc, "status_code", None)
        is_rate_limited = status_code == 429 or "429" in lowered or "too many requests" in lowered
        if is_rate_limited:
            reason = "推荐资料检索暂时触发限流，已跳过推荐步骤，不影响试卷主体解析。"
            fallback_reason = "rate_limited"
        else:
            reason = "推荐资料检索暂时失败，已跳过推荐步骤，不影响试卷主体解析。"
            fallback_reason = "recommendation_unavailable"
        return [
            {
                "rank": 1,
                "title": "推荐资料暂不可用",
                "section_path": "system/fallback",
                "reason": reason,
                "fallback_reason": fallback_reason,
                "error_detail": message[:240],
            }
        ]

    async def _answer_questions(
        self,
        questions: List[Dict[str, Any]],
        discipline: str,
        tenant_id: str = "public",
        billing_client_id: str = "",
        billing_exempt: bool = False,
    ) -> List[Dict[str, Any]]:
        if not questions:
            return []
        answered: List[Dict[str, Any]] = []
        use_agent = self.agent_chains is not None
        for item in questions:
            query = str(item.get("text", "")).strip()
            question_type = str(item.get("question_type", "standard"))
            material_text = str(item.get("material_text") or "")
            enriched_query = self._build_agent_query(query, question_type, material_text=material_text)
            if use_agent:
                graph_result = await self.agent_chains.run_exam_graph(
                    query=enriched_query,
                    discipline=discipline,
                    tenant_id=tenant_id,
                    question_type=question_type,
                    billing_client_id=billing_client_id,
                    billing_exempt=billing_exempt,
                )
                answer_text = str(graph_result.get("answer", "")).strip() or "未生成有效答案。"
                brief_reasoning = self._sanitize_brief_reasoning(graph_result.get("brief_reasoning", []))
                evidence = graph_result.get("evidence", [])
                strategy = self._normalize_strategy(graph_result.get("answer_strategy", {}))
                qa_gates = graph_result.get("qa_regression_gates", {})
                quality_gates = graph_result.get("quality_gates", {})
                merged_gates = qa_gates if isinstance(qa_gates, dict) else {}
                if isinstance(quality_gates, dict):
                    merged_gates = {**merged_gates, **quality_gates}
                if not merged_gates:
                    merged_gates = self._build_qa_gates(answer_text, brief_reasoning, evidence, strategy)
                answered.append(
                    {
                        **item,
                        "ai_answer": answer_text,
                        "brief_reasoning": brief_reasoning,
                        "evidence": evidence,
                        "answer_strategy": strategy,
                        "qa_gates": merged_gates,
                        "agent_trace": graph_result.get("agent_trace", []),
                        "cost_profile": graph_result.get("cost_profile", {}),
                    }
                )
                continue
            if self.ai_router is None:
                answered.append(
                    {
                        **item,
                        "ai_answer": "当前未配置可用模型，暂无法自动作答。",
                        "brief_reasoning": ["模型未启用，暂无法生成简版思路。"],
                        "evidence": [],
                        "answer_strategy": self._empty_strategy(),
                        "qa_gates": self._build_qa_gates(
                            answer="当前未配置可用模型，暂无法自动作答。",
                            brief_reasoning=["模型未启用，暂无法生成简版思路。"],
                            evidence=[],
                            strategy=self._empty_strategy(),
                        ),
                    }
                )
                continue
            retrieval = await self.rag_engine.hybrid_search(
                query=query,
                discipline_filter=discipline,
                top_k=4,
                tenant_id=tenant_id,
                billing_client_id=billing_client_id,
                billing_exempt=billing_exempt,
            )
            contexts = retrieval.get("results", [])
            evidence = [
                {
                    "title": str(row.get("title", "未命名来源")),
                    "section_path": str(row.get("section_path", "N/A")),
                    "discipline": str(row.get("discipline", discipline or "all")),
                }
                for row in contexts[:3]
            ]
            prompt = self._build_answer_prompt(query, contexts, evidence, question_type=question_type, material_text=material_text)
            resp = await self.ai_router.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是考试答题助手。请先进行内部推理，但最终仅输出约定 JSON。"
                            "禁止泄露完整思维链或逐步推导。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=520,
                temperature=0.2,
            )
            if self._should_retry_chat_response(resp):
                resp = await self._retry_question_chat_after_backoff(
                    chat_messages,
                    max_tokens=520,
                    temperature=0.2,
                )
                if self._should_retry_chat_response(resp):
                    return self._build_answer_failure_result(item, RuntimeError("answer provider unavailable after retry"))
            structured = self._parse_answer_contract(str(resp.get("content", "")))
            answer_text = structured["answer"] or "未生成有效答案。"
            brief_reasoning = self._sanitize_brief_reasoning(structured["brief_reasoning"])
            strategy = self._normalize_strategy(structured["answer_strategy"])
            qa_gates = self._build_qa_gates(
                answer=answer_text,
                brief_reasoning=brief_reasoning,
                evidence=evidence,
                strategy=strategy,
            )
            answered.append(
                {
                    **item,
                    "ai_answer": answer_text,
                    "brief_reasoning": brief_reasoning,
                    "evidence": evidence,
                    "answer_strategy": strategy,
                    "qa_gates": qa_gates,
                }
            )
        return answered

    def _build_agent_query(self, question: str, question_type: str, material_text: str = "") -> str:
        hint = self._resolve_question_type_hint(question_type, question, material_text=material_text)
        parts = [question]
        if material_text:
            parts.append(f"\n\n【相关材料】\n{material_text[:1200]}")
        if hint:
            parts.append(f"\n\n{hint}\n输出仍需遵循简版思路与证据可追溯。")
        return "".join(parts)

    def _resolve_question_type_hint(self, question_type: str, question: str, material_text: str = "") -> str:
        hint = self._ANSWER_STRATEGY_HINTS.get(question_type, "")
        combined = f"{question}\n{material_text}".lower()
        review_keywords = [
            "审核",
            "评价",
            "评估",
            "改进",
            "优化",
            "题目设计",
            "试题设计",
            "命题",
            "干扰项",
            "选项设计",
        ]
        if question_type in {"design", "short_answer", "standard"} and any(keyword in combined for keyword in review_keywords):
            return (
                "【题型：题目设计/审核题】\n"
                "1) 先判断原题设计目标、考查点与适配对象；\n"
                "2) 明确指出题干、选项、干扰项、表述边界中的问题；\n"
                "3) 给出可执行的修改建议或重写版本；\n"
                "4) 如果涉及选择题，需单独评价正确项唯一性、干扰项质量和歧义风险。"
            )
        return hint

    def _build_answer_prompt(
        self,
        question: str,
        contexts: List[Dict[str, Any]],
        evidence: List[Dict[str, str]],
        question_type: str = "standard",
        material_text: str = "",
    ) -> str:
        snippets = []
        for row in contexts[:4]:
            snippets.append(
                f"[{row.get('title', '未命名来源')}::{row.get('section_path', 'N/A')}]\n{row.get('content', '')}"
            )
        context_text = "\n\n".join(snippets) if snippets else "暂无检索上下文"
        evidence_text = "；".join(
            f"{e.get('title', '未命名来源')}({e.get('section_path', 'N/A')})"
            for e in evidence[:3]
        ) or "暂无明确证据"
        output_contract = (
            "{\n"
            '  "answer": "最终答案，1-3句",\n'
            '  "brief_reasoning": ["简版思路1", "简版思路2", "简版思路3(可选)"],\n'
            '  "answer_strategy": {\n'
            '    "concept_induction": "题目意图与考点归纳",\n'
            '    "information_compression": "证据压缩后的关键依据",\n'
            '    "reverse_check": "反向检验是否与答案冲突",\n'
            '    "distractor_design": "干扰项设计说明或排除逻辑"\n'
            "  }\n"
            "}"
        )
        type_hint = self._resolve_question_type_hint(question_type, question, material_text=material_text)
        if type_hint:
            type_hint = f"{type_hint}\n"
        material_section = ""
        if material_text:
            material_section = f"\n【相关材料】\n{material_text[:1200]}\n"
        if PromptTemplate is not None:
            template = PromptTemplate.from_template(
                "请回答以下题目，并按约定输出 JSON。\n"
                "{type_hint}"
                "作答策略要求（必须全部体现）：\n"
                "1) 概念归纳：先提炼题目意图与考点；\n"
                "2) 信息压缩：仅保留高信息密度证据；\n"
                "3) 反向思考：检验答案鲁棒性；\n"
                "4) 干扰项设计：说明易错项或排除逻辑。\n"
                "输出约束：\n"
                "- 仅输出合法 JSON，不要 markdown；\n"
                "- brief_reasoning 最多 3 条、每条一句；\n"
                "- 不得输出完整推理链或逐步思维过程。\n\n"
                "{material_section}"
                "题目：{question}\n\n"
                "证据来源：{evidence}\n\n"
                "参考资料：\n{context}\n"
                "请严格按以下结构返回：\n{contract}\n"
            )
            return template.format(
                question=question,
                evidence=evidence_text,
                context=context_text,
                contract=output_contract,
                type_hint=type_hint,
                material_section=material_section,
            )
        return (
            "请回答以下题目，并按约定输出 JSON。\n"
            f"{type_hint}"
            "必须体现：概念归纳、信息压缩、反向思考、干扰项设计；"
            "且仅输出答案+简版思路+策略结构，不输出完整推理链。\n\n"
            f"{material_section}"
            f"题目：{question}\n\n证据来源：{evidence_text}\n\n参考资料：\n{context_text}\n\n"
            f"返回结构：\n{output_contract}"
        )

    async def _answer_questions(
        self,
        questions: List[Dict[str, Any]],
        discipline: str,
        tenant_id: str = "public",
        billing_client_id: str = "",
        billing_exempt: bool = False,
    ) -> List[Dict[str, Any]]:
        if not questions:
            return []

        normalized = [dict(item) for item in questions]
        path_map: Dict[str, Dict[str, Any]] = {}
        children_map: Dict[str, List[Dict[str, Any]]] = {}
        ordered_paths: List[str] = []

        for item in normalized:
            path = str(item.get("number_path") or item.get("id") or "").strip()
            if not path:
                path = str(len(ordered_paths) + 1)
                item["number_path"] = path
            path_map[path] = item
            children_map.setdefault(path, [])
            ordered_paths.append(path)

        roots: List[Dict[str, Any]] = []
        for item in normalized:
            parent_path = str(item.get("parent_path") or "").strip()
            if parent_path and parent_path in path_map:
                children_map.setdefault(parent_path, []).append(item)
            else:
                roots.append(item)

        for child_items in children_map.values():
            child_items.sort(key=self._question_sort_key)
        roots.sort(key=self._question_sort_key)

        answered_cache: Dict[str, Dict[str, Any]] = {}

        async def solve(item: Dict[str, Any]) -> Dict[str, Any]:
            path = str(item.get("number_path") or item.get("id") or "").strip()
            if path in answered_cache:
                return answered_cache[path]

            answered_children: List[Dict[str, Any]] = []
            for child in children_map.get(path, []):
                answered_children.append(await solve(child))

            if answered_children:
                solved = self._build_group_question_summary(item, answered_children, path_map)
            else:
                solved = await self._answer_single_question_resilient(
                    item,
                    discipline,
                    tenant_id=tenant_id,
                    billing_client_id=billing_client_id,
                    billing_exempt=billing_exempt,
                    path_map=path_map,
                    children_map=children_map,
                )

            solved["child_count"] = len(answered_children)
            solved["node_kind"] = "group" if answered_children else "leaf"
            solved["child_question_paths"] = [str(child.get("number_path", "")).strip() for child in answered_children]
            solved["question_context"] = self._build_question_context(item, path_map, children_map)
            solved["subtree_summary"] = self._build_subtree_summary(solved, answered_children)
            answered_cache[path] = solved
            return solved

        for root in roots:
            await solve(root)

        return [answered_cache[path] for path in ordered_paths if path in answered_cache]

    async def _answer_single_question(
        self,
        item: Dict[str, Any],
        discipline: str,
        tenant_id: str = "public",
        billing_client_id: str = "",
        billing_exempt: bool = False,
        path_map: Optional[Dict[str, Dict[str, Any]]] = None,
        children_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        query = str(item.get("text", "")).strip()
        question_type = str(item.get("question_type", "standard"))
        material_text = str(item.get("material_text") or "")
        hierarchy_context = self._build_question_context(item, path_map or {}, children_map or {})
        retrieval_query = self._build_retrieval_query(item, path_map or {})
        enriched_query = self._build_agent_query(
            query,
            question_type,
            material_text=material_text,
            hierarchy_context=hierarchy_context,
        )

        if self.agent_chains is not None:
            try:
                graph_result = await self.agent_chains.run_exam_graph(
                    query=enriched_query,
                    discipline=discipline,
                    tenant_id=tenant_id,
                    question_type=question_type,
                    question_context=hierarchy_context,
                    billing_client_id=billing_client_id,
                    billing_exempt=billing_exempt,
                )
            except Exception as exc:
                if self._is_rate_limited_error(exc):
                    try:
                        await asyncio.sleep(self._ANSWER_RATE_LIMIT_RETRY_DELAY_SECONDS)
                        graph_result = await self.agent_chains.run_exam_graph(
                            query=enriched_query,
                            discipline=discipline,
                            tenant_id=tenant_id,
                            question_type=question_type,
                            question_context=hierarchy_context,
                            billing_client_id=billing_client_id,
                            billing_exempt=billing_exempt,
                        )
                    except Exception as retry_exc:
                        return self._build_answer_failure_result(item, retry_exc)
                else:
                    return self._build_answer_failure_result(item, exc)
            answer_text = str(graph_result.get("answer", "")).strip() or "未生成有效作答。"
            brief_reasoning = self._sanitize_brief_reasoning(graph_result.get("brief_reasoning", []))
            evidence = graph_result.get("evidence", [])
            strategy = self._normalize_strategy(graph_result.get("answer_strategy", {}))
            qa_gates = graph_result.get("qa_regression_gates", {})
            quality_gates = graph_result.get("quality_gates", {})
            merged_gates = qa_gates if isinstance(qa_gates, dict) else {}
            if isinstance(quality_gates, dict):
                merged_gates = {**merged_gates, **quality_gates}
            if not merged_gates:
                merged_gates = self._build_qa_gates(answer_text, brief_reasoning, evidence, strategy)
            return {
                **item,
                "ai_answer": answer_text,
                "brief_reasoning": brief_reasoning,
                "evidence": evidence,
                "answer_strategy": strategy,
                "qa_gates": merged_gates,
                "agent_trace": graph_result.get("agent_trace", []),
                "cost_profile": graph_result.get("cost_profile", {}),
            }

        if self.ai_router is None:
            answer_text = "当前未配置可用模型，暂时无法自动作答。"
            brief_reasoning = ["模型未启用，暂时无法生成简版思路。"]
            strategy = self._empty_strategy()
            return {
                **item,
                "ai_answer": answer_text,
                "brief_reasoning": brief_reasoning,
                "evidence": [],
                "answer_strategy": strategy,
                "qa_gates": self._build_qa_gates(answer_text, brief_reasoning, [], strategy),
            }

        try:
            retrieval = await self.rag_engine.hybrid_search(
                query=retrieval_query,
                discipline_filter=discipline,
                top_k=4,
                tenant_id=tenant_id,
                billing_client_id=billing_client_id,
                billing_exempt=billing_exempt,
            )
        except Exception as exc:
            if self._is_rate_limited_error(exc):
                try:
                    retrieval = await self._retry_question_retrieval_after_backoff(
                        query=retrieval_query,
                        discipline=discipline,
                        tenant_id=tenant_id,
                        billing_client_id=billing_client_id,
                        billing_exempt=billing_exempt,
                    )
                except Exception as retry_exc:
                    return self._build_answer_failure_result(item, retry_exc)
            else:
                return self._build_answer_failure_result(item, exc)
        contexts = retrieval.get("results", [])
        evidence = [
            {
                "title": str(row.get("title", "未命名来源")),
                "section_path": str(row.get("section_path", "N/A")),
                "discipline": str(row.get("discipline", discipline or "all")),
            }
            for row in contexts[:3]
        ]
        prompt = self._build_answer_prompt(
            query,
            contexts,
            evidence,
            question_type=question_type,
            material_text=material_text,
            hierarchy_context=hierarchy_context,
        )
        chat_messages = [
            {
                "role": "system",
                "content": (
                    "浣犳槸鑰冭瘯绛旈鍔╂墜銆傝鍏堣繘琛屽唴閮ㄦ帹鐞嗭紝浣嗘渶缁堜粎杈撳嚭绾﹀畾 JSON銆?"
                    "绂佹娉勯湶瀹屾暣鎬濈淮閾炬垨閫愭鎺ㄥ銆?"
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            resp = await self.ai_router.chat(
                [
                    {
                    "role": "system",
                    "content": (
                        "你是考试答题助手。请先进行内部推理，但最终仅输出约定 JSON。"
                        "禁止泄露完整思维链或逐步推导。"
                    ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=520,
                temperature=0.2,
            )
            structured = self._parse_answer_contract(str(resp.get("content", "")))
        except Exception as exc:
            if self._is_rate_limited_error(exc):
                try:
                    resp = await self._retry_question_chat_after_backoff(
                        chat_messages,
                        max_tokens=520,
                        temperature=0.2,
                    )
                    if self._should_retry_chat_response(resp):
                        return self._build_answer_failure_result(item, RuntimeError("answer provider unavailable after retry"))
                    structured = self._parse_answer_contract(str(resp.get("content", "")))
                except Exception as retry_exc:
                    return self._build_answer_failure_result(item, retry_exc)
            else:
                return self._build_answer_failure_result(item, exc)
        answer_text = structured["answer"] or "未生成有效作答。"
        brief_reasoning = self._sanitize_brief_reasoning(structured["brief_reasoning"])
        strategy = self._normalize_strategy(structured["answer_strategy"])
        qa_gates = self._build_qa_gates(
            answer=answer_text,
            brief_reasoning=brief_reasoning,
            evidence=evidence,
            strategy=strategy,
        )
        return {
            **item,
            "ai_answer": answer_text,
            "brief_reasoning": brief_reasoning,
            "evidence": evidence,
            "answer_strategy": strategy,
            "qa_gates": qa_gates,
        }

    async def _answer_single_question_resilient(
        self,
        item: Dict[str, Any],
        discipline: str,
        tenant_id: str = "public",
        billing_client_id: str = "",
        billing_exempt: bool = False,
        path_map: Optional[Dict[str, Dict[str, Any]]] = None,
        children_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        query = str(item.get("text", "")).strip()
        question_type = str(item.get("question_type", "standard"))
        material_text = str(item.get("material_text") or "")
        hierarchy_context = self._build_question_context(item, path_map or {}, children_map or {})
        retrieval_query = self._build_retrieval_query(item, path_map or {})
        enriched_query = self._build_agent_query(
            query,
            question_type,
            material_text=material_text,
            hierarchy_context=hierarchy_context,
        )

        agent_chain_error: Optional[Exception] = None
        if self.agent_chains is not None:
            agent_retry_used = False
            graph_result: Optional[Dict[str, Any]] = None
            try:
                graph_result = await self.agent_chains.run_exam_graph(
                    query=enriched_query,
                    discipline=discipline,
                    tenant_id=tenant_id,
                    question_type=question_type,
                    question_context=hierarchy_context,
                    billing_client_id=billing_client_id,
                    billing_exempt=billing_exempt,
                )
            except Exception as exc:
                agent_chain_error = exc
                if self._is_rate_limited_error(exc):
                    agent_retry_used = True
                    try:
                        await asyncio.sleep(self._ANSWER_RATE_LIMIT_RETRY_DELAY_SECONDS)
                        graph_result = await self.agent_chains.run_exam_graph(
                            query=enriched_query,
                            discipline=discipline,
                            tenant_id=tenant_id,
                            question_type=question_type,
                            question_context=hierarchy_context,
                            billing_client_id=billing_client_id,
                            billing_exempt=billing_exempt,
                        )
                        agent_chain_error = None
                    except Exception as retry_exc:
                        agent_chain_error = retry_exc
            if agent_chain_error is None and graph_result is not None:
                answer_text = str(graph_result.get("answer", "")).strip() or "未生成有效作答。"
                brief_reasoning = self._sanitize_brief_reasoning(graph_result.get("brief_reasoning", []))
                evidence = graph_result.get("evidence", [])
                strategy = self._normalize_strategy(graph_result.get("answer_strategy", {}))
                qa_gates = graph_result.get("qa_regression_gates", {})
                quality_gates = graph_result.get("quality_gates", {})
                merged_gates = qa_gates if isinstance(qa_gates, dict) else {}
                if isinstance(quality_gates, dict):
                    merged_gates = {**merged_gates, **quality_gates}
                if not merged_gates:
                    merged_gates = self._build_qa_gates(answer_text, brief_reasoning, evidence, strategy)
                return {
                    **item,
                    "ai_answer": answer_text,
                    "brief_reasoning": brief_reasoning,
                    "evidence": evidence,
                    "answer_strategy": strategy,
                    "qa_gates": merged_gates,
                    "agent_trace": graph_result.get("agent_trace", []),
                    "cost_profile": graph_result.get("cost_profile", {}),
                    "answer_provider": str(graph_result.get("provider") or "agent-graph"),
                    "retry_used": agent_retry_used,
                }

        if self.ai_router is None:
            if agent_chain_error is not None:
                return self._build_answer_failure_result(item, agent_chain_error)
            answer_text = "当前未配置可用模型，暂时无法自动作答。"
            brief_reasoning = ["模型未启用，暂时无法生成简版思路。"]
            strategy = self._empty_strategy()
            return {
                **item,
                "ai_answer": answer_text,
                "brief_reasoning": brief_reasoning,
                "evidence": [],
                "answer_strategy": strategy,
                "qa_gates": self._build_qa_gates(answer_text, brief_reasoning, [], strategy),
            }

        try:
            retrieval = await self.rag_engine.hybrid_search(
                query=retrieval_query,
                discipline_filter=discipline,
                top_k=4,
                tenant_id=tenant_id,
                billing_client_id=billing_client_id,
                billing_exempt=billing_exempt,
            )
        except Exception as exc:
            if self._is_rate_limited_error(exc):
                try:
                    retrieval = await self._retry_question_retrieval_after_backoff(
                        query=retrieval_query,
                        discipline=discipline,
                        tenant_id=tenant_id,
                        billing_client_id=billing_client_id,
                        billing_exempt=billing_exempt,
                    )
                except Exception as retry_exc:
                    return self._build_answer_failure_result(item, retry_exc)
            else:
                return self._build_answer_failure_result(item, exc)

        contexts = retrieval.get("results", [])
        evidence = [
            {
                "title": str(row.get("title", "未命名来源")),
                "section_path": str(row.get("section_path", "N/A")),
                "discipline": str(row.get("discipline", discipline or "all")),
            }
            for row in contexts[:3]
        ]
        prompt = self._build_answer_prompt(
            query,
            contexts,
            evidence,
            question_type=question_type,
            material_text=material_text,
            hierarchy_context=hierarchy_context,
        )
        chat_messages = self._build_answer_chat_messages(prompt)
        retry_used = False
        try:
            resp = await self.ai_router.chat_with_task(
                messages=chat_messages,
                task_type="exam_answer",
                max_tokens=520,
                temperature=0.2,
                prefer_free=False,
            )
            if self._should_retry_chat_response(resp):
                retry_used = True
                resp = await self._retry_question_chat_after_backoff(
                    chat_messages,
                    max_tokens=520,
                    temperature=0.2,
                )
        except Exception as exc:
            if self._is_rate_limited_error(exc):
                retry_used = True
                try:
                    resp = await self._retry_question_chat_after_backoff(
                        chat_messages,
                        max_tokens=520,
                        temperature=0.2,
                    )
                except Exception as retry_exc:
                    return self._build_answer_failure_result(item, retry_exc)
            else:
                return self._build_answer_failure_result(item, exc)

        if self._should_retry_chat_response(resp):
            return self._build_answer_failure_result(item, RuntimeError("answer provider unavailable after retry"))

        structured = self._parse_answer_contract(str(resp.get("content", "")))
        answer_text = structured["answer"] or "未生成有效作答。"
        brief_reasoning = self._sanitize_brief_reasoning(structured["brief_reasoning"])
        strategy = self._normalize_strategy(structured["answer_strategy"])
        qa_gates = self._build_qa_gates(
            answer=answer_text,
            brief_reasoning=brief_reasoning,
            evidence=evidence,
            strategy=strategy,
        )
        return {
            **item,
            "ai_answer": answer_text,
            "brief_reasoning": brief_reasoning,
            "evidence": evidence,
            "answer_strategy": strategy,
            "qa_gates": qa_gates,
            "answer_provider": str(resp.get("provider") or ""),
            "retry_used": retry_used,
            "agent_chain_fallback": agent_chain_error is not None,
        }

    def _is_rate_limited_error(self, exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        message = str(exc).strip().lower()
        return status_code == 429 or "429" in message or "too many requests" in message or "rate limit" in message

    def _should_retry_chat_response(self, resp: Dict[str, Any]) -> bool:
        provider = str(resp.get("provider") or "").strip().lower()
        content = str(resp.get("content") or "").strip()
        if not content:
            return True
        return provider in {"none", "hash-fallback"}

    def _backup_answer_provider_order(self) -> List[str]:
        return ["transformers-local", "huggingface", "github-models", "zhipu", "gemini"]

    def _build_answer_chat_messages(self, prompt: str) -> List[Dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are an exam answer assistant. "
                    "Return valid JSON only. "
                    "Do not reveal chain-of-thought or step-by-step internal reasoning."
                ),
            },
            {"role": "user", "content": prompt},
        ]

    async def _retry_question_retrieval_after_backoff(
        self,
        *,
        query: str,
        discipline: str,
        tenant_id: str,
        billing_client_id: str,
        billing_exempt: bool,
    ) -> Dict[str, Any]:
        await asyncio.sleep(self._ANSWER_RATE_LIMIT_RETRY_DELAY_SECONDS)
        return await self.rag_engine.hybrid_search(
            query=query,
            discipline_filter=discipline,
            top_k=4,
            tenant_id=tenant_id,
            billing_client_id=billing_client_id,
            billing_exempt=billing_exempt,
            embedding_mode="local",
        )

    async def _retry_question_chat_after_backoff(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
    ) -> Dict[str, Any]:
        if self.ai_router is None:
            return {"provider": "none", "content": "", "task_type": "exam_retry"}
        await asyncio.sleep(self._ANSWER_RATE_LIMIT_RETRY_DELAY_SECONDS)
        return await self.ai_router.chat_with_provider_order_override(
            messages=messages,
            provider_order=self._backup_answer_provider_order(),
            task_type="exam_retry",
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def _build_answer_failure_result(self, item: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
        status_code = getattr(exc, "status_code", None)
        message = str(exc).strip()
        lowered = message.lower()
        is_rate_limited = status_code == 429 or "429" in lowered or "too many requests" in lowered

        if is_rate_limited:
            answer_text = "Auto-answer temporarily failed because the upstream model is rate limited. Please retry later."
            brief_reasoning = ["This question hit an upstream rate limit, so the exam upload can continue with a fallback result."]
            fallback_reason = "rate_limited"
        else:
            answer_text = "Auto-answer temporarily failed for this question. Please retry later."
            brief_reasoning = ["This question encountered a transient model or retrieval error, so a fallback result was returned."]
            fallback_reason = "answer_generation_failed"

        strategy = self._empty_strategy()
        qa_gates = self._build_qa_gates(answer_text, brief_reasoning, [], strategy)
        failed_checks = list(qa_gates.get("failed_checks", []))
        if fallback_reason not in failed_checks:
            failed_checks.append(fallback_reason)
        qa_gates["passed"] = False
        qa_gates["failed_checks"] = failed_checks

        return {
            **item,
            "ai_answer": answer_text,
            "brief_reasoning": brief_reasoning,
            "evidence": [],
            "answer_strategy": strategy,
            "qa_gates": qa_gates,
            "fallback_reason": fallback_reason,
            "error_detail": message[:240],
        }

    def _build_group_question_summary(
        self,
        item: Dict[str, Any],
        answered_children: List[Dict[str, Any]],
        path_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        child_titles = [
            f"Q{child.get('number_path', child.get('id', ''))} {self._question_preview(child.get('text', ''))}"
            for child in answered_children[:4]
        ]
        focus_lines = self._collect_unique_lines(
            [line for child in answered_children for line in child.get("brief_reasoning", [])],
            limit=3,
        )
        answer_parts = [f"本题为题组，共 {len(answered_children)} 个子题，建议按顺序递归作答。"]
        if child_titles:
            answer_parts.append("优先处理：" + "；".join(child_titles))
        if focus_lines:
            answer_parts.append("共性抓手：" + "；".join(focus_lines[:2]))
        answer_text = "\n".join(answer_parts).strip()
        evidence = self._aggregate_child_evidence(answered_children, limit=4)
        strategy = {
            "concept_induction": self._join_or_default(
                [child.get("answer_strategy", {}).get("concept_induction", "") for child in answered_children],
                "先识别大题要求，再定位每个子题的考点。",
            ),
            "information_compression": self._join_or_default(
                [child.get("answer_strategy", {}).get("information_compression", "") for child in answered_children],
                "提炼各子题共享条件与差异条件，避免重复阅读。",
            ),
            "reverse_check": self._join_or_default(
                [child.get("answer_strategy", {}).get("reverse_check", "") for child in answered_children],
                "回看子题答案是否与总题干要求冲突。",
            ),
            "distractor_design": self._join_or_default(
                [child.get("answer_strategy", {}).get("distractor_design", "") for child in answered_children],
                "注意区分总题干公共条件与各子题局部条件。",
            ),
        }
        qa_gates = self._build_qa_gates(answer_text, focus_lines or ["已结合子题结果做递归汇总。"], evidence, strategy)
        qa_gates["child_pass_rate"] = self._aggregate_child_pass_rate(answered_children)
        qa_gates["passed"] = bool(qa_gates.get("passed")) and qa_gates["child_pass_rate"] >= 0.5
        if qa_gates["child_pass_rate"] < 1.0:
            failed = list(qa_gates.get("failed_checks", []))
            if "subquestion_consistency" not in failed:
                failed.append("subquestion_consistency")
            qa_gates["failed_checks"] = failed
        return {
            **item,
            "ai_answer": answer_text,
            "brief_reasoning": focus_lines or ["本题需要先理解总题干，再逐层拆解到子题。"],
            "evidence": evidence,
            "answer_strategy": strategy,
            "qa_gates": qa_gates,
            "agent_trace": self._aggregate_child_traces(answered_children),
            "cost_profile": {"mode": "recursive-summary", "source": "child-aggregation"},
        }

    def _build_question_tree(self, questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not questions:
            return []
        items = [dict(item) for item in questions]
        path_map = {
            str(item.get("number_path") or item.get("id") or "").strip(): item
            for item in items
            if str(item.get("number_path") or item.get("id") or "").strip()
        }
        children_map: Dict[str, List[Dict[str, Any]]] = {path: [] for path in path_map}
        roots: List[Dict[str, Any]] = []
        for item in items:
            path = str(item.get("number_path") or item.get("id") or "").strip()
            parent_path = str(item.get("parent_path") or "").strip()
            if parent_path and parent_path in path_map:
                children_map.setdefault(parent_path, []).append(item)
            elif path:
                roots.append(item)
        for child_items in children_map.values():
            child_items.sort(key=self._question_sort_key)
        roots.sort(key=self._question_sort_key)

        def build_node(item: Dict[str, Any]) -> Dict[str, Any]:
            path = str(item.get("number_path") or item.get("id") or "").strip()
            child_nodes = [build_node(child) for child in children_map.get(path, [])]
            summary = str(item.get("subtree_summary") or item.get("ai_answer") or "").strip()
            return {
                "id": item.get("id"),
                "number_path": path,
                "title": self._question_preview(item.get("text", ""), limit=88),
                "level": int(item.get("level", 1) or 1),
                "question_type": str(item.get("question_type", "standard")),
                "difficulty_level": str(item.get("difficulty_level", "")),
                "node_kind": str(item.get("node_kind", "group" if child_nodes else "leaf")),
                "child_count": len(child_nodes),
                "summary": summary[:180],
                "children": child_nodes,
            }

        return [build_node(root) for root in roots]

    def _summarize_exam_structure(
        self,
        questions: List[Dict[str, Any]],
        question_tree: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        total = len(questions)
        leaf_count = sum(1 for item in questions if not item.get("child_count"))
        max_depth = max((int(item.get("level", 1) or 1) for item in questions), default=1)
        type_counter = Counter(str(item.get("question_type", "standard")) for item in questions)
        dominant_types = [name for name, _ in type_counter.most_common(3)]
        lines = [
            f"共 {total} 题，叶子题 {leaf_count} 题，最大层级 {max_depth} 层。",
            f"主导题型：{' / '.join(dominant_types) if dominant_types else 'standard'}。",
        ]
        root_titles = [str(node.get("title", "")).strip() for node in question_tree[:4] if str(node.get("title", "")).strip()]
        if root_titles:
            lines.append("顶层结构：" + "；".join(root_titles))
        return {
            "total_questions": total,
            "leaf_questions": leaf_count,
            "max_depth": max_depth,
            "dominant_types": dominant_types,
            "lines": lines,
        }

    def _infer_exam_profile(
        self,
        questions: List[Dict[str, Any]],
        question_tree: List[Dict[str, Any]],
        discipline: str,
    ) -> Dict[str, Any]:
        max_depth = max((int(item.get("level", 1) or 1) for item in questions), default=1)
        has_material = any(bool(item.get("material_text")) for item in questions)
        type_counter = Counter(str(item.get("question_type", "standard")) for item in questions)
        dominant_types = [name for name, _ in type_counter.most_common(3)]
        if has_material and max_depth >= 2:
            label = "材料题 / 复合题结构"
        elif max_depth >= 3:
            label = "分层递归试卷"
        else:
            label = "标准试卷结构"
        return {
            "label": label,
            "discipline": discipline,
            "root_count": len(question_tree),
            "max_depth": max_depth,
            "dominant_types": dominant_types,
            "has_material_group": has_material,
        }

    def _build_question_context(
        self,
        item: Dict[str, Any],
        path_map: Dict[str, Dict[str, Any]],
        children_map: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        path = str(item.get("number_path") or item.get("id") or "").strip()
        lines = [
            f"当前题号: Q{path or item.get('id', '')}",
            f"当前题型: {item.get('question_type', 'standard')}",
            f"当前层级: {int(item.get('level', 1) or 1)}",
        ]
        section_title = str(item.get("section_title") or "").strip()
        if section_title:
            lines.append(f"所属大题: {section_title}")

        ancestors = self._ancestor_chain(item, path_map)
        if ancestors:
            lines.append("上级题目:")
            for ancestor in ancestors[-3:]:
                lines.append(f"- Q{ancestor.get('number_path', '')}: {self._question_preview(ancestor.get('text', ''), limit=72)}")

        children = children_map.get(path, [])
        if children:
            lines.append("下级子题:")
            for child in children[:5]:
                lines.append(f"- Q{child.get('number_path', '')}: {self._question_preview(child.get('text', ''), limit=72)}")

        return "\n".join(line for line in lines if line).strip()

    def _build_retrieval_query(self, item: Dict[str, Any], path_map: Dict[str, Dict[str, Any]]) -> str:
        parts = [str(item.get("text", "")).strip()]
        for ancestor in self._ancestor_chain(item, path_map)[-2:]:
            ancestor_text = self._question_preview(ancestor.get("text", ""), limit=80)
            if ancestor_text:
                parts.append(ancestor_text)
        material_text = str(item.get("material_text") or "").strip()
        if material_text:
            parts.append(self._question_preview(material_text, limit=140))
        return "\n".join(part for part in parts if part)

    def _build_agent_query(
        self,
        question: str,
        question_type: str,
        material_text: str = "",
        hierarchy_context: str = "",
    ) -> str:
        hint = self._ANSWER_STRATEGY_HINTS.get(question_type, "")
        parts = [question]
        if hierarchy_context:
            parts.append(f"\n\n【题目结构】\n{hierarchy_context[:900]}")
        if material_text:
            parts.append(f"\n\n【相关材料】\n{material_text[:1200]}")
        if hint:
            parts.append(f"\n\n{hint}\n输出仍需遵循简版思路与证据可追溯。")
        return "".join(parts)

    def _build_answer_prompt(
        self,
        question: str,
        contexts: List[Dict[str, Any]],
        evidence: List[Dict[str, str]],
        question_type: str = "standard",
        material_text: str = "",
        hierarchy_context: str = "",
    ) -> str:
        snippets = []
        for row in contexts[:4]:
            snippets.append(
                f"[{row.get('title', '未命名来源')}::{row.get('section_path', 'N/A')}]\n{row.get('content', '')}"
            )
        context_text = "\n\n".join(snippets) if snippets else "暂无检索上下文"
        evidence_text = "；".join(
            f"{e.get('title', '未命名来源')}({e.get('section_path', 'N/A')})"
            for e in evidence[:3]
        ) or "暂无明确证据"
        output_contract = (
            "{\n"
            '  "answer": "最终答案，1-3句",\n'
            '  "brief_reasoning": ["简版思路1", "简版思路2", "简版思路3(可选)"],\n'
            '  "answer_strategy": {\n'
            '    "concept_induction": "题目意图与考点归纳",\n'
            '    "information_compression": "证据压缩后的关键依据",\n'
            '    "reverse_check": "反向检验是否与答案冲突",\n'
            '    "distractor_design": "易错点说明或排除逻辑"\n'
            "  }\n"
            "}"
        )
        type_hint = self._ANSWER_STRATEGY_HINTS.get(question_type, "")
        sections: List[str] = [
            "请回答以下题目，并按约定输出 JSON。",
            "必须体现：先理解题目层级，再压缩信息，再做反向检验。",
        ]
        if type_hint:
            sections.append(type_hint)
        if hierarchy_context:
            sections.append(f"【题目树上下文】\n{hierarchy_context[:1200]}")
        if material_text:
            sections.append(f"【相关材料】\n{material_text[:1200]}")
        sections.extend(
            [
                f"题目：{question}",
                f"证据来源：{evidence_text}",
                f"参考资料：\n{context_text}",
                f"返回结构：\n{output_contract}",
            ]
        )
        if PromptTemplate is not None:
            template = PromptTemplate.from_template("{body}")
            return template.format(body="\n\n".join(part for part in sections if part))
        return "\n\n".join(part for part in sections if part)

    def _build_subtree_summary(self, item: Dict[str, Any], answered_children: List[Dict[str, Any]]) -> str:
        if answered_children:
            head = str(item.get("ai_answer", "")).strip()
            if head:
                return head[:180]
            child_titles = [f"Q{child.get('number_path', '')}" for child in answered_children[:4]]
            return f"题组概览：{' / '.join(child_titles)}"
        answer = str(item.get("ai_answer", "")).strip()
        return answer[:180] if answer else self._question_preview(item.get("text", ""), limit=120)

    def _aggregate_child_evidence(self, answered_children: List[Dict[str, Any]], limit: int = 4) -> List[Dict[str, str]]:
        merged: List[Dict[str, str]] = []
        seen = set()
        for child in answered_children:
            for evidence in child.get("evidence", []) or []:
                key = (
                    str(evidence.get("title", "")).strip(),
                    str(evidence.get("section_path", "")).strip(),
                    str(evidence.get("discipline", "")).strip(),
                )
                if key in seen or not key[0]:
                    continue
                seen.add(key)
                merged.append(
                    {
                        "title": key[0],
                        "section_path": key[1] or "N/A",
                        "discipline": key[2] or "all",
                    }
                )
                if len(merged) >= limit:
                    return merged
        return merged

    def _aggregate_child_traces(self, answered_children: List[Dict[str, Any]], limit: int = 8) -> List[str]:
        traces: List[str] = []
        seen = set()
        for child in answered_children:
            for step in child.get("agent_trace", []) or []:
                step_text = str(step).strip()
                if not step_text or step_text in seen:
                    continue
                seen.add(step_text)
                traces.append(step_text)
                if len(traces) >= limit:
                    return traces
        return traces

    def _aggregate_child_pass_rate(self, answered_children: List[Dict[str, Any]]) -> float:
        if not answered_children:
            return 1.0
        passed = 0
        for child in answered_children:
            gates = child.get("qa_gates", {}) if isinstance(child, dict) else {}
            if isinstance(gates, dict) and gates.get("passed"):
                passed += 1
        return round(passed / len(answered_children), 3)

    def _ancestor_chain(
        self,
        item: Dict[str, Any],
        path_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        chain: List[Dict[str, Any]] = []
        parent_path = str(item.get("parent_path") or "").strip()
        guard = 0
        while parent_path and parent_path in path_map and guard < 12:
            parent = path_map[parent_path]
            chain.append(parent)
            parent_path = str(parent.get("parent_path") or "").strip()
            guard += 1
        chain.reverse()
        return chain

    def _question_sort_key(self, item: Dict[str, Any]) -> Tuple[Any, ...]:
        path = str(item.get("number_path") or item.get("id") or "").strip()
        return self._number_path_key(path)

    def _number_path_key(self, value: str) -> Tuple[Any, ...]:
        parts = [part for part in str(value or "").split(".") if part]
        key: List[Any] = []
        for part in parts:
            if part.isdigit():
                key.append(int(part))
            else:
                key.append(part)
        return tuple(key or [999999])

    def _question_preview(self, text: Any, limit: int = 96) -> str:
        preview = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(preview) <= limit:
            return preview
        return f"{preview[: max(8, limit - 1)].rstrip()}…"

    def _collect_unique_lines(self, items: List[Any], limit: int = 3) -> List[str]:
        out: List[str] = []
        seen = set()
        for raw in items:
            text = self._question_preview(raw, limit=120)
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
            if len(out) >= limit:
                break
        return out

    def _join_or_default(self, items: List[Any], default: str, limit: int = 2) -> str:
        lines = self._collect_unique_lines(items, limit=limit)
        return "；".join(lines) if lines else default

    def _parse_answer_contract(self, text: str) -> Dict[str, Any]:
        raw = (text or "").strip()
        if not raw:
            return {"answer": "", "brief_reasoning": [], "answer_strategy": self._empty_strategy()}

        parsed = FreeAIRouter.safe_json_loads(raw, None)
        if not isinstance(parsed, dict):
            cleaned = raw
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if len(lines) >= 2 and lines[0].strip().startswith("```"):
                    lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    cleaned = "\n".join(lines).strip()
            if cleaned:
                parsed = FreeAIRouter.safe_json_loads(cleaned, None)

        if not isinstance(parsed, dict):
            return {
                "answer": self._sanitize_answer(raw),
                "brief_reasoning": [],
                "answer_strategy": self._empty_strategy(),
            }

        return {
            "answer": self._sanitize_answer(str(parsed.get("answer", ""))),
            "brief_reasoning": parsed.get("brief_reasoning", []),
            "answer_strategy": parsed.get("answer_strategy", {}),
        }

    def _sanitize_answer(self, value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        lowered = text.lower()
        leakage_flags = [
            "chain of thought",
            "逐步推理",
            "完整推理",
            "内部思考",
            "let's think step by step",
        ]
        if any(flag in lowered for flag in leakage_flags):
            return "已生成答案，但为避免泄露完整推理链，仅保留结论与可验证依据。"
        return text[:500]

    def _sanitize_brief_reasoning(self, value: Any) -> List[str]:
        items: List[str]
        if isinstance(value, list):
            items = [str(x).strip() for x in value]
        elif isinstance(value, str):
            items = [x.strip() for x in re.split(r"[\n;；]+", value)]
        else:
            items = []
        out: List[str] = []
        for text in items:
            if not text:
                continue
            lowered = text.lower()
            if any(
                flag in lowered
                for flag in ["chain of thought", "逐步推理", "完整推理", "内部思考", "let's think step by step"]
            ):
                continue
            out.append(text[:120])
            if len(out) >= 3:
                break
        if not out:
            out = ["基于检索证据提炼结论，优先保留可验证依据。"]
        return out

    def _normalize_strategy(self, value: Any) -> Dict[str, str]:
        base = self._empty_strategy()
        if not isinstance(value, dict):
            return base
        for key in base:
            text = str(value.get(key, "")).strip()
            base[key] = text[:220] if text else base[key]
        return base

    def _empty_strategy(self) -> Dict[str, str]:
        return {
            "concept_induction": "待补充题目意图与考点。",
            "information_compression": "待补充证据压缩结果。",
            "reverse_check": "待执行反向检验。",
            "distractor_design": "待补充干扰项设计说明。",
        }

    def _build_qa_gates(
        self,
        answer: str,
        brief_reasoning: List[str],
        evidence: List[Dict[str, str]],
        strategy: Dict[str, str],
    ) -> Dict[str, Any]:
        evidence_traceable = bool(evidence) and all(
            str(item.get("title", "")).strip() and str(item.get("section_path", "")).strip() for item in evidence
        )
        reasoning_visible = 1 <= len(brief_reasoning) <= 3 and all(
            "逐步推理" not in x and "完整推理" not in x and "chain of thought" not in x.lower() for x in brief_reasoning
        )
        consistency = bool(answer.strip()) and all(
            bool(str(strategy.get(k, "")).strip())
            for k in ("concept_induction", "information_compression", "reverse_check", "distractor_design")
        )
        distractor_design_present = bool(str(strategy.get("distractor_design", "")).strip())
        failures = []
        if not consistency:
            failures.append("consistency")
        if not evidence_traceable:
            failures.append("evidence_traceable")
        if not reasoning_visible:
            failures.append("reasoning_visibility")
        if not distractor_design_present:
            failures.append("distractor_design")
        return {
            "consistency": consistency,
            "evidence_traceable": evidence_traceable,
            "reasoning_visibility": reasoning_visible,
            "distractor_design": distractor_design_present,
            "passed": len(failures) == 0,
            "failed_checks": failures,
        }

    def _aggregate_regression_gates(self, questions: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not questions:
            return {
                "consistency_pass_rate": 0.0,
                "evidence_traceable_pass_rate": 0.0,
                "reasoning_visibility_pass_rate": 0.0,
                "overall_pass_rate": 0.0,
            }
        total = len(questions)
        consistency_ok = 0
        evidence_ok = 0
        reasoning_ok = 0
        overall_ok = 0
        for q in questions:
            gates = q.get("qa_gates", {}) if isinstance(q, dict) else {}
            if gates.get("consistency"):
                consistency_ok += 1
            if gates.get("evidence_traceable"):
                evidence_ok += 1
            if gates.get("reasoning_visibility"):
                reasoning_ok += 1
            if gates.get("passed"):
                overall_ok += 1
        return {
            "consistency_pass_rate": round(consistency_ok / total, 3),
            "evidence_traceable_pass_rate": round(evidence_ok / total, 3),
            "reasoning_visibility_pass_rate": round(reasoning_ok / total, 3),
            "overall_pass_rate": round(overall_ok / total, 3),
        }
