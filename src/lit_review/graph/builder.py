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


def build_graph(settings: Settings):
    """Compile the literature-review graph.

    Flow:
        START -> plan -> search_sources -> dedupe_rank -> filter_top_k
            -> should_refine -> (refine | synthesize)
        refine -> search_sources (loop, max 2)
        synthesize -> assemble -> END
    """
    g = StateGraph(GraphState)

    g.add_node("plan", lambda s: plan_node(s, settings))
    g.add_node("search_sources", lambda s: search_sources_node(s, settings))
    g.add_node("dedupe_rank", lambda s: dedupe_rank_node(s, settings))
    g.add_node("filter_top_k", lambda s: filter_top_k_node(s, settings))
    g.add_node("refine_search", lambda s: refine_search_node(s, settings))
    g.add_node("synthesize_sections", lambda s: synthesize_sections_node(s, settings))
    g.add_node("assemble", lambda s: assemble_node(s, settings))

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
