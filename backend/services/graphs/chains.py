from typing import Any, Dict, List, Optional

from .graph_runtime import GraphRuntime
from .nodes import GraphNodes
from .state import GraphState


class AgentChains:
    def __init__(self, ai_router: Any, rag_engine: Any, memory_hook: Any = None) -> None:
        self.runtime = GraphRuntime(memory_hook=memory_hook)
        self.nodes = GraphNodes(ai_router=ai_router, rag_engine=rag_engine, memory_hook=memory_hook)
        self.memory_hook = memory_hook

    async def run_ingestion_graph(
        self,
        text: str,
        document_type: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> List[Dict[str, str]]:
        initial: GraphState = {
            "doc_text": text,
            "document_type": document_type,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "agent_trace": [],
        }
        result = await self.runtime.run(
            graph_name="ingestion",
            initial_state=initial,
            node_funcs={
                "split_long_text": self.nodes.split_long_text,
                "abstract_chunks": self.nodes.abstract_chunks,
            },
            edges=[("split_long_text", "abstract_chunks"), ("abstract_chunks", "__end__")],
            entry="split_long_text",
        )
        sections = result.get("sections", [])
        return sections if isinstance(sections, list) else []

    async def run_chat_graph(
        self,
        query: str,
        discipline: str,
        mode: str,
        document_id: int | None = None,
        embedding_mode: str = "auto",
        tenant_id: str = "public",
        billing_client_id: str = "",
        billing_exempt: bool = False,
    ) -> Dict[str, Any]:
        initial: GraphState = {
            "query": query,
            "discipline": discipline,
            "mode": mode,
            "embedding_mode": embedding_mode,
            "tenant_id": tenant_id,
            "billing_client_id": billing_client_id,
            "billing_exempt": billing_exempt,
            "top_k": 6,
            "agent_trace": [],
        }
        if isinstance(document_id, int) and document_id > 0:
            initial["document_id"] = document_id
        result = await self.runtime.run(
            graph_name="chat",
            initial_state=initial,
            node_funcs={
                "retrieve_context": self.nodes.retrieve_context,
                "recover_sparse_evidence": self.nodes.recover_sparse_evidence,
                "compress_evidence": self.nodes.compress_evidence,
                "internal_reasoning_step": self.nodes.internal_reasoning_step,
                "generate_chat_contract": self.nodes.generate_chat_contract,
                "check_chat_quality": self.nodes.check_chat_quality,
            },
            edges=[
                ("retrieve_context", "recover_sparse_evidence"),
                ("recover_sparse_evidence", "compress_evidence"),
                ("compress_evidence", "internal_reasoning_step"),
                ("internal_reasoning_step", "generate_chat_contract"),
                ("generate_chat_contract", "check_chat_quality"),
                ("check_chat_quality", "__end__"),
            ],
            entry="retrieve_context",
        )
        return dict(result)

    async def run_summary_graph(
        self,
        query: str,
        discipline: str,
        document_id: int | None = None,
        embedding_mode: str = "auto",
        tenant_id: str = "public",
        billing_client_id: str = "",
        billing_exempt: bool = False,
        summary_debug_passthrough: bool = False,
        summary_compact_level: int = 1,
        summary_mode: str = "fast",
        _progress_callback=None,
    ) -> Dict[str, Any]:
        initial: GraphState = {
            "query": query,
            "discipline": discipline,
            "embedding_mode": embedding_mode,
            "tenant_id": tenant_id,
            "billing_client_id": billing_client_id,
            "billing_exempt": billing_exempt,
            "top_k": 8,
            "max_qa_pairs": 3,
            "summary_debug_passthrough": bool(summary_debug_passthrough),
            "summary_compact_level": int(summary_compact_level),
            "summary_mode": str(summary_mode or "fast"),
            "agent_trace": [],
        }
        if isinstance(document_id, int) and document_id > 0:
            initial["document_id"] = document_id
        if callable(_progress_callback):
            initial["_progress_callback"] = _progress_callback
        result = await self.runtime.run(
            graph_name="summary",
            initial_state=initial,
            node_funcs={
                "retrieve_summary_context": self.nodes.retrieve_summary_context,
                "recover_sparse_evidence": self.nodes.recover_sparse_evidence,
                "map_reduce_summary": self.nodes.map_reduce_summary,
                "generate_summary_contract": self.nodes.generate_summary_contract,
                "check_summary_quality": self.nodes.check_summary_quality,
            },
            edges=[
                ("retrieve_summary_context", "recover_sparse_evidence"),
                ("recover_sparse_evidence", "map_reduce_summary"),
                ("map_reduce_summary", "generate_summary_contract"),
                ("generate_summary_contract", "check_summary_quality"),
                ("check_summary_quality", "__end__"),
            ],
            entry="retrieve_summary_context",
        )
        return dict(result)

    async def run_report_graph(
        self,
        query: str,
        discipline: str,
        document_id: int | None = None,
        embedding_mode: str = "auto",
        tenant_id: str = "public",
        billing_client_id: str = "",
        billing_exempt: bool = False,
        summary_compact_level: int = 1,
        _progress_callback=None,
    ) -> Dict[str, Any]:
        initial: GraphState = {
            "query": query,
            "discipline": discipline,
            "embedding_mode": embedding_mode,
            "tenant_id": tenant_id,
            "billing_client_id": billing_client_id,
            "billing_exempt": billing_exempt,
            "top_k": 16,
            "summary_mode": "full",
            "summary_compact_level": int(summary_compact_level),
            "agent_trace": [],
        }
        if isinstance(document_id, int) and document_id > 0:
            initial["document_id"] = document_id
        if callable(_progress_callback):
            initial["_progress_callback"] = _progress_callback
        result = await self.runtime.run(
            graph_name="report",
            initial_state=initial,
            node_funcs={
                "retrieve_report_context": self.nodes.retrieve_report_context,
                "map_reduce_report": self.nodes.map_reduce_report,
                "generate_report_contract": self.nodes.generate_report_contract,
                "check_report_quality": self.nodes.check_report_quality,
            },
            edges=[
                ("retrieve_report_context", "map_reduce_report"),
                ("map_reduce_report", "generate_report_contract"),
                ("generate_report_contract", "check_report_quality"),
                ("check_report_quality", "__end__"),
            ],
            entry="retrieve_report_context",
        )
        return dict(result)

    async def run_exam_graph(
        self,
        query: str,
        discipline: str,
        tenant_id: str = "public",
        question_type: str = "standard",
        question_context: str = "",
        billing_client_id: str = "",
        billing_exempt: bool = False,
    ) -> Dict[str, Any]:
        initial: GraphState = {
            "query": query,
            "discipline": discipline,
            "tenant_id": tenant_id,
            "billing_client_id": billing_client_id,
            "billing_exempt": billing_exempt,
            "top_k": 4,
            "agent_trace": [],
            "question_type": question_type,
            "question_context": question_context,
        }
        result = await self.runtime.run(
            graph_name="exam",
            initial_state=initial,
            node_funcs={
                "retrieve_context": self.nodes.retrieve_context,
                "recover_sparse_evidence": self.nodes.recover_sparse_evidence,
                "compress_evidence": self.nodes.compress_evidence,
                "internal_reasoning_step": self.nodes.internal_reasoning_step,
                "generate_exam_contract": self.nodes.generate_exam_contract,
                "check_exam_quality": self.nodes.check_exam_quality,
            },
            edges=[
                ("retrieve_context", "recover_sparse_evidence"),
                ("recover_sparse_evidence", "compress_evidence"),
                ("compress_evidence", "internal_reasoning_step"),
                ("internal_reasoning_step", "generate_exam_contract"),
                ("generate_exam_contract", "check_exam_quality"),
                ("check_exam_quality", "__end__"),
            ],
            entry="retrieve_context",
        )
        return dict(result)

    async def run_deep_pipeline_job(
        self,
        pipeline_service: Any,
        job_id: str,
        tenant_id: str = "public",
        user_id: str = "system",
        roles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        四库深度流水线：单节点 LangGraph（与 GraphRuntime 回退链一致），
        实际 DB1~DB4 写入在 DeepPipelineService.run_job 内顺序完成。
        """
        initial: GraphState = {
            "query": "",
            "discipline": "all",
            "agent_trace": [],
        }

        async def node_execute(state: GraphState) -> Dict[str, Any]:
            await pipeline_service.run_job(job_id, tenant_id=tenant_id, user_id=user_id, roles=roles or [])
            trace = [*state.get("agent_trace", []), "deep_pipeline:execute"]
            return {"agent_trace": trace}

        result = await self.runtime.run(
            graph_name="deep_pipeline",
            initial_state=initial,
            node_funcs={"execute": node_execute},
            edges=[("execute", "__end__")],
            entry="execute",
        )
        return dict(result)
