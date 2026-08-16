"""Source tool clients + dedupe/rank (sync) and async fan-out (v0.2+)."""

from .arxiv import search_arxiv
from .openalex import search_openalex
from .huggingface import search_huggingface
from .semantic_scholar import search_semantic_scholar
from .crossref import search_crossref
from .rank import merge_and_rank
from . import async_runner as _async_runner
from .arxiv_async import search_arxiv_async
from .openalex_async import search_openalex_async
from .crossref_async import search_crossref_async
from .semantic_scholar_async import search_semantic_scholar_async

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

# Register async implementations. Sources without one stay on the to_thread
# fallback in async_runner. Hugging Face intentionally has no async variant
# because its endpoint only exposes trending daily papers (one HTTP per day).
_async_runner.register_async_source("arxiv", search_arxiv_async)
_async_runner.register_async_source("openalex", search_openalex_async)
_async_runner.register_async_source("semantic_scholar", search_semantic_scholar_async)
_async_runner.register_async_source("crossref", search_crossref_async)


def run_sources(settings, queries, *, sources, years):
    """Run a list of sources and return a flat list of Paper objects.

    Each source is independent; we collect errors per-source and keep going.
    Provided for backwards compatibility with v0.1 callers. New code should
    use :func:`async_runner.run_sources_async`.
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


# Re-export async helpers so callers don't need to know about async_runner.
ASYNC_SOURCE_FNS = _async_runner.ASYNC_SOURCE_FNS
register_async_source = _async_runner.register_async_source
run_sources_async = _async_runner.run_sources_async
close_async_client = _async_runner.close_async_client


__all__ = [
    "search_arxiv",
    "search_openalex",
    "search_huggingface",
    "search_semantic_scholar",
    "search_crossref",
    "merge_and_rank",
    "ALL_SOURCES",
    "SOURCE_FNS",
    "ASYNC_SOURCE_FNS",
    "register_async_source",
    "run_sources",
    "run_sources_async",
    "close_async_client",
]
