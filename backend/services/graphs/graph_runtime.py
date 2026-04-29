from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .state import GraphState

NodeFn = Callable[[GraphState], Awaitable[Dict[str, Any]]]

try:
    from langgraph.graph import END, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    END = "__end__"  # type: ignore
    StateGraph = None  # type: ignore
    LANGGRAPH_AVAILABLE = False


class GraphRuntime:
    def __init__(self, memory_hook: Optional[Any] = None) -> None:
        self.memory_hook = memory_hook

    async def run(
        self,
        graph_name: str,
        initial_state: GraphState,
        node_funcs: Dict[str, NodeFn],
        edges: List[Tuple[str, str]],
        entry: str,
    ) -> GraphState:
        # LangGraph's plain `dict` state does not preserve arbitrary intermediate
        # keys in this project reliably. Report/chat pipelines depend on merged
        # state from earlier nodes such as `chapter_summaries`, so prefer the
        # deterministic in-house fallback runner for now.
        result = await self._run_fallback(graph_name, initial_state, node_funcs, edges, entry)

        # --- memory hook: persist working-memory artifacts after execution ---
        if self.memory_hook is not None:
            try:
                session_id = result.get("session_id", "default")
                tenant_id = result.get("tenant_id", "public")
                await self.memory_hook.after_execution(
                    state=result,
                    session_id=session_id,
                    tenant_id=tenant_id,
                )
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "MemoryHook.after_execution failed for graph=%s", graph_name
                )

        return result

    async def _run_langgraph(
        self,
        initial_state: GraphState,
        node_funcs: Dict[str, NodeFn],
        edges: List[Tuple[str, str]],
        entry: str,
    ) -> GraphState:
        graph = StateGraph(dict)
        for name, fn in node_funcs.items():
            graph.add_node(name, fn)
        graph.set_entry_point(entry)
        for src, dst in edges:
            graph.add_edge(src, END if dst == "__end__" else dst)
        app = graph.compile()
        result = await app.ainvoke(initial_state)
        return dict(result)

    async def _run_fallback(
        self,
        graph_name: str,
        initial_state: GraphState,
        node_funcs: Dict[str, NodeFn],
        edges: List[Tuple[str, str]],
        entry: str,
    ) -> GraphState:
        state: GraphState = dict(initial_state)
        state.setdefault("agent_trace", [])
        next_map: Dict[str, str] = {}
        for src, dst in edges:
            if src not in next_map:
                next_map[src] = dst

        total_nodes = len(node_funcs)
        node_index = 0
        progress_cb = state.get("_progress_callback")

        current = entry
        while current and current != "__end__":
            fn = node_funcs.get(current)
            if fn is None:
                break
            if callable(progress_cb):
                try:
                    progress_cb({"stage": "node", "node": current, "node_index": node_index, "total_nodes": total_nodes})
                except Exception:
                    pass
            patch = await fn(state)
            if isinstance(patch, dict):
                state.update(patch)
            state["agent_trace"] = [*state.get("agent_trace", []), f"{graph_name}:{current}"]
            node_index += 1
            current = next_map.get(current, "__end__")
        return state
