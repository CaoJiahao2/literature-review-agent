"""Build the compiled LangGraph."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..config import Settings
from ..state import GraphState
from .nodes import (
    assemble_node,
    dedupe_rank_node,
    filter_top_k_node,
    plan_node,
    refine_search_node,
    search_sources_node,
    synthesize_sections_node,
)
from .edges import should_refine


def _wrap_node(fn, settings: Settings):
    """Bind ``settings`` and forward the internal ``__node_times__`` channel.

    ``timed_node`` records wall-clock by mutating the node's input state in
    place, but LangGraph only merges the *returned* partial dict. We copy the
    (mutated) timing bucket into the return value so per-node timings survive
    and accumulate across the whole run.
    """

    def wrapped(s: dict) -> dict:
        out = fn(s, settings)
        times = dict(s.get("__node_times__") or {})
        times.update(out.get("__node_times__") or {})
        out["__node_times__"] = times
        return out

    return wrapped


def build_graph(settings: Settings):
    """Compile the literature-review graph.

    Flow:
        START -> plan -> search_sources -> dedupe_rank -> filter_top_k
            -> should_refine -> (refine | synthesize)
        refine -> search_sources (loop, max 2)
        synthesize -> assemble -> END
    """
    g = StateGraph(GraphState)

    g.add_node("plan", _wrap_node(plan_node, settings))
    g.add_node("search_sources", _wrap_node(search_sources_node, settings))
    g.add_node("dedupe_rank", _wrap_node(dedupe_rank_node, settings))
    g.add_node("filter_top_k", _wrap_node(filter_top_k_node, settings))
    g.add_node("refine_search", _wrap_node(refine_search_node, settings))
    g.add_node("synthesize_sections", _wrap_node(synthesize_sections_node, settings))
    g.add_node("assemble", _wrap_node(assemble_node, settings))

    g.add_edge(START, "plan")
    g.add_edge("plan", "search_sources")
    g.add_edge("search_sources", "dedupe_rank")
    g.add_edge("dedupe_rank", "filter_top_k")

    g.add_conditional_edges(
        "filter_top_k",
        should_refine,
        {"refine": "refine_search", "synthesize": "synthesize_sections"},
    )

    # Refine loop goes back through search_sources to query the same set again.
    g.add_edge("refine_search", "search_sources")

    g.add_edge("synthesize_sections", "assemble")
    g.add_edge("assemble", END)

    return g.compile()
