"""Source tool clients + dedupe/rank."""

from .arxiv import search_arxiv
from .openalex import search_openalex
from .huggingface import search_huggingface
from .semantic_scholar import search_semantic_scholar
from .crossref import search_crossref
from .rank import merge_and_rank

ALL_SOURCES = (
    "arxiv",
    "openalex",
    "huggingface",
    "semantic_scholar",
    "crossref",
)

SOURCE_FNS = {
    "arxiv": search_arxiv,
    "openalex": search_openalex,
    "huggingface": search_huggingface,
    "semantic_scholar": search_semantic_scholar,
    "crossref": search_crossref,
}


def run_sources(settings, queries, *, sources, years):
    """Run a list of sources and return a flat list of Paper objects.

    Each source is independent; we collect errors per-source and keep going.
    """
    import logging

    log = logging.getLogger(__name__)
    out = []
    errors = []
    for name in sources:
        fn = SOURCE_FNS.get(name)
        if fn is None:
            errors.append(f"unknown source: {name}")
            continue
        try:
            out.extend(fn(settings, queries, years=years))
        except Exception as exc:
            log.warning("source %s failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
    return out, errors


__all__ = [
    "search_arxiv",
    "search_openalex",
    "search_huggingface",
    "search_semantic_scholar",
    "search_crossref",
    "merge_and_rank",
    "ALL_SOURCES",
    "SOURCE_FNS",
    "run_sources",
]
