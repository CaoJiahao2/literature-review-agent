"""LangGraph node implementations.

Each node is a small function that takes a `GraphState` dict and returns a
partial dict to merge back into the state. Errors are captured into
`state["errors"]` instead of raising, so one bad source doesn't kill the run.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import Settings
from ..llm import build_chat_model
from ..report.template import SECTIONS, skeleton_body
from ..state import Paper, SearchPlan
from ..tools import merge_and_rank, run_sources

log = logging.getLogger(__name__)


# --- helpers ---------------------------------------------------------------


def _ensure_list(x) -> list:
    if x is None:
        return []
    if isinstance(x, list):
        return list(x)
    return [x]


def _safe_json(text: str) -> dict:
    """Best-effort JSON parse for LLM output; returns {} on failure."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _papers_for_section(section_name: str, papers: list[Paper], max_n: int = 12) -> list[Paper]:
    """Pick the most relevant subset of the corpus for a given section."""
    if not papers:
        return []
    pool = papers[: max(max_n * 3, 30)]
    offset = {"background": 0, "methods": 2, "datasets": 4, "trends": 6, "open_problems": 8}.get(section_name, 0)
    return pool[offset : offset + max_n] or pool[:max_n]


def _enabled_sources(state: dict, settings: Settings) -> list[str]:
    srcs = state.get("sources") or settings.enabled_sources()
    return [s for s in srcs if s]


# --- nodes ------------------------------------------------------------------


def plan_node(state: dict, settings: Settings) -> dict:
    """Decide what to search for, given the raw topic."""
    topic = state["topic"]
    queries = _plan_queries_deterministic(topic)
    summary = topic
    rationale = ""

    model = build_chat_model(settings, temperature=0.2)
    if model is not None:
        try:
            resp = model.invoke(
                [
                    SystemMessage(
                        content=(
                            "You are a research assistant. Given a topic, propose 4-6 focused "
                            "search queries that would find relevant AI literature on arXiv, "
                            "OpenAlex, HuggingFace Daily Papers, Semantic Scholar, and Crossref. "
                            'Respond with JSON: {"topic_summary": "...", "queries": ["...", "..."], "rationale": "..."}. '
                            "Queries should be short (2-6 words), specific, and combine the topic "
                            "with likely subfields, methods, or applications."
                        )
                    ),
                    HumanMessage(content=f"Topic: {topic}"),
                ]
            )
            data = _safe_json(resp.content if isinstance(resp.content, str) else str(resp.content))
            if data.get("queries"):
                queries = [str(q).strip() for q in data["queries"] if str(q).strip()][:6]
                summary = data.get("topic_summary") or summary
                rationale = data.get("rationale") or ""
        except Exception as exc:  # pragma: no cover
            log.warning("plan LLM call failed: %s", exc)

    return {
        "plan": SearchPlan(topic_summary=summary, queries=queries, rationale=rationale),
        "iteration": state.get("iteration", 0) + 1,
        "errors": [],
    }


def _plan_queries_deterministic(topic: str) -> list[str]:
    t = topic.strip()
    return [t, f"{t} survey", f"{t} review", f"{t} benchmark", f"{t} methods"][:5]


def search_sources_node(state: dict, settings: Settings) -> dict:
    """Run every enabled source against the planned queries."""
    plan: SearchPlan = state["plan"]
    years = state.get("years")
    sources = _enabled_sources(state, settings)

    papers, errors = run_sources(settings, plan.queries, sources=sources, years=years)

    # Split results back into per-source buckets for visibility / debugging.
    per_source: dict[str, list[Paper]] = {s: [] for s in sources}
    for p in papers:
        per_source.setdefault(p.source, []).append(p)

    new_errors = list(state.get("errors") or []) + [f"{e}" for e in errors]
    return {
        "source_results": per_source,
        "arxiv_results": per_source.get("arxiv", []),  # back-compat
        "openalex_results": per_source.get("openalex", []),  # back-compat
        "errors": new_errors,
    }


def dedupe_rank_node(state: dict, settings: Settings) -> dict:
    papers: list[Paper] = []
    for v in (state.get("source_results") or {}).values():
        papers.extend(v)
    # Fall back to v1 keys if source_results is empty (e.g. legacy state).
    if not papers:
        papers = _ensure_list(state.get("arxiv_results")) + _ensure_list(state.get("openalex_results"))
    merged = merge_and_rank(papers, top_k=None)
    err = state.pop("_err", None)
    new_errors = list(state.get("errors") or [])
    if err:
        new_errors.append(err)
    return {"merged": merged, "errors": new_errors}


def filter_top_k_node(state: dict, settings: Settings) -> dict:
    top_k = int(state.get("top_k", 30))
    merged = state.get("merged", [])
    return {"merged": merged[:top_k]}


