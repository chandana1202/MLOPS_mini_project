"""LangGraph wiring of the same nodes as core.run_sync (A1/A2).
extract -> (reason -> verify -> retry?)* -> finalize"""
from typing import TypedDict

from langgraph.graph import END, StateGraph

from agents import core


class G(TypedDict):
    run: core.RunState


def build_graph(llm):
    def node(fn, uses_llm=True):
        def _n(st):
            fn(st["run"], llm) if uses_llm else fn(st["run"])
            return {"run": st["run"]}
        return _n

    g = StateGraph(G)
    g.add_node("extract", node(core.extract))
    g.add_node("reason", node(core.reason))
    g.add_node("verify", node(core.verify))
    g.add_node("retry", node(core.retry, False))
    g.add_node("finalize", node(core.finalize, False))
    g.set_entry_point("extract")
    g.add_conditional_edges("extract", lambda st: core.next_after_extract(st["run"]),
                            {"reason": "reason", "finalize": "finalize"})
    g.add_edge("reason", "verify")
    g.add_conditional_edges("verify", lambda st: core.next_after_verify(st["run"]),
                            {"retry": "retry", "finalize": "finalize"})
    g.add_edge("retry", "reason")
    g.add_edge("finalize", END)
    return g.compile()


def run_graph(request, llm, store, limits=None):
    s = core.new_state(request, store, limits)
    build_graph(llm).invoke({"run": s}, {"recursion_limit": 60})
    return s.result
