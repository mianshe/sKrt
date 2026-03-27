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

    async def analyze_exam(self, exam_text: str, discipline: str = "all", tenant_id: str = "public") -> Dict[str, Any]:
        questions = self._split_questions(exam_text)
        stats = self._difficulty_stats(questions)
        recommendations = await self._recommend(questions, discipline, tenant_id=tenant_id)
        return {
            "question_count": len(questions),
            "difficulty": stats,
            "questions": questions,
            "recommendations": recommendations,
        }

    async def analyze_and_answer_exam(
        self, exam_text: str, discipline: str = "all", tenant_id: str = "public"
    ) -> Dict[str, Any]:
        analysis = await self.analyze_exam(exam_text, discipline, tenant_id=tenant_id)
        answered_questions = await self._answer_questions(analysis.get("questions", []), discipline, tenant_id=tenant_id)
        analysis["questions"] = answered_questions
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
                parent_paths[level] = number_path
            else:
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

    def _split_blocks(self, text: str) -> List[str]:
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
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

    async def _recommend(self, questions: List[Dict[str, Any]], discipline: str, tenant_id: str = "public") -> List[Dict[str, Any]]:
        if not questions:
            return []
        query = " ".join(q["text"] for q in questions[:3])[:1200]
        found = await self.rag_engine.hybrid_search(query=query, discipline_filter=discipline, top_k=5, tenant_id=tenant_id)
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

    async def _answer_questions(
        self, questions: List[Dict[str, Any]], discipline: str, tenant_id: str = "public"
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
                    query=enriched_query, discipline=discipline, tenant_id=tenant_id, question_type=question_type
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
            retrieval = await self.rag_engine.hybrid_search(query=query, discipline_filter=discipline, top_k=4)
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
        hint = self._ANSWER_STRATEGY_HINTS.get(question_type, "")
        parts = [question]
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
        type_hint = self._ANSWER_STRATEGY_HINTS.get(question_type, "")
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
