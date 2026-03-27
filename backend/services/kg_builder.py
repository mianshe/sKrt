from collections import defaultdict
from typing import Any, Dict, List, Tuple


class KGBuilder:
    def build_graph(
        self,
        documents: List[Dict[str, Any]],
        chunks_by_document: Dict[int, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
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

        return {"nodes": nodes, "links": links}

    def extract_cross_relations(self, graph: Dict[str, Any]) -> List[Tuple[str, str, str]]:
        result: List[Tuple[str, str, str]] = []
        for link in graph.get("links", []):
            if link.get("type") == "cross_discipline":
                result.append(
                    (
                        str(link.get("source")),
                        str(link.get("target")),
                        str(link.get("explanation", "")),
                    )
                )
        return result

    def _pairs(self, values: List[int]) -> List[Tuple[int, int]]:
        pairs = []
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                pairs.append((values[i], values[j]))
        return pairs
