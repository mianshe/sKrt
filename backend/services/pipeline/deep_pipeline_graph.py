"""
四库深度流水线与 LangGraph 的衔接点。

实际编排入口：`AgentChains.run_deep_pipeline_job`（见 `backend/services/graphs/chains.py`），
单节点图 `execute` 调用 `DeepPipelineService.run_job`，内部顺序完成 DB1→DB2→投影→DB3→校验→DB4。
"""

from backend.services.pipeline.deep_pipeline_service import DeepPipelineService, run_deep_pipeline_graph_stub

__all__ = ["DeepPipelineService", "run_deep_pipeline_graph_stub"]
