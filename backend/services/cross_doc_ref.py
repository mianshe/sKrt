"""
跨文档引用解析器

分析文档间的引用关系，构建概念网络，支持教学路径分析
"""

import re
import json
from typing import List, Dict, Set, Any, Optional, Tuple
from collections import defaultdict, namedtuple
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class DocumentReference:
    """文档引用"""
    source_doc_id: int
    source_chunk_id: str
    target_type: str  # "figure", "table", "section", "document", "equation", "reference"
    target_label: str
    target_doc_id: Optional[int] = None  # 跨文档引用时不为None
    target_page: Optional[int] = None
    target_chapter: Optional[str] = None
    target_section: Optional[str] = None
    confidence: float = 0.8


@dataclass
class DocumentTerm:
    """文档术语"""
    doc_id: int
    chunk_id: str
    term: str
    term_type: str  # "concept", "method", "tool", "technique", "entity"
    frequency: int = 1


@dataclass 
class CrossDocumentRelation:
    """跨文档关系"""
    source_doc_id: int
    target_doc_id: int
    relation_type: str  # "cites", "extends", "contradicts", "complements", "references"
    strength: float = 0.5
    evidence: List[str] = field(default_factory=list)
    shared_terms: List[str] = field(default_factory=list)


@dataclass
class Concept:
    """教学概念"""
    concept_id: str
    concept_type: str  # "definition", "example", "application", "explanation", "summary", "problem", "general"
    content: str
    references_to: List[str] = field(default_factory=list)  # 引用其他概念的ID
    referenced_by: List[str] = field(default_factory=list)  # 被哪些概念引用
    metadata: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.7


