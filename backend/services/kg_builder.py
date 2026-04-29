from collections import defaultdict
from typing import Any, Dict, List, Tuple, Optional
import logging

from .cross_doc_ref import (
    CrossDocumentReferenceParser,
    DocumentReference,
    DocumentTerm,
    CrossDocumentRelation,
    find_related_documents,
    analyze_citation_network
)

logger = logging.getLogger(__name__)


class KGBuilder:
    def __init__(self):
        self.ref_parser = CrossDocumentReferenceParser()
        self.cross_relations: List[CrossDocumentRelation] = []
        
    def build_graph(
        self,
        documents: List[Dict[str, Any]],
        chunks_by_document: Dict[int, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """构建增强的知识图谱，包含跨文档引用"""
        
        # 1. 注册所有文档到引用解析器
        self._register_documents(documents)
        
        # 2. 解析所有chunk，提取引用和术语
        all_references: List[DocumentReference] = []
        all_terms: List[DocumentTerm] = []
        
        for doc_id, chunks in chunks_by_document.items():
            for chunk in chunks:
                chunk_id = chunk.get("chunk_id", "")
                content = chunk.get("content", "")
                if content:
                    refs, terms = self.ref_parser.parse_chunk(doc_id, chunk_id, content)
                    all_references.extend(refs)
                    all_terms.extend(terms)
        
        # 3. 构建跨文档关系
        self.cross_relations = self.ref_parser.build_cross_document_relations()
        
        # 4. 构建图谱节点和边
        nodes: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        node_ids = set()
        link_ids = set()

        def add_node(node_id: str, label: str, node_type: str, group: str) -> None:
            if node_id in node_ids:
                return
            node_ids.add(node_id)
            nodes.append({"id": node_id, "label": label, "type": node_type, "group": group})

        def add_link(source: str, target: str, rel_type: str, explanation: str) -> None:
            key = (source, target, rel_type)
            if key in link_ids:
                return
            link_ids.add(key)
            links.append(
                {
                    "source": source,
                    "target": target,
                    "type": rel_type,
                    "explanation": explanation,
                }
            )

        discipline_docs: Dict[str, List[int]] = defaultdict(list)
        kp_to_docs: Dict[str, List[int]] = defaultdict(list)

        for doc in documents:
            doc_id = int(doc["id"])
            title = doc.get("title", f"Document {doc_id}")
            discipline = doc.get("discipline", "general")
            doc_type = doc.get("document_type", "academic")
            add_node(f"sub:{discipline}", discipline, "discipline", discipline)
            add_node(f"doc:{doc_id}", title, "document", discipline)
            add_link(
                f"doc:{doc_id}",
                f"sub:{discipline}",
                "belongs_to",
                f"文档《{title}》归属学科 {discipline}。",
            )

            discipline_docs[discipline].append(doc_id)
            for kp in doc.get("knowledge_points", [])[:25]:
                kp_id = f"kp:{kp}"
                add_node(kp_id, kp, "knowledge", discipline)
                add_link(f"doc:{doc_id}", kp_id, "related", f"文档涉及知识点 {kp}。")
                kp_to_docs[kp].append(doc_id)

            if doc_type == "exam":
                exam_id = f"exam:{doc_id}"
                add_node(exam_id, f"考题集-{title}", "exam", discipline)
                add_link(exam_id, f"doc:{doc_id}", "cites", "考题来源于该文档。")
            elif doc_type == "technical":
                api_id = f"api:{doc_id}"
                add_node(api_id, f"API-{title}", "api", discipline)
                add_link(api_id, f"doc:{doc_id}", "depends_on", "技术文档对应API实体。")
            elif doc_type == "project":
                task_id = f"task:{doc_id}"
                add_node(task_id, f"Task-{title}", "task", discipline)
                add_link(task_id, f"doc:{doc_id}", "depends_on", "项目任务依赖该文档内容。")

        for kp, doc_ids in kp_to_docs.items():
            if len(doc_ids) < 2:
                continue
            pairs = self._pairs(doc_ids[:12])
            for a, b in pairs:
                if a == b:
                    continue
                add_link(
                    f"doc:{a}",
                    f"doc:{b}",
                    "cross_discipline",
                    f"文档通过共享知识点 {kp} 形成跨学科连接。",
                )

        # 5. 添加跨文档引用关系边
        self._add_cross_document_links(nodes, links, add_link)
        
        return {"nodes": nodes, "links": links}

    def _register_documents(self, documents: List[Dict[str, Any]]) -> None:
        """注册文档到引用解析器"""
        for doc in documents:
            doc_id = int(doc["id"])
            metadata = {
                "title": doc.get("title", f"Document {doc_id}"),
                "discipline": doc.get("discipline", "general"),
                "document_type": doc.get("document_type", "academic"),
                "knowledge_points": doc.get("knowledge_points", []),
            }
            self.ref_parser.register_document(doc_id, metadata)
    
    def _add_cross_document_links(self, nodes: List[Dict[str, Any]], links: List[Dict[str, Any]], 
                                  add_link_func) -> None:
        """添加跨文档引用关系边到图谱"""
        for relation in self.cross_relations:
            source_node = f"doc:{relation.source_doc_id}"
            target_node = f"doc:{relation.target_doc_id}"
            
            # 确保节点存在
            source_exists = any(n["id"] == source_node for n in nodes)
            target_exists = any(n["id"] == target_node for n in nodes)
            
            if source_exists and target_exists:
                explanation = self._build_relation_explanation(relation)
                add_link_func(
                    source_node,
                    target_node,
                    relation.relation_type,
                    explanation
                )
                
                # 添加反向关系（对于某些类型）
                if relation.relation_type in ["cites", "references", "extends"]:
                    add_link_func(
                        target_node,
                        source_node,
                        f"cited_by",
                        f"被{relation.source_doc_id}引用"
                    )
    
    def _build_relation_explanation(self, relation: CrossDocumentRelation) -> str:
        """构建关系解释文本"""
        evidence_text = "；".join(relation.evidence[:2])
        
        if relation.terms_in_common:
            terms_text = "、".join(relation.terms_in_common[:3])
            return f"{evidence_text}（共享术语：{terms_text}）"
        
        return evidence_text or f"文档间{relation.relation_type}关系"

    def extract_cross_relations(self, graph: Dict[str, Any]) -> List[Tuple[str, str, str]]:
        """提取跨文档关系（向后兼容）"""
        result: List[Tuple[str, str, str]] = []
        
        # 1. 从图谱中提取
        for link in graph.get("links", []):
            if link.get("type") == "cross_discipline":
                result.append(
                    (
                        str(link.get("source")),
                        str(link.get("target")),
                        str(link.get("explanation", "")),
                    )
                )
        
        # 2. 从解析的关系中提取（增强版）
        for relation in self.cross_relations:
            result.append(
                (
                    f"doc:{relation.source_doc_id}",
                    f"doc:{relation.target_doc_id}",
                    self._build_relation_explanation(relation),
                )
            )
        
        return result

    def get_cross_document_relations(self) -> List[CrossDocumentRelation]:
        """获取跨文档关系（新API）"""
        return self.cross_relations
    
    def find_related_documents(self, doc_id: int, min_strength: float = 0.5) -> List[Dict[str, Any]]:
        """查找相关文档"""
        return find_related_documents(doc_id, self.cross_relations, min_strength)
    
    def analyze_citation_network(self) -> Dict[str, Any]:
        """分析引用网络"""
        return analyze_citation_network(self.cross_relations)
    
    def get_document_citation_stats(self, doc_id: int) -> Dict[str, Any]:
        """获取文档引用统计"""
        outgoing = 0
        incoming = 0
        
        for rel in self.cross_relations:
            if rel.relation_type in ["cites", "references"]:
                if rel.source_doc_id == doc_id:
                    outgoing += 1
                if rel.target_doc_id == doc_id:
                    incoming += 1
        
        return {
            "doc_id": doc_id,
            "outgoing_citations": outgoing,
            "incoming_citations": incoming,
            "total_relations": outgoing + incoming,
        }

    def _pairs(self, values: List[int]) -> List[Tuple[int, int]]:
        pairs = []
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                pairs.append((values[i], values[j]))
        return pairs
