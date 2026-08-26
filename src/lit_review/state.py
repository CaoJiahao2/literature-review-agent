"""Shared state types for the ReAct literature-review agent.

A `Paper` remains the canonical record produced by every source tool. The old
LangGraph `GraphState` pipeline has been replaced by a single ReAct agent whose
working memory is an `AgentState` dict: the LLM message transcript, collected
papers, section drafts, and reflection notes all live here for the duration of
one run.
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

    # Filled by rank.merge_and_rank; used for sorting and dedupe.
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
    """Output of the agent's initial planning step."""

    topic_summary: str = ""
    queries: list[str] = Field(default_factory=list)
    rationale: str = ""


class AgentState(dict):
    """In-run working memory for the ReAct agent.

    LangChain tool functions mutate this dict through a shared `AgentRuntime`;
    it is also the object returned in `RunResult.state`. The agent transcript
    lives in `messages`; retrieved papers accumulate in `papers`; section text
    accumulates in `drafts`.
    """

    topic: str
    language: str = "en"
    years: Optional[tuple[int, int]] = None
    top_k: int = 30
    sources: list[str] = []
    output_path: str = "report.md"
    verbose: bool = False

    messages: list[Any] = []          # langchain_core.messages.BaseMessage
    papers: list[Paper] = []          # all papers collected this run (deduped lazily)
    plan: dict[str, Any] = {}
    drafts: dict[str, str] = {}
    reflections: list[dict[str, Any]] = []
    merged: list[Paper] = []          # Top-K after submit_report
    sections: dict[str, str] = {}     # alias kept for report writer compatibility
    step: int = 0
    tool_calls: int = 0
    done: bool = False
    errors: list[str] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        # Each instance gets its own fresh defaults; mutable containers are
        # never shared across instances (the class-level annotations above are
        # documentation only, not instance defaults).
        self.update(
            {
                "topic": "",
                "language": "en",
                "years": None,
                "top_k": 30,
                "sources": [],
                "output_path": "report.md",
                "verbose": False,
                "messages": [],
                "papers": [],
                "plan": {},
                "drafts": {},
                "reflections": [],
                "merged": [],
                "sections": {},
                "step": 0,
                "tool_calls": 0,
                "done": False,
                "errors": [],
            }
        )
        # Caller-supplied values override defaults; mutable containers are
        # defensively copied so callers cannot accidentally share references.
        for key, value in kwargs.items():
            if isinstance(value, dict):
                self[key] = dict(value)
            elif isinstance(value, list):
                self[key] = list(value)
            else:
                self[key] = value


def _norm_title(title: str) -> str:
    import re

    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", title.lower())).strip()


def today_iso() -> str:
    return date.today().isoformat()
