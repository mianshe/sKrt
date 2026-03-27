from typing import Any, Awaitable, Callable, Dict, List, Tuple

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
    async def run(
        self,
        graph_name: str,
        initial_state: GraphState,
        node_funcs: Dict[str, NodeFn],
        edges: List[Tuple[str, str]],
        entry: str,
    ) -> GraphState:
        if LANGGRAPH_AVAILABLE:
            try:
                return await self._run_langgraph(initial_state, node_funcs, edges, entry)
            except Exception:
                pass
        return await self._run_fallback(graph_name, initial_state, node_funcs, edges, entry)

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

        current = entry
        while current and current != "__end__":
            fn = node_funcs.get(current)
            if fn is None:
                break
            patch = await fn(state)
            if isinstance(patch, dict):
                state.update(patch)
            state["agent_trace"] = [*state.get("agent_trace", []), f"{graph_name}:{current}"]
            current = next_map.get(current, "__end__")
        return state