class CrossDocumentReferenceParser:
    """
    跨文档引用解析器，支持概念分析和教学路径评估
    """
    
    def __init__(self, enable_concept_analysis: bool = True):
        self.enable_concept_analysis = enable_concept_analysis
        
        # 文档注册表
        self.documents: Dict[int, Dict[str, Any]] = {}
        
        # 引用和术语存储
        self.references: List[DocumentReference] = []
        self.terms: List[DocumentTerm] = []
        
        # 概念注册表（如果启用概念分析）
        self.concept_registry: Dict[str, Concept] = {}
        self.next_concept_id: int = 1
        
        # 初始化正则表达式模式
        self._init_patterns()
        
        # 概念类型权重（用于教学路径分析）
        self.concept_weights = {
            "definition": 0.30,
            "explanation": 0.15,
            "example": 0.20,
            "application": 0.20,
            "summary": 0.10,
            "problem": 0.05,
        }
    
    def _init_patterns(self):
        """初始化正则表达式模式"""
        # 图表引用模式
        self.figure_patterns = [
            r'如图\s*(\d+\.\d+|\d+)',
            r'图\s*(\d+\.\d+|\d+)(?:[\(（].*?[\)）])?\s*(?:所示|显示|展示)',
            r'Figure\s*(\d+\.\d+|\d+)\s*(?:shows|displays|illustrates)',
            r'参见图\s*(\d+\.\d+|\d+)',
        ]
        
        # 表格引用模式
        self.table_patterns = [
            r'表\s*(\d+\.\d+|\d+)(?:[\(（].*?[\)）])?',
            r'Table\s*(\d+\.\d+|\d+)',
            r'如表\s*(\d+\.\d+|\d+)(?:所示|显示)',
        ]
        
        # 章节引用模式
        self.section_patterns = [
            r'第\s*[一二三四五六七八九十\d]+\s*章',
            r'第\s*[一二三四五六七八九十\d]+\s*节',
            r'Section\s*\d+(?:\.\d+)*',
            r'Chapter\s*\d+',
        ]
        
        # 文献引用模式
        self.reference_patterns = [
            r'参考文献\s*\[([^\]]+)\]',
            r'文献\s*\[([^\]]+)\]',
            r'参考文献\s*\d+(?:\s*,\s*\d+)*',
            r'\[(\d+(?:\s*,\s*\d+)*)\]',
            r'\(([A-Z][a-z]+,\s*\d{4})\)',
        ]
        
        # 文档间引用模式
        self.cross_doc_patterns = [
            r'参见《([^》]{2,50})》',
            r'参考《([^》]{2,50})》',
            r'详见《([^》]{2,50})》',
            r'《([^》]{2,50})》中(?:提到|指出|说明|阐述)',
            r'参见文献《([^》]{2,50})》',
        ]
        
        # 术语识别模式
        self.term_patterns = [
            r'\*\*(.+?)\*\*',  # 加粗文本
            r'``(.+?)``',      # 代码标记
            r'《([^》]{2,30})》',  # 书名号
            r'"([^"]{2,30})"',    # 双引号
        ]
        
        # 概念提取模式
        self.concept_patterns = {
            "definition": [
                r'(?:定义为|是指|称为|叫做|即|指的是|定义为|定义作|Definition|Definition:|define[sd]?|defined as)\s*[""]?([^"",。；\n]{2,30})[""]?',
                r'(\S+)\s*(?:是|指|指的是|就是|即为)\s*',
            ],
            "example": [
                r'(?:例如|比如|举例|例\d+|例题|示例|Example|Example:|example[s]?|for example|such as|e\.g\.)\s*[""]?([^"",。；\n]{2,50})[""]?',
                r'(?:如图\s*\d+|表\s*\d+所示).*?(?:说明|展示|描述了?)\s*([^。；\n]{2,50})',
            ],
            "application": [
                r'(?:应用|运用|使用|可用于|适用于|应用领域|应用场景|Application|Application:|apply|applications|usage|use case)\s*[""]?([^"",。；\n]{2,50})[""]?',
                r'(?:实际应用|实践应用|工程应用|商业应用)\s*(?:中|方面)?[:：]?\s*([^。；\n]{2,50})',
            ],
            "explanation": [
                r'(?:解释|说明|阐述|详述|Explanation|Explanation:|explain|explanation)\s*[""]?([^"",。；\n]{2,50})[""]?',
                r'([^。；\n]{2,30})\s*(?:的)?(?:原因|原理|机制|工作原理|工作流程)',
            ],
            "problem": [
                r'(?:问题|疑问|难点|挑战|Question|Problem|problem[s]?|question[s]?|issue[s]?)\s*[:：]\s*([^。；\n]{2,50})',
                r'(?:思考题|练习题|作业题|习题)\s*[\d\.]+\s*[:：]?\s*([^。；\n]{2,50})',
            ],
        }
    
    def register_document(self, doc_id: int, metadata: Dict[str, Any]) -> None:
        """注册文档"""
        self.documents[doc_id] = metadata
    
    def parse_chunk(self, doc_id: int, chunk_id: str, content: str) -> Tuple[List[DocumentReference], List[DocumentTerm]]:
        """解析chunk，提取引用和术语"""
        references = []
        terms = []
        
        # 提取图表引用
        references.extend(self._extract_figure_references(doc_id, chunk_id, content))
        
        # 提取表格引用
        references.extend(self._extract_table_references(doc_id, chunk_id, content))
        
        # 提取章节引用
        references.extend(self._extract_section_references(doc_id, chunk_id, content))
        
        # 提取文献引用
        references.extend(self._extract_reference_references(doc_id, chunk_id, content))
        
        # 提取文档间引用
        references.extend(self._extract_cross_doc_references(doc_id, chunk_id, content))
        
        # 提取术语
        terms.extend(self._extract_terms(doc_id, chunk_id, content))
        
        # 如果启用概念分析，提取概念
        if self.enable_concept_analysis:
            self._extract_and_register_concepts(doc_id, chunk_id, content)
        
        return references, terms
    
    def _extract_figure_references(self, doc_id: int, chunk_id: str, content: str) -> List[DocumentReference]:
        """提取图表引用"""
        references = []
        
        for pattern in self.figure_patterns:
            for match in re.finditer(pattern, content):
                figure_num = match.group(1)
                ref = DocumentReference(
                    source_doc_id=doc_id,
                    source_chunk_id=chunk_id,
                    target_type="figure",
                    target_label=f"图{figure_num}",
                    target_doc_id=doc_id,
                    confidence=0.9
                )
                references.append(ref)
        
        return references
    
    def _extract_table_references(self, doc_id: int, chunk_id: str, content: str) -> List[DocumentReference]:
        """提取表格引用"""
        references = []
        
        for pattern in self.table_patterns:
            for match in re.finditer(pattern, content):
                table_num = match.group(1)
                ref = DocumentReference(
                    source_doc_id=doc_id,
                    source_chunk_id=chunk_id,
                    target_type="table",
                    target_label=f"表{table_num}",
                    target_doc_id=doc_id,
                    confidence=0.9
                )
                references.append(ref)
        
        return references
    
    def _extract_section_references(self, doc_id: int, chunk_id: str, content: str) -> List[DocumentReference]:
        """提取章节引用"""
        references = []
        
        for pattern in self.section_patterns:
            for match in re.finditer(pattern, content):
                section_label = match.group(0)
                ref = DocumentReference(
                    source_doc_id=doc_id,
                    source_chunk_id=chunk_id,
                    target_type="section",
                    target_label=section_label,
                    target_doc_id=doc_id,
                    confidence=0.8
                )
                references.append(ref)
        
        return references
    
    def _extract_reference_references(self, doc_id: int, chunk_id: str, content: str) -> List[DocumentReference]:
        """提取文献引用"""
        references = []
        
        for pattern in self.reference_patterns:
            for match in re.finditer(pattern, content):
                ref_label = match.group(1) if match.groups() else match.group(0)
                ref = DocumentReference(
                    source_doc_id=doc_id,
                    source_chunk_id=chunk_id,
                    target_type="reference",
                    target_label=ref_label,
                    confidence=0.7
                )
                references.append(ref)
        
        return references
    
    def _extract_cross_doc_references(self, doc_id: int, chunk_id: str, content: str) -> List[DocumentReference]:
        """提取文档间引用"""
        references = []
        
        for pattern in self.cross_doc_patterns:
            for match in re.finditer(pattern, content):
                doc_title = match.group(1)
                
                # 尝试匹配已注册的文档
                target_doc_id = None
                for registered_id, metadata in self.documents.items():
                    if registered_id != doc_id and metadata.get('title', '').find(doc_title) != -1:
                        target_doc_id = registered_id
                        break
                
                ref = DocumentReference(
                    source_doc_id=doc_id,
                    source_chunk_id=chunk_id,
                    target_type="document",
                    target_label=doc_title,
                    target_doc_id=target_doc_id,
                    confidence=0.6 if target_doc_id else 0.4
                )
                references.append(ref)
        
        return references
    
    def _extract_terms(self, doc_id: int, chunk_id: str, content: str) -> List[DocumentTerm]:
        """提取术语"""
        terms = []
        term_freq = defaultdict(int)
        
        for pattern in self.term_patterns:
            for match in re.finditer(pattern, content):
                term = match.group(1).strip()
                if len(term) >= 2 and len(term) <= 30:
                    term_freq[term] += 1
        
        # 识别术语类型
        for term, freq in term_freq.items():
            term_type = self._classify_term_type(term, content)
            term_obj = DocumentTerm(
                doc_id=doc_id,
                chunk_id=chunk_id,
                term=term,
                term_type=term_type,
                frequency=freq
            )
            terms.append(term_obj)
        
        return terms
    
    def _classify_term_type(self, term: str, context: str) -> str:
        """分类术语类型"""
        term_lower = term.lower()
        
        # 概念类
        if any(keyword in term_lower for keyword in ['算法', '模型', '理论', '原理', '概念', '定义']):
            return "concept"
        
        # 方法类
        if any(keyword in term_lower for keyword in ['方法', '技术', '策略', '方案', '流程', '步骤']):
            return "method"
        
        # 工具类
        if any(keyword in term_lower for keyword in ['工具', '软件', '系统', '平台', '框架', '库']):
            return "tool"
        
        # 实体类
        if re.search(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*', term):
            return "entity"
        
        return "general"
    
    def _extract_and_register_concepts(self, doc_id: int, chunk_id: str, content: str) -> None:
        """提取并注册概念"""
        if not self.enable_concept_analysis:
            return
        
        # 提取各种类型的概念
        for concept_type, patterns in self.concept_patterns.items():
            for pattern in patterns:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    concept_content = match.group(1).strip()
                    if concept_content and len(concept_content) >= 2:
                        concept_id = f"concept_{self.next_concept_id}"
                        self.next_concept_id += 1
                        
                        concept = Concept(
                            concept_id=concept_id,
                            concept_type=concept_type,
                            content=concept_content,
                            metadata={
                                "source_doc_id": doc_id,
                                "source_chunk_id": chunk_id,
                                "extraction_pattern": pattern,
                            }
                        )
                        
                        self.concept_registry[concept_id] = concept
    
    def build_cross_document_relations(self) -> List[CrossDocumentRelation]:
        """构建跨文档关系"""
        relations = []
        
        if len(self.documents) < 2:
            return relations
        
        # 分析文档间的引用关系
        cross_refs = [r for r in self.references if r.target_doc_id is not None]
        
        for ref in cross_refs:
            if ref.target_doc_id:
                # 检查是否已有关系
                existing = False
                for rel in relations:
                    if (rel.source_doc_id == ref.source_doc_id and 
                        rel.target_doc_id == ref.target_doc_id):
                        rel.evidence.append(f"引用: {ref.target_label}")
                        rel.strength = min(1.0, rel.strength + 0.1)
                        existing = True
                        break
                
                if not existing:
                    relation = CrossDocumentRelation(
                        source_doc_id=ref.source_doc_id,
                        target_doc_id=ref.target_doc_id,
                        relation_type="cites",
                        strength=0.6,
                        evidence=[f"引用: {ref.target_label}"]
                    )
                    relations.append(relation)
        
        # 分析共享术语
        for doc1_id in self.documents:
            for doc2_id in self.documents:
                if doc1_id >= doc2_id:
                    continue
                
                terms1 = {t.term for t in self.terms if t.doc_id == doc1_id}
                terms2 = {t.term for t in self.terms if t.doc_id == doc2_id}
                shared_terms = terms1.intersection(terms2)
                
                if shared_terms:
                    # 检查是否已有关系
                    existing = False
                    for rel in relations:
                        if (rel.source_doc_id == doc1_id and 
                            rel.target_doc_id == doc2_id) or \
                           (rel.source_doc_id == doc2_id and 
                            rel.target_doc_id == doc1_id):
                            rel.shared_terms.extend(list(shared_terms))
                            rel.strength = min(1.0, rel.strength + 0.05 * len(shared_terms))
                            existing = True
                            break
                    
                    if not existing:
                        relation = CrossDocumentRelation(
                            source_doc_id=doc1_id,
                            target_doc_id=doc2_id,
                            relation_type="complements",
                            strength=0.3 + 0.05 * len(shared_terms),
                            shared_terms=list(shared_terms)
                        )
                        relations.append(relation)
        
        return relations
    
    def analyze_concept_network(self) -> Dict[str, Any]:
        """分析概念网络"""
        if not self.enable_concept_analysis or not self.concept_registry:
            return {"total_concepts": 0, "concepts_by_type": {}}
        
        # 按类型统计概念
        concepts_by_type = defaultdict(list)
        for concept in self.concept_registry.values():
            concepts_by_type[concept.concept_type].append(concept.concept_id)
        
        # 计算概念连接性
        connected_concepts = 0
        for concept in self.concept_registry.values():
            if concept.references_to:
                connected_concepts += 1
        
        # 构建概念依赖图
        dependency_graph = {}
        for concept_id, concept in self.concept_registry.items():
            dependency_graph[concept_id] = concept.references_to
        
        return {
            "total_concepts": len(self.concept_registry),
            "concepts_by_type": dict(concepts_by_type),
            "connected_concepts": connected_concepts,
            "connectivity_rate": connected_concepts / len(self.concept_registry) if self.concept_registry else 0,
            "dependency_graph": dependency_graph,
        }

    def analyze_concept_references(self, concepts: Optional[List[Concept]] = None) -> Dict[str, Any]:
        """
        分析概念引用关系，评估教学路径质量
        
        参数:
            concepts: 概念列表，如果为None则使用注册表中的所有概念
            
        返回:
            概念引用分析结果
        """
        if not self.enable_concept_analysis:
            return {"enabled": False, "message": "概念分析未启用"}
        
        # 使用提供的概念或注册表中的所有概念
        if concepts is None:
            concepts = list(self.concept_registry.values())
        
        if not concepts:
            return {"total_concepts": 0, "concepts_by_type": {}, "analysis": {}}
        
        # 1. 分析教学关系模式
        teaching_patterns = self._analyze_teaching_patterns(concepts)
        
        # 2. 提取教学路径
        teaching_paths = self._extract_teaching_paths(concepts, max_depth=5)
        
        # 3. 评估概念完整性
        concept_completeness = self._evaluate_concept_completeness(concepts)
        
        # 4. 构建概念依赖图
        dependency_graph = self._build_concept_dependency_graph(concepts)
        
        # 5. 计算概念依赖深度
        max_depth = self._calculate_max_dependency_depth(dependency_graph)
        
        # 6. 统计孤立概念
        isolated_count = self._count_isolated_concepts(dependency_graph)
        
        # 7. 按类型统计概念
        concepts_by_type = self._count_concepts_by_type(concepts)
        
        # 8. 生成教学优化建议
        recommendations = self._generate_teaching_recommendations(concepts, teaching_patterns, concept_completeness)
        
        return {
            "total_concepts": len(concepts),
            "concepts_by_type": concepts_by_type,
            "teaching_patterns": teaching_patterns,
            "teaching_paths": teaching_paths,
            "concept_completeness": concept_completeness,
            "dependency_analysis": {
                "max_depth": max_depth,
                "isolated_concepts": isolated_count,
                "total_nodes": len(dependency_graph),
                "total_edges": sum(len(neighbors) for neighbors in dependency_graph.values()),
            },
            "recommendations": recommendations,
            "concept_registry_size": len(self.concept_registry),
        }
    
    # ============ 概念引用分析辅助方法 ============

    def _count_concepts_by_type(self, concepts: List[Concept]) -> Dict[str, int]:
        """统计概念类型分布"""
        counts = defaultdict(int)
        for concept in concepts:
            counts[concept.concept_type] += 1
        return dict(counts)

    def _analyze_teaching_patterns(self, concepts: List[Concept]) -> Dict[str, Any]:
        """分析概念之间的教学关系模式"""
        patterns = defaultdict(int)
        
        for concept in concepts:
            for target_id in concept.references_to:
                if target_id in self.concept_registry:
                    target = self.concept_registry[target_id]
                    # 构建教学关系模式
                    pattern_key = f"{concept.concept_type}->{target.concept_type}"
                    patterns[pattern_key] += 1
        
        # 常见教学路径
        common_patterns = sorted(patterns.items(), key=lambda x: x[1], reverse=True)
        
        return {
            "total_patterns": len(patterns),
            "common_patterns": common_patterns[:10],
            "pattern_frequency": dict(patterns),
        }

    def _extract_teaching_paths(self, concepts: List[Concept], max_depth: int) -> List[List[str]]:
        """提取概念引用链中的教学路径"""
        paths = []
        
        def dfs(current_id: str, path: List[str], visited: Set[str]) -> None:
            if len(path) >= max_depth or current_id in visited:
                # 如果路径太长或形成循环，停止
                if len(path) >= 2:  # 至少有两个节点的路径才有意义
                    paths.append(path.copy())
                return
            
            visited.add(current_id)
            
            # 从概念注册表中获取当前概念
            if current_id not in self.concept_registry:
                visited.remove(current_id)
                return
            
            current_concept = self.concept_registry[current_id]
            
            # 尝试所有引用的概念
            for next_id in current_concept.references_to:
                if next_id in self.concept_registry:
                    next_concept = self.concept_registry[next_id]
                    # 检查是否构成合理的教学顺序
                    if self._is_teaching_progression(current_concept.concept_type, next_concept.concept_type):
                        new_path = path + [current_id, next_id]
                        dfs(next_id, new_path, visited.copy())
            
            # 如果没有找到后续概念，且路径长度合适，保存路径
            if len(path) >= 2:
                paths.append(path.copy())
            
            visited.remove(current_id)
        
        # 从定义类概念开始搜索
        for concept in concepts:
            if concept.concept_type == "definition":
                dfs(concept.concept_id, [], set())
        
        # 排序：先按长度降序，再按概念类型序列的质量
        paths.sort(key=lambda p: (-len(p), sum(1 for concept_id in p if self.concept_registry.get(concept_id, Concept).concept_type != "general")))
        
        return paths[:20]  # 返回最多20条路径

    def _is_teaching_progression(self, from_type: str, to_type: str) -> bool:
        """检查两个概念类型之间是否构成合理的教学进展"""
        teaching_order = ["definition", "explanation", "example", "application", "summary", "problem"]
        
        try:
            from_idx = teaching_order.index(from_type) if from_type in teaching_order else -1
            to_idx = teaching_order.index(to_type) if to_type in teaching_order else -1
            
            # 允许正向进展或相同类型的概念关联
            if from_idx == -1 or to_idx == -1:
                return True  # 对于未知类型，允许任意关联
            return to_idx >= from_idx  # 正向或相同
        except ValueError:
            return True

    def _evaluate_concept_completeness(self, concepts: List[Concept]) -> Dict[str, float]:
        """评估概念教学的完整性"""
        if not concepts:
            return {}
        
        # 统计概念类型分布
        type_counts = self._count_concepts_by_type(concepts)
        total = len(concepts)
        
        # 理想的教学完整性：应该包含定义、解释、示例、应用
        essential_types = ["definition", "example", "application"]
        
        completeness_scores = {}
        for essential_type in essential_types:
            count = type_counts.get(essential_type, 0)
            completeness_scores[f"{essential_type}_coverage"] = count / max(1, total)
        
        # 计算总体完整性分数
        has_definition = type_counts.get("definition", 0) > 0
        has_example = type_counts.get("example", 0) > 0
        has_application = type_counts.get("application", 0) > 0
        
        # 权重：定义最重要，其次示例，最后应用
        weights = {"definition": 0.4, "example": 0.3, "application": 0.3}
        overall_score = 0.0
        
        for concept_type, weight in weights.items():
            count = type_counts.get(concept_type, 0)
            type_score = min(1.0, count / 2.0)  # 最多2个该类型概念就算满分
            overall_score += type_score * weight
        
        completeness_scores["overall_completeness"] = overall_score
        completeness_scores["missing_types"] = [t for t in essential_types if type_counts.get(t, 0) == 0]
        
        return completeness_scores

    def _build_concept_dependency_graph(self, concepts: List[Concept]) -> Dict[str, List[str]]:
        """构建概念依赖关系图"""
        graph = {concept.concept_id: [] for concept in concepts}
        
        for concept in concepts:
            for target_id in concept.references_to:
                if target_id in graph:
                    graph[concept.concept_id].append(target_id)
        
        return graph

    def _calculate_max_dependency_depth(self, graph: Dict[str, List[str]]) -> int:
        """计算概念依赖的最大深度"""
        def dfs(node: str, visited: Set[str], depth: int) -> int:
            if node in visited:
                return depth
            
            visited.add(node)
            max_depth = depth
            
            for neighbor in graph.get(node, []):
                max_depth = max(max_depth, dfs(neighbor, visited.copy(), depth + 1))
            
            return max_depth
        
        max_depth = 0
        for node in graph:
            depth = dfs(node, set(), 0)
            max_depth = max(max_depth, depth)
        
        return max_depth

    def _count_isolated_concepts(self, graph: Dict[str, List[str]]) -> int:
        """统计孤立的（无依赖关系）概念数量"""
        isolated = 0
        for node, neighbors in graph.items():
            if not neighbors and sum(1 for n in graph.values() if node in n) == 0:
                isolated += 1
        return isolated

    def _generate_teaching_recommendations(self, concepts: List[Concept], 
                                           patterns: Dict[str, Any], 
                                           completeness: Dict[str, float]) -> List[Dict[str, Any]]:
        """生成教学优化建议"""
        recommendations = []
        
        # 检查完整性
        missing_types = completeness.get("missing_types", [])
        for missing_type in missing_types:
            type_names = {
                "definition": "定义",
                "example": "示例", 
                "application": "应用",
                "explanation": "解释",
                "summary": "总结"
            }
            recommendations.append({
                "type": "completeness",
                "priority": "high" if missing_type == "definition" else "medium",
                "message": f"缺少{type_names.get(missing_type, missing_type)}类型的概念",
                "suggestion": f"考虑添加一些{type_names.get(missing_type, missing_type)}来完善知识结构"
            })
        
        # 分析概念依赖关系
        dependency_graph = self._build_concept_dependency_graph(concepts)
        isolated_count = self._count_isolated_concepts(dependency_graph)
        
        if isolated_count > 0:
            recommendations.append({
                "type": "integration",
                "priority": "medium",
                "message": f"有{isolated_count}个孤立概念没有与其他概念建立联系",
                "suggestion": "考虑为这些概念添加与其他概念的引用关系，增强知识的关联性"
            })
        
        # 检查概念链的完整性
        teaching_paths = self._extract_teaching_paths(concepts, max_depth=5)
        if len(teaching_paths) < 2:
            recommendations.append({
                "type": "flow",
                "priority": "medium",
                "message": "概念之间的教学路径较少",
                "suggestion": "考虑增加概念之间的引用关系，形成更完整的学习路径"
            })
        
        # 评估概念类型平衡
        type_counts = self._count_concepts_by_type(concepts)
        total = len(concepts)
        
        for concept_type, count in type_counts.items():
            proportion = count / total
            if proportion > 0.5 and concept_type == "example":
                recommendations.append({
                    "type": "balance",
                    "priority": "low",
                    "message": f"示例概念占比过高 ({proportion:.1%})",
                    "suggestion": "可以适当增加定义或应用类型的概念来平衡知识结构"
                })
        
        return recommendations


def find_related_documents(source_doc_id: int, all_documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """查找相关文档"""
    # 简化实现
    return []


def analyze_citation_network(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """分析引用网络"""
    # 简化实现
    return {}