# Source excerpt; private application dependencies are intentionally omitted.
from langgraph.graph import END, START, StateGraph
# ChatGraphState and node handlers belong to the private application.
def _timed_node(name, handler):
    async def run(state):
        start = time.perf_counter()
        output = await handler(state)
        timings = dict(state.get("timings_ms") or {})
        timings[name] = round((time.perf_counter() - start) * 1000, 2)
        output["timings_ms"] = timings
        if name == "prepare_session":
            output["t0"] = start
        return output
    return run

def _build_graph():
    graph = StateGraph(ChatGraphState)
    graph.add_node("quick_help", _timed_node("quick_help", _quick_help_node))
    graph.add_node("prepare_session", _timed_node("prepare_session", _prepare_session_node))
    graph.add_node("route", _timed_node("route", _route_node))
    graph.add_node("admin_guard", _timed_node("admin_guard", _admin_guard_node))
    graph.add_node("retrieve", _timed_node("retrieve", _retrieve_node))
    graph.add_node("course_recommend", _timed_node("course_recommend", _course_recommend_node))
    graph.add_node("generate", _timed_node("generate", _generate_node))
    graph.add_node("persist_result", _timed_node("persist_result", _persist_result_node))
    graph.add_node("kb_ops", _timed_node("kb_ops", _kb_ops_node))
    graph.add_node("agent_tools", _timed_node("agent_tools", _agent_tools_node))
    graph.add_node("finalize", _finalize_node)
    graph.add_edge(START, "prepare_session")
    graph.add_edge("prepare_session", "route")
    graph.add_conditional_edges(
        "route",
        _route_after_classify,
        {
            "admin_guard": "admin_guard",
            "retrieve": "retrieve",
            "course_recommend": "course_recommend",
            "kb_ops": "kb_ops",
            "agent_tools": "agent_tools",
            "quick_help": "quick_help",
        },
    )
    graph.add_edge("admin_guard", "persist_result")
    graph.add_edge("quick_help", "persist_result")
    graph.add_edge("course_recommend", "persist_result")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "persist_result")
    graph.add_edge("persist_result", "finalize")
    graph.add_conditional_edges(
        "kb_ops",
        _route_after_kb_ops,
        {
            "finalize": "finalize",
            "agent_tools": "agent_tools",
        },
    )
    graph.add_edge("agent_tools", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
