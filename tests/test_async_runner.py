"""Tests for async source registration and fan-out runner.

These tests focus on the orchestration contract: registering async sources,
dispatching through ``asyncio.to_thread`` when a source is sync, and
collecting partial errors so one bad source doesn't kill the run.
"""

from __future__ import annotations

import asyncio
from typing import Iterable, Optional

import pytest

from lit_review.config import Settings
from lit_review.state import Paper
from lit_review.tools import (
    ASYNC_SOURCE_FNS,
    register_async_source,
    run_sources,
    run_sources_async,
    close_async_client,
)
from lit_review.tools import async_runner as ar


@pytest.fixture(autouse=True)
def _cleanup_async():
    """Close the shared async client after each test."""
    yield
    asyncio.run(close_async_client())


def _settings() -> Settings:
    return Settings(llm_api_key="", request_timeout=5.0)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def test_async_source_fns_populated():
    """The bundled async sources are registered at import time."""
    assert {"arxiv", "openalex", "crossref", "semantic_scholar"} <= set(ASYNC_SOURCE_FNS.keys())


def test_register_async_source_overrides():
    sentinel = object()

    def _fake(*args, **kwargs):  # pragma: no cover - never awaited here
        return sentinel

    register_async_source("arxiv", _fake)
    assert ASYNC_SOURCE_FNS["arxiv"] is _fake
    # Reset so subsequent tests get the bundled implementation back.
    from lit_review.tools.arxiv_async import search_arxiv_async
    register_async_source("arxiv", search_arxiv_async)


# --------------------------------------------------------------------------- #
# Sync fallback
# --------------------------------------------------------------------------- #


def test_sync_run_sources_collects_errors():
    out, errors = run_sources(
        _settings(),
        ["x"],
        sources=["not_a_real_source"],
        years=None,
    )
    assert out == []
    assert errors and "not_a_real_source" in errors[0]


def test_run_sources_calls_each_source(monkeypatch):
    """The sync fan-out invokes every source registered."""
    calls = []

    def _stub_search(settings, queries, *, years=None):
        calls.append(list(queries))
        return [
            Paper(title=f"p{i}", authors=["a"], year=2024, source="stub")
            for i, _ in enumerate(queries)
        ]

    monkeypatch.setitem(__import__("lit_review.tools", fromlist=["SOURCE_FNS"]).SOURCE_FNS, "stub", _stub_search)
    out, errors = run_sources(
        _settings(),
        ["q1", "q2"],
        sources=["stub"],
        years=None,
    )
    assert calls == [["q1", "q2"]]  # sync runner dispatches all queries in one call
    assert errors == []
    assert len(out) == 2  # queries=["q1","q2"] => two Papers from _stub_search


# --------------------------------------------------------------------------- #
# Async fan-out
# --------------------------------------------------------------------------- #


def test_run_sources_async_with_only_sync_sources(monkeypatch):
    """A source without an async impl is dispatched via to_thread."""
    calls = []

    def _sync(settings, queries, *, max_per_query=None, years=None):
        calls.append(list(queries))
        return [Paper(title="ok", year=2024, source="sync-only")]

    monkeypatch.setattr(
        "lit_review.tools.async_runner.ASYNC_SOURCE_FNS", {},
        raising=False,
    )
    monkeypatch.setitem(
        __import__("lit_review.tools", fromlist=["SOURCE_FNS"]).SOURCE_FNS,
        "sync-only",
        _sync,
    )

    async def _go():
        return await run_sources_async(
            _settings(), ["q1", "q2"], sources=["sync-only"]
        )

    papers, errors = asyncio.run(_go())
    # The two queries should have been forwarded in one bundled call.
    assert sorted(calls) == [["q1"], ["q2"]]  # one call per query (with single-q list)
    assert errors == []
    assert len(papers) == 2  # one per query
    assert all(p.source == "sync-only" for p in papers)


def test_run_sources_async_collects_source_errors(monkeypatch):
    """A failing async source doesn't kill the rest of the fan-out."""
    async def _ok(client, settings, queries, *, max_per_query, years=None):
        return [Paper(title="t", year=2024, source="ok-source")]

    async def _bad(client, settings, queries, *, max_per_query, years=None):
        raise RuntimeError("boom")

    # Register a fake async source that raises.
    from lit_review.tools.async_runner import register_async_source as _reg

    _reg("bad-async", _bad)
    _reg("ok-async", _ok)
    try:
        async def _go():
            return await run_sources_async(
                _settings(), ["q1"], sources=["bad-async", "ok-async"]
            )
        papers, errors = asyncio.run(_go())
        assert any("bad-async" in e and "boom" in e for e in errors) or any("boom" in e for e in errors)
        # The good source still produced a paper.
        assert any(p.source == "ok-source" for p in papers)
    finally:
        # Reset the registration table.
        ar.ASYNC_SOURCE_FNS.pop("bad-async", None)
        ar.ASYNC_SOURCE_FNS.pop("ok-async", None)


def test_run_sources_async_no_sources():
    async def _go():
        return await run_sources_async(_settings(), ["q"], sources=[])
    papers, errors = asyncio.run(_go())
    assert papers == []
    assert errors == []


def test_run_sources_async_honors_per_source_caps(monkeypatch):
    """The async fan-out must use Settings.*_max_per_query, not a hardcoded cap."""
    from lit_review.tools import async_runner as ar

    seen: dict[str, int] = {}

    async def _arxiv(client, settings, queries, *, max_per_query, years=None):
        seen["arxiv"] = max_per_query
        return []

    async def _crossref(client, settings, queries, *, max_per_query, years=None):
        seen["crossref"] = max_per_query
        return []

    ar.ASYNC_SOURCE_FNS["arxiv"] = _arxiv
    ar.ASYNC_SOURCE_FNS["crossref"] = _crossref
    try:
        async def _go():
            return await ar.run_sources_async(
                Settings(arxiv_max_per_query=3, crossref_max_per_query=7),
                ["q1"],
                sources=["arxiv", "crossref"],
            )
        asyncio.run(_go())
        assert seen.get("arxiv") == 3
        assert seen.get("crossref") == 7
    finally:
        from lit_review.tools.arxiv_async import search_arxiv_async
        from lit_review.tools.crossref_async import search_crossref_async
        ar.ASYNC_SOURCE_FNS["arxiv"] = search_arxiv_async
        ar.ASYNC_SOURCE_FNS["crossref"] = search_crossref_async