def should_refine(state: dict) -> str:
    max_iter = int(state.get("max_iter", 2))
    iteration = int(state.get("iteration", 0))
    merged = state.get("merged", []) or []
    if iteration < max_iter and len(merged) < max(5, int(state.get("top_k", 30)) // 2):
        return "refine"
    return "synthesize"


def refine_search_node(state: dict, settings: Settings) -> dict:
    """Generate one more round of queries and search again, then re-dedupe."""
    plan: SearchPlan = state["plan"]
    model = build_chat_model(settings, temperature=0.4)
    sources = _enabled_sources(state, settings)

    new_queries: list[str] = []
    if model is not None:
        try:
            resp = model.invoke(
                [
                    SystemMessage(
                        content=(
                            "You are extending a literature search. The previous round returned "
                            "few results. Propose 2-3 new, broader or differently-angled queries. "
                            'Respond with JSON: {"queries": ["...", "..."]}'
                        )
                    ),
                    HumanMessage(
                        content=(
                            f"Topic: {state['topic']}\n"
                            f"Previous queries: {plan.queries}\n"
                            f"Found {len(state.get('merged', []))} papers so far."
                        )
                    ),
                ]
            )
            data = _safe_json(resp.content if isinstance(resp.content, str) else str(resp.content))
            if data.get("queries"):
                new_queries = [str(q).strip() for q in data["queries"] if str(q).strip()][:3]
        except Exception as exc:  # pragma: no cover
            log.warning("refine LLM call failed: %s", exc)

    if not new_queries:
        new_queries = [
            f"{state['topic']} advances",
            f"{state['topic']} applications",
            f"{state['topic']} evaluation",
        ]

    all_queries = list(plan.queries) + new_queries
    new_plan = SearchPlan(
        topic_summary=plan.topic_summary,
        queries=all_queries,
        rationale=plan.rationale,
    )

    years = state.get("years")
    new_papers, new_errors = run_sources(settings, new_queries, sources=sources, years=years)

    per_source: dict[str, list[Paper]] = dict(state.get("source_results") or {})
    for p in new_papers:
        per_source.setdefault(p.source, []).append(p)

    combined: list[Paper] = []
    for v in per_source.values():
        combined.extend(v)

    merged = merge_and_rank(combined, top_k=None)
    return {
        "plan": new_plan,
        "source_results": per_source,
        "arxiv_results": per_source.get("arxiv", []),
        "openalex_results": per_source.get("openalex", []),
        "merged": merged,
        "iteration": int(state.get("iteration", 0)) + 1,
        "errors": list(state.get("errors") or []) + [f"{e}" for e in new_errors],
    }


def synthesize_sections_node(state: dict, settings: Settings) -> dict:
    merged: list[Paper] = state.get("merged", []) or []
    sections: dict[str, str] = {}
    model = build_chat_model(settings, temperature=0.2)
    language = state.get("language", "en")
    forced_skeleton = bool(state.get("no_llm")) or model is None

    for spec in SECTIONS:
        subset = _papers_for_section(spec.name, merged)
        if forced_skeleton:
            sections[spec.name] = skeleton_body(spec, subset)
            continue
        try:
            sections[spec.name] = _synthesize_one(model, spec, subset, state["topic"], language)
        except Exception as exc:  # pragma: no cover
            log.warning("synthesize %s failed: %s — using skeleton", spec.name, exc)
            sections[spec.name] = skeleton_body(spec, subset)

    return {"sections": sections}


def _synthesize_one(model, spec, papers: list[Paper], topic: str, language: str) -> str:
    lang_name = "Chinese (Simplified)" if language.lower().startswith("zh") else "English"
    if not papers:
        body_prompt = (
            f"The literature search returned no papers. Write a brief 1-2 paragraph "
            f"{spec.title} section explaining what would normally appear here, given "
            f"the topic '{topic}'. Be honest about the gap."
        )
        context = "(no papers found)"
    else:
        bullets = []
        for p in papers:
            cite = f"{p.title} ({p.year or 'n.d.'})" + (f" — {', '.join(p.authors[:3])}" if p.authors else "")
            abs_excerpt = (p.abstract or "").replace("\n", " ")[:400]
            bullets.append(f"- {cite}: {abs_excerpt}")
        context = "\n".join(bullets)
        body_prompt = (
            f"Using only the papers listed below (do not invent citations), write the "
            f"'{spec.title}' section of a literature review on '{topic}'. "
            f"Write in {lang_name}. Aim for 2-4 short paragraphs. "
            f"Cite papers inline with [#] where # is the number in the list below. "
            f"Do not invent facts not present in the abstracts.\n\nPapers:\n{context}"
        )

    resp = model.invoke(
        [
            SystemMessage(
                content=(
                    f"You are writing the '{spec.title}' section of an academic literature review. "
                    f"Use neutral, precise prose in {lang_name}. Ground every claim in the provided abstracts."
                )
            ),
            HumanMessage(content=body_prompt),
        ]
    )
    return (resp.content if isinstance(resp.content, str) else str(resp.content)).strip()


def assemble_node(state: dict, settings: Settings) -> dict:
    """Propagate the initial input keys + a final-report marker."""
    return {
        "topic": state.get("topic", ""),
        "language": state.get("language", "en"),
        "output_path": state.get("output_path", "report.md"),
        "sources": state.get("sources", []),
        "final_report": "ok",
    }
