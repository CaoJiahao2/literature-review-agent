"""LangGraph node implementations.

Each node is a small function that takes a `GraphState` dict and returns a
partial dict to merge back into the state. Errors are captured into
`state["errors"]` instead of raising, so one bad source doesn't kill the run.

v0.2 changes
============
* Sources run via :func:`tools.run_sources_async` (concurrent), with the
  historical sync :func:`tools.run_sources` used as a graceful fallback when
  the asyncio loop cannot be started (e.g. inside Gradio's synchronous
  handler).
* LLM calls go through :class:`LLMClient` (caching + retry + token accounting)
  when an LLM key is configured.
* All nodes are wrapped with :func:`metrics.timed_node` so per-node wall-clock
  is captured into ``state['__node_times__']``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import Settings
from ..llm_client import LLMClient, _safe_json
from ..metrics import timed_node
from ..report.template import SECTIONS, skeleton_body
from ..state import Paper, SearchPlan
from ..tools import (
    ASYNC_SOURCE_FNS,
    close_async_client,
    merge_and_rank,
    run_sources,
    run_sources_async,
)

log = logging.getLogger(__name__)


# --- helpers ---------------------------------------------------------------


def _ensure_list(x) -> list:
    if x is None:
        return []
    if isinstance(x, list):
        return list(x)
    return [x]


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


def _client(state: dict, settings: Settings) -> LLMClient:
    """Return the per-run LLM client, lazily constructing it if missing."""
    cli = state.get("__llm_client__")
    if isinstance(cli, LLMClient):
        return cli
    cli = LLMClient(settings)
    state["__llm_client__"] = cli
    return cli


# --- nodes ------------------------------------------------------------------


def plan_node(state: dict, settings: Settings) -> dict:
    """Decide what to search for, given the raw topic."""
    with timed_node(state, "plan"):
        topic = state["topic"]
        queries = _plan_queries_deterministic(topic)
        summary = topic
        rationale = ""

        client = _client(state, settings)
        if client is not None and settings.has_llm():
            data = client.invoke_json(
                system=(
                    "You are a research assistant. Given a topic, propose 4-6 focused "
                    "search queries that would find relevant AI literature on arXiv, "
                    "OpenAlex, HuggingFace Daily Papers, Semantic Scholar, and Crossref. "
                    'Respond with JSON: {"topic_summary": "...", "queries": ["...", "..."], "rationale": "..."}. '
                    "Queries should be short (2-6 words), specific, and combine the topic "
                    "with likely subfields, methods, or applications."
                ),
                user=f"Topic: {topic}",
                tag="plan",
                temperature=0.2,
                fallback={"queries": queries},
            )
            if isinstance(data, dict) and data.get("queries"):
                queries = [str(q).strip() for q in data["queries"] if str(q).strip()][:6]
                summary = data.get("topic_summary") or summary
                rationale = data.get("rationale") or ""

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
    with timed_node(state, "search_sources"):
        plan: SearchPlan = state["plan"]
        years = state.get("years")
        sources = _enabled_sources(state, settings)
        queries = list(plan.queries)

        papers, errors = _run_sources(sources, settings, queries, years)
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


def _run_sources(sources, settings, queries, years):
    """Run sources via async fan-out when possible, sync otherwise.

    The async path is preferred: it's faster and survives partial source
    failures more gracefully. We always check for a running event loop
    first because some hosts (Gradio, Jupyter) already have one bound to
    the main thread.
    """
    if not sources or not queries:
        return [], []
    try:
        asyncio.get_running_loop()
        # A loop is running — fall back to the sync path. This branch is hit
        # from inside Gradio's synchronous handler; the UI can opt in to
        # async via ``runner.run_async`` instead.
        return run_sources(settings, queries, sources=sources, years=years)
    except RuntimeError:
        # No loop: spin one up.
        pass
    try:
        return asyncio.run(
            run_sources_async(
                settings,
                queries,
                sources=sources,
                years=years,
            )
        )
    except Exception as exc:
        log.warning("async runner failed (%s); falling back to sync", exc)
        return run_sources(settings, queries, sources=sources, years=years)


def dedupe_rank_node(state: dict, settings: Settings) -> dict:
    with timed_node(state, "dedupe_rank"):
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
        return {
            "merged": merged,
            "errors": new_errors,
            "__dedupe_stats__": {
                "clusters_before": len(papers),
                "papers_after_dedupe": len(merged),
            },
        }


def filter_top_k_node(state: dict, settings: Settings) -> dict:
    with timed_node(state, "filter_top_k"):
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
    with timed_node(state, "refine_search"):
        plan: SearchPlan = state["plan"]
        sources = _enabled_sources(state, settings)

        new_queries: list[str] = []
        client = _client(state, settings)
        if settings.has_llm() and client is not None:
            data = client.invoke_json(
                system=(
                    "You are extending a literature search. The previous round returned "
                    "few results. Propose 2-3 new, broader or differently-angled queries. "
                    'Respond with JSON: {"queries": ["...", "..."]}'
                ),
                user=(
                    f"Topic: {state['topic']}\n"
                    f"Previous queries: {plan.queries}\n"
                    f"Found {len(state.get('merged', []))} papers so far."
                ),
                tag="refine.search",
                temperature=0.4,
                fallback={"queries": []},
            )
            if isinstance(data, dict) and data.get("queries"):
                new_queries = [str(q).strip() for q in data["queries"] if str(q).strip()][:3]

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
        new_papers, new_errors = _run_sources(sources, settings, new_queries, years)

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
            "__dedupe_stats__": {
                "clusters_before": len(combined),
                "papers_after_dedupe": len(merged),
            },
        }


async def _synthesize_one_async(
    client: LLMClient,
    spec,
    papers: list[Paper],
    topic: str,
    language: str,
) -> tuple[str, str, int]:
    """Return (section_name, body, body_chars). Runs one LLM call."""
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
            cite = f"{p.title} ({p.year or 'n.d.'})" + (
                f" — {', '.join(p.authors[:3])}" if p.authors else ""
            )
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

    text = await asyncio.to_thread(
        client.invoke_text,
        system=(
            f"You are writing the '{spec.title}' section of an academic literature review. "
            f"Use neutral, precise prose in {lang_name}. Ground every claim in the provided abstracts."
        ),
        user=body_prompt,
        tag=f"synthesize.{spec.name}",
        temperature=0.2,
    )
    body = (text or "").strip() or skeleton_body(spec, papers)
    return spec.name, body, len(body)


async def _synthesize_all_async(
    merged: list[Paper],
    client: LLMClient,
    topic: str,
    language: str,
    forced_skeleton: bool,
) -> dict[str, str]:
    sections: dict[str, str] = {}
    if forced_skeleton or not _has_llm_signal(client):
        # No LLM -> run skeletons in parallel via the same gather so the UI sees uniform timing.
        async def _skel(spec):
            return spec.name, skeleton_body(spec, _papers_for_section(spec.name, merged))
        results = await asyncio.gather(*(_skel(s) for s in SECTIONS))
        for name, body in results:
            sections[name] = body
        return sections

    async def _one(spec):
        subset = _papers_for_section(spec.name, merged)
        return await _synthesize_one_async(client, spec, subset, topic, language)

    results = await asyncio.gather(*(_one(s) for s in SECTIONS), return_exceptions=True)
    for spec, result in zip(SECTIONS, results):
        if isinstance(result, Exception):
            log.warning("synthesize %s failed: %s — falling back to skeleton", spec.name, result)
            sections[spec.name] = skeleton_body(spec, _papers_for_section(spec.name, merged))
        else:
            name, body, _chars = result
            sections[name] = body
    return sections


def _has_llm_signal(client: LLMClient) -> bool:
    """Whether the client has an LLM endpoint to talk to.

    We treat the presence of an API key as the signal; the underlying model
    build can still fail at request time and fall back per-section.
    """
    return bool(client.settings.has_llm())


def _run_synth_in_own_loop(merged, client, topic, language, forced_skeleton) -> dict[str, str]:
    """Run the parallel section synthesis on a fresh, private event loop.

    ``asyncio.run()`` raises ``RuntimeError`` if a loop is already running in
    the current thread (Gradio/Jupyter hosts, LangGraph async streaming), so we
    always execute the coroutine on a dedicated worker thread that owns its own
    loop. Returns the ``sections`` mapping.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _target():
        return asyncio.run(
            _synthesize_all_async(merged, client, topic, language, forced_skeleton)
        )

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="lit-review-synth") as pool:
        return pool.submit(_target).result()


def synthesize_sections_node(state: dict, settings: Settings) -> dict:
    """Compose the 5 sections in parallel when an LLM is available."""
    with timed_node(state, "synthesize_sections"):
        merged: list[Paper] = state.get("merged", []) or []
        language = state.get("language", "en")
        no_llm = bool(state.get("no_llm"))
        client = _client(state, settings)
        forced_skeleton = no_llm or not _has_llm_signal(client)

        sections = _run_synth_in_own_loop(
            merged, client, state["topic"], language, forced_skeleton
        )
        return {"sections": sections}


def assemble_node(state: dict, settings: Settings) -> dict:
    """Propagate the initial input keys + a final-report marker."""
    with timed_node(state, "assemble"):
        return {
            "topic": state.get("topic", ""),
            "language": state.get("language", "en"),
            "output_path": state.get("output_path", "report.md"),
            "sources": state.get("sources", []),
            "final_report": "ok",
        }
