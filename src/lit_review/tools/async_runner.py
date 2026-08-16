"""Async fan-out runner for source tools.

Why?
====

In v0.1 each source was queried serially; within each source, each query was
serial too. For a typical ``5 sources × 5 queries = 25 HTTP calls`` that meant
60-120 second runs.

The async runner dispatches all (source, query) pairs concurrently with a
configurable semaphore, capping open connections so polite-pool limits (e.g.
OpenAlex / S2) are respected.

Usage from a graph node::

    from ..tools.async_runner import run_sources_async

    papers, errors = await run_sources_async(
        settings, queries, sources=sources, years=years,
        max_concurrent_sources=4,
    )

Sync sources are still supported: any source whose registered function is
*synchronous* is dispatched via ``asyncio.to_thread`` so callers don't need
separate branches.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Awaitable, Callable, Iterable, Optional

from ..config import Settings
from ..state import Paper
from ._http import get_async_client

log = logging.getLogger(__name__)


# Async source functions, looked up by name. Populated lazily by each
# ``tools/<source>.py`` via :func:`register_async_source`. If a source has no
# async implementation, we dispatch it via ``asyncio.to_thread``.
ASYNC_SOURCE_FNS: dict[str, Callable[..., Awaitable[list[Paper]]]] = {}


def register_async_source(name: str, fn: Callable[..., Awaitable[list[Paper]]]) -> None:
    """Register (or override) an async source function for ``name``."""
    ASYNC_SOURCE_FNS[name] = fn


# Quotas: per-source concurrency hint. Sources without an entry default to 2.
_DEFAULT_PER_SOURCE_CONCURRENCY: dict[str, int] = {
    "arxiv": 3,
    "openalex": 2,
    "semantic_scholar": 1,
    "crossref": 2,
    "huggingface": 1,  # serial-by-design (iterates calendar days)
}


async def _dispatch_one_source(
    name: str,
    settings: Settings,
    queries: Iterable[str],
    *,
    years: Optional[tuple[int, int]],
    max_per_query: Optional[int],
    semaphore: asyncio.Semaphore,
) -> tuple[list[Paper], list[str]]:
    """Run a single source over all queries, honoring its per-source concurrency."""
    async with semaphore:
        out: list[Paper] = []
        errors: list[str] = []

        cap = _cap_from_settings(settings, name) if max_per_query is None else max_per_query

        async_fn = ASYNC_SOURCE_FNS.get(name)
        if async_fn is not None:
            try:
                papers = await async_fn(
                    _current_async_client(),  # see _shared_client()
                    settings,
                    list(queries),
                    max_per_query=cap,
                    years=years,
                )
                out.extend(papers)
                return out, errors
            except Exception as exc:
                log.warning("async source %s failed: %s", name, exc)
                errors.append(f"{name}: {exc}")
                return out, errors

        # Fallback to the sync implementation, off-loaded to a thread.
        from . import SOURCE_FNS as _SF
        sync_fn = _SF.get(name)
        if sync_fn is None:
            errors.append(f"unknown source: {name}")
            return out, errors
        try:
            papers = await asyncio.to_thread(
                sync_fn,
                settings,
                list(queries),
                max_per_query=cap,
                years=years,
            )
            out.extend(papers)
        except Exception as exc:
            log.warning("source %s failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
        return out, errors


_CAP_ATTRS = {
    "arxiv": "arxiv_max_per_query",
    "openalex": "openalex_max_per_query",
    "huggingface": "huggingface_max_per_query",
    "semantic_scholar": "semantic_scholar_max_per_query",
    "crossref": "crossref_max_per_query",
}


def _cap_from_settings(settings: Settings, name: str) -> int:
    """Per-query cap for a source, sourced from ``Settings`` (``.env``).

    Falls back to 10 when the source has no per-source knob, so the async
    fan-out honors the same ``*_MAX_PER_QUERY`` limits the sync path uses.
    """
    attr = _CAP_ATTRS.get(name)
    if attr is None:
        return 10
    try:
        val = int(getattr(settings, attr) or 0)
    except Exception:
        return 10
    return val if val > 0 else 10


# Shared async client lifecycle ------------------------------------------------

_async_client: Optional[tuple[Settings, "httpx.AsyncClient"]] = None  # type: ignore[name-defined]


def _current_async_client():
    """Return the active async client (lazily created).

    The client is shared across sources for connection pooling, mirroring how
    the sync ``get_client`` pattern is used by individual sources. It is closed
    at the end of a run by :func:`close_async_client`.
    """
    return _async_client[1] if _async_client is not None else None


async def _ensure_async_client(settings: Settings) -> "httpx.AsyncClient":  # type: ignore[name-defined]
    global _async_client
    if _async_client is None or _async_client[0] is not settings:
        await close_async_client()
        client = get_async_client(settings)
        _async_client = (settings, client)
    return _async_client[1]


async def close_async_client() -> None:
    global _async_client
    if _async_client is not None:
        _, client = _async_client
        try:
            await client.aclose()
        except Exception:
            pass
        _async_client = None


# Public API ------------------------------------------------------------------


async def run_sources_async(
    settings: Settings,
    queries: Iterable[str],
    *,
    sources: Iterable[str],
    years: Optional[tuple[int, int]] = None,
    max_per_query: Optional[int] = None,
    max_concurrent_sources: int = 4,
    max_concurrent_queries_per_source: int = 3,
) -> tuple[list[Paper], list[str]]:
    """Run ``sources`` against ``queries`` concurrently.

    Parameters
    ----------
    max_concurrent_sources:
        Upper bound on how many sources run in parallel. Defaults to 4.
    max_concurrent_queries_per_source:
        Upper bound on how many queries each source handles in parallel.
        Bounded by per-source overrides in :data:`_DEFAULT_PER_SOURCE_CONCURRENCY`.

    Returns
    -------
    (papers, errors)
        ``papers`` is a flat list of ``Paper`` records from every source; ``errors``
        is a list of human-readable error messages collected across failures
        (an exception in one source never aborts the rest of the run).
    """
    sources_list = [s for s in sources if s]
    queries_list = [q for q in queries if q and q.strip()]
    if not sources_list or not queries_list:
        return [], []

    client = await _ensure_async_client(settings)
    # Use a small wrapper to keep the contract even when the source is sync.
    global _async_client
    _async_client = (settings, client) if _async_client is None else _async_client

    sem_overall = asyncio.Semaphore(max_concurrent_sources)

    async def _run_one_source(source_name: str) -> tuple[str, list[Paper], list[str]]:
        # All queries for this source dispatched in parallel under sem_query.
        per_source = max(
            1, _DEFAULT_PER_SOURCE_CONCURRENCY.get(source_name, max_concurrent_queries_per_source)
        )
        sem_query = asyncio.Semaphore(per_source)

        async def _one_query(q: str) -> tuple[list[Paper], list[str]]:
            async with sem_query:
                return await _dispatch_one_source(
                    source_name, settings, [q],
                    years=years,
                    max_per_query=max_per_query,
                    semaphore=asyncio.Semaphore(1),
                )

        per_query_results = await asyncio.gather(
            *(_one_query(q) for q in queries_list),
            return_exceptions=True,
        )
        papers: list[Paper] = []
        errors: list[str] = []
        for r in per_query_results:
            if isinstance(r, Exception):
                errors.append(f"{source_name}: {r}")
            else:
                q_papers, q_errors = r
                papers.extend(q_papers)
                errors.extend(q_errors)
        return source_name, papers, errors

    async def _wrapped(name: str):
        async with sem_overall:
            return await _run_one_source(name)

    coros = [_wrapped(name) for name in sources_list]

    results = await asyncio.gather(*coros, return_exceptions=True)

    all_papers: list[Paper] = []
    all_errors: list[str] = []
    per_source_counts: dict[str, int] = {}
    for r in results:
        if isinstance(r, Exception):
            all_errors.append(f"dispatch: {r}")
            continue
        try:
            name, papers, errors = r
        except Exception:  # pragma: no cover
            continue
        per_source_counts[name] = len(papers)
        all_papers.extend(papers)
        all_errors.extend(errors)

    # Best-effort propagation of counts (used by metrics).
    log.debug("async runner results: %s", per_source_counts)
    return all_papers, all_errors


__all__ = [
    "ASYNC_SOURCE_FNS",
    "register_async_source",
    "run_sources_async",
    "close_async_client",
]
