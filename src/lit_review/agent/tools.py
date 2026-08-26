"""LangChain tools exposed to the ReAct agent.

The tool list is built by :func:`build_tools` against a shared
:class:`AgentRuntime`. Source search tools delegate to the existing
``lit_review.tools`` clients, while ``review_search_coverage`` and
``review_report_draft`` implement the two explicit self-reflection steps and
``submit_report`` materializes the final Markdown from the *actual* collected
papers (not LLM-invented citations).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from langchain_core.tools import StructuredTool

from ..config import Settings
from ..llm_client import LLMClient
from ..report.template import SECTIONS
from ..report.writer import write_report
from ..state import AgentState, Paper
from ..tools import (
    search_arxiv,
    search_crossref,
    search_huggingface,
    search_openalex,
    search_semantic_scholar,
)
from ..tools.rank import merge_and_rank

log = logging.getLogger(__name__)

SECTION_NAMES = [spec.name for spec in SECTIONS]


@dataclass
class AgentRuntime:
    """Shared mutable context passed to every tool function."""

    settings: Settings
    state: AgentState
    client: LLMClient
    output_path: Optional[Path] = None

    # -- paper memory ---------------------------------------------------

    def record_papers(self, papers: list[Paper], source: str) -> int:
        """Append unseen papers to the working memory and return added count."""
        existing = {p.short_id() for p in self.state.get("papers", []) if p.short_id()}
        source_counts = self.state.setdefault("source_counts", {})
        added = 0
        for p in papers:
            key = p.short_id()
            if not key or key in existing:
                continue
            if not p.source:
                p.source = source
            self.state.setdefault("papers", []).append(p)
            existing.add(key)
            source_counts[source] = int(source_counts.get(source, 0)) + 1
            added += 1
        return added

    def current_papers(self) -> list[Paper]:
        return list(self.state.get("papers", []) or [])

    def reflection_count(self, kind: str) -> int:
        return sum(
            1
            for r in self.state.get("reflections", [])
            if isinstance(r, dict) and r.get("type") == kind
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _year_tuple(years: Optional[list[int]]) -> Optional[tuple[int, int]]:
    if not years:
        return None
    if len(years) != 2:
        raise ValueError("years must be [start_year, end_year]")
    low, high = int(years[0]), int(years[1])
    if low > high:
        raise ValueError("years start must be <= end")
    return (low, high)


def _paper_summary(p: Paper) -> dict[str, Any]:
    return {
        "title": p.title,
        "year": p.year,
        "authors": p.authors[:3],
        "doi": p.doi,
        "arxiv_id": p.arxiv_id,
        "citation_count": p.citation_count,
        "venue": p.venue,
        "abstract_excerpt": (p.abstract or "")[:500],
    }


def _papers_json(source: str, query: str, papers: list[Paper], added: int) -> str:
    return json.dumps(
        {
            "source": source,
            "query": query,
            "count": len(papers),
            "added_to_memory": added,
            "papers": [_paper_summary(p) for p in papers],
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Tool factories
# ---------------------------------------------------------------------------


def _make_search_tool(runtime: AgentRuntime, name: str, source: str, fn) -> StructuredTool:
    cap_attr = f"{source}_max_per_query"

    def _search(
        query: str,
        max_results: int = 0,
        years: Optional[list[int]] = None,
    ) -> str:
        """Search one academic source for a single query string.

        Args:
            query: Short, specific search query (2-6 words recommended).
            max_results: Maximum papers to return. 0 uses the source default.
            years: Optional inclusive [start_year, end_year] filter.
        """
        try:
            cap = int(max_results) if int(max_results) > 0 else int(getattr(runtime.settings, cap_attr, 10))
            y = _year_tuple(years)
            papers = fn(
                runtime.settings,
                [query.strip()],
                max_per_query=cap,
                years=y,
            )
            added = runtime.record_papers(papers, source)
            return _papers_json(source, query.strip(), papers, added)
        except Exception as exc:
            log.warning("%s search failed: %s", name, exc)
            runtime.state.setdefault("errors", []).append(f"{name}: {exc}")
            return json.dumps({"source": source, "query": query, "error": str(exc)}, ensure_ascii=False)

    return StructuredTool.from_function(
        func=_search,
        name=name,
        description=(
            f"Search {source.upper()} for academic papers matching a query. "
            "Returns a JSON object with the paper count and each paper's title, year, "
            "authors, identifiers, citation count, venue, and an abstract excerpt."
        ),
    )


def _make_list_papers_tool(runtime: AgentRuntime) -> StructuredTool:
    def list_papers() -> str:
        """Return the current deduplicated, numbered reference list in memory.

        Call this before drafting or submitting so inline [#] citations match the
        reference list that will actually be written to disk.
        """
        papers = runtime.current_papers()
        if not papers:
            return json.dumps({"count": 0, "references": []}, ensure_ascii=False)
        ranked = merge_and_rank(papers, top_k=None)
        refs = [{"number": i + 1, **{k: p.model_dump().get(k) for k in ("title", "year", "authors", "doi", "arxiv_id", "citation_count", "source")}} for i, p in enumerate(ranked)]
        return json.dumps({"count": len(refs), "references": refs}, ensure_ascii=False)

    return StructuredTool.from_function(
        func=list_papers,
        name="list_papers",
        description="List the papers collected so far as a numbered reference list. Use before final submission to keep citations consistent.",
    )


def _make_review_search_tool(runtime: AgentRuntime) -> StructuredTool:
    def review_search_coverage() -> str:
        """Critique the current search coverage and suggest additional queries.

        This is the first explicit self-reflection step. It does not run a search
        itself; it returns an honest assessment of what is likely missing so you can
        decide whether to search more.
        """
        max_reflections = int(runtime.settings.max_reflections)
        if runtime.reflection_count("search_coverage") >= max_reflections:
            return json.dumps({"status": "limit_reached", "message": "Search coverage already reviewed."}, ensure_ascii=False)

        papers = runtime.current_papers()
        titles = "\n".join(f"- {p.title} ({p.year or 'n.d.'})" for p in papers[:30]) or "(none yet)"
        topic = runtime.state.get("topic", "")
        plan = runtime.state.get("plan", {})
        queries = plan.get("queries", []) if isinstance(plan, dict) else []

        data = runtime.client.invoke_json(
            system=(
                "You are the self-critique component of a literature-review agent. "
                "Assess whether the current search results sufficiently cover the topic "
                "across subfields, methods, datasets, and recent work. Be concise and specific. "
                'Respond with JSON: {"coverage_gaps": ["..."], "suggested_queries": ["..."], "verdict": "sufficient|insufficient"}.'
            ),
            user=(
                f"Topic: {topic}\n"
                f"Previous/current queries: {queries}\n"
                f"Papers found: {len(papers)}\n"
                f"Titles:\n{titles}"
            ),
            tag="reflection.search_coverage",
            temperature=0.3,
            fallback={"coverage_gaps": [], "suggested_queries": [], "verdict": "sufficient"},
        )
        runtime.state.setdefault("reflections", []).append(
            {"step": int(runtime.state.get("step", 0)), "type": "search_coverage", "data": data}
        )
        return json.dumps({"type": "search_coverage", **data}, ensure_ascii=False)

    return StructuredTool.from_function(
        func=review_search_coverage,
        name="review_search_coverage",
        description="Critique the search coverage so far and get suggestions for additional queries. Call after collecting an initial corpus.",
    )


def _make_review_draft_tool(runtime: AgentRuntime) -> StructuredTool:
    def review_report_draft(sections: Optional[dict[str, str]] = None) -> str:
        """Critique the current report drafts and return per-section revision notes.

        This is the second explicit self-reflection step. Call it after drafting the
        sections and before submit_report; revise the drafts according to the notes.

        Args:
            sections: Optional mapping of the current section drafts. When provided,
                the drafts are recorded in working memory before they are critiqued,
                so the agent can hand its latest draft to this self-review step.
        """
        max_reflections = int(runtime.settings.max_reflections)
        if runtime.reflection_count("report_draft") >= max_reflections:
            return json.dumps({"status": "limit_reached", "message": "Report draft already reviewed."}, ensure_ascii=False)

        if sections:
            runtime.state["drafts"] = dict(sections)

        drafts = runtime.state.get("drafts", {}) or {}
        if not drafts:
            return json.dumps({"status": "no_draft", "message": "No section drafts are available yet."}, ensure_ascii=False)

        sections_block = "\n\n".join(f"## {SECTIONS_BY_NAME.get(k, k)}\n{v}" for k, v in drafts.items() if k in SECTIONS_BY_NAME) or str(drafts)
        topic = runtime.state.get("topic", "")
        language = runtime.state.get("language", "en")
        lang_name = "Chinese (Simplified)" if str(language).lower().startswith("zh") else "English"

        data = runtime.client.invoke_json(
            system=(
                "You are the revision editor of a literature review. Review the draft "
                f"sections (written in {lang_name}) for factual grounding, citation accuracy, "
                "coherence, and coverage of the topic. Identify concrete weaknesses only; "
                "do not invent new facts or citations. "
                'Respond with JSON: {"per_section": {"background": "...", ...}, "overall": "..."}.'
            ),
            user=f"Topic: {topic}\n\nDraft:\n{sections_block}",
            tag="reflection.report_draft",
            temperature=0.3,
            fallback={"per_section": {}, "overall": "Draft is acceptable."},
        )
        runtime.state.setdefault("reflections", []).append(
            {"step": int(runtime.state.get("step", 0)), "type": "report_draft", "data": data}
        )
        return json.dumps({"type": "report_draft", **data}, ensure_ascii=False)

    return StructuredTool.from_function(
        func=review_report_draft,
        name="review_report_draft",
        description="Critique the current report drafts (pass them via the optional 'sections' argument) and return revision notes. Revise drafts using the notes before calling submit_report.",
    )


def _make_submit_report_tool(runtime: AgentRuntime) -> StructuredTool:
    def submit_report(sections: dict[str, str]) -> str:
        """Submit the final report sections and write the Markdown report.

        Args:
            sections: Mapping of section name to Markdown body. Use the five standard
                section keys: background, methods, datasets, trends, open_problems.
        """
        try:
            drafts = dict(runtime.state.get("drafts", {}) or {})
            normalized: dict[str, str] = {}
            for spec in SECTIONS:
                normalized[spec.name] = (sections or {}).get(spec.name) or drafts.get(spec.name) or spec.placeholder

            # References always come from the real collected corpus.
            papers = runtime.current_papers()
            ranked = merge_and_rank(papers, top_k=int(runtime.state.get("top_k", 30)))

            runtime.state["sections"] = normalized
            runtime.state["drafts"] = normalized
            runtime.state["merged"] = ranked
            runtime.state["done"] = True

            write_state = {
                "topic": runtime.state.get("topic", ""),
                "language": runtime.state.get("language", "en"),
                "sections": normalized,
                "merged": ranked,
                "output_path": str(runtime.output_path or runtime.state.get("output_path", "report.md")),
            }
            out = write_report(write_state, output_path=runtime.output_path)
            return json.dumps({"status": "written", "path": str(out)}, ensure_ascii=False)
        except Exception as exc:
            log.exception("submit_report failed")
            runtime.state.setdefault("errors", []).append(f"submit_report: {exc}")
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    return StructuredTool.from_function(
        func=submit_report,
        name="submit_report",
        description="Write the final literature review report to disk using the provided section bodies. References are generated automatically from collected papers.",
    )


# section name -> title mapping for draft critique
SECTIONS_BY_NAME = {spec.name: spec.title for spec in SECTIONS}


def build_tools(runtime: AgentRuntime) -> list[StructuredTool]:
    """Build the full ReAct tool list for one run."""
    tools: list[StructuredTool] = [
        _make_search_tool(runtime, "search_arxiv", "arxiv", search_arxiv),
        _make_search_tool(runtime, "search_openalex", "openalex", search_openalex),
        _make_search_tool(runtime, "search_huggingface", "huggingface", search_huggingface),
        _make_search_tool(runtime, "search_semantic_scholar", "semantic_scholar", search_semantic_scholar),
        _make_search_tool(runtime, "search_crossref", "crossref", search_crossref),
        _make_list_papers_tool(runtime),
        _make_review_search_tool(runtime),
        _make_review_draft_tool(runtime),
        _make_submit_report_tool(runtime),
    ]
    return tools
