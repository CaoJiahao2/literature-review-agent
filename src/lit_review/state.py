"""State schemas shared across the LangGraph nodes.

A `Paper` is the canonical record produced by every source tool. Sources may
fill only a subset of fields (e.g. arXiv has no citation count, OpenAlex has
no arxiv id when the work isn't on arXiv) — the rank/merge step reconciles
them.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field


class Paper(BaseModel):
    """Canonical paper record.

    All fields are best-effort: a source fills what it knows and leaves the
    rest empty. `rank.merge_papers` reconciles duplicates.
    """

    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    abstract: str = ""
    venue: str = ""
    url: str = ""
    doi: str = ""
    arxiv_id: str = ""
    categories: list[str] = Field(default_factory=list)
    citation_count: Optional[int] = None
    source: str = ""  # "arxiv" | "openalex" | "merged"
    extra: dict[str, Any] = Field(default_factory=dict)

    # Filled by rank.merge_papers; used for sorting and dedupe.
    dedupe_key: str = ""
    score: float = 0.0

    def short_id(self) -> str:
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id.lower()}"
        return f"title:{_norm_title(self.title)}"

    def canonical_url(self) -> str:
        """Return the most reader-friendly URL for this paper.

        Priority: DOI > arXiv > the provided URL (skipping OpenAlex
        internal IDs since those don't help humans).
        """
        if self.doi:
            return f"https://doi.org/{self.doi}"
        if self.arxiv_id:
            return f"https://arxiv.org/abs/{self.arxiv_id}"
        if self.url and "openalex.org/works/" not in self.url:
            return self.url
        # OpenAlex URLs are internal IDs — useless for readers.
        return ""

    def display_ref(self, n: int) -> str:
        """One-line citation suitable for the references list."""
        authors = ", ".join(self.authors[:3]) + (" et al." if len(self.authors) > 3 else "")
        year = f" ({self.year})" if self.year else ""
        venue = f" *{self.venue}*" if self.venue else ""
        link = self.canonical_url()
        link_part = f" [{link}]({link})" if link else ""
        sources = f" _{self.source}_" if self.source else ""
        return f"[{n}] {authors}{year}. {self.title}.{venue}{sources}{link_part}"


class SearchPlan(BaseModel):
    """Output of the `plan` node: keyword queries + a topic framing."""

    topic_summary: str = ""
    queries: list[str] = Field(default_factory=list)
    rationale: str = ""


class SectionDraft(BaseModel):
    name: str
    title: str
    body: str = ""


class GraphState(dict):
    """LangGraph state container.

    LangGraph passes dicts through nodes; we use a plain dict subclass with
    documented keys so any node can read/write safely.

    The ``__dunder__`` keys are **internal channels** that must be declared
    here: LangGraph only carries keys it knows about across nodes, so an
    undeclared key (e.g. one a node writes in place) would be silently
    stripped from the state and lost.
    """

    # Internal channels — see module docstring for why they're declared.
    __node_times__: dict[str, int] = {}
    __dedupe_stats__: dict[str, int] = {}
    __llm_client__: Any = None

    topic: str
    language: str = "en"
    years: Optional[tuple[int, int]] = None
    top_k: int = 30
    max_iter: int = 2
    no_llm: bool = False
    output_path: str = "report.md"
    verbose: bool = False
    sources: list[str] = []   # which sources to query; populated from Settings if empty

    plan: Optional[SearchPlan] = None
    # Generic source-keyed results (replaces the per-source arxiv_results/openalex_results
    # keys used in v1). v1 keys are still written for back-compat with old callers.
    source_results: dict[str, list[Paper]] = {}
    arxiv_results: list[Paper] = []
    openalex_results: list[Paper] = []
    merged: list[Paper] = []
    iteration: int = 0
    sections: dict[str, str] = {}
    final_report: str = ""
    errors: list[str] = []


def _norm_title(title: str) -> str:
    import re

    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", title.lower())).strip()


def today_iso() -> str:
    return date.today().isoformat()
