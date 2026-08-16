"""End-to-end smoke test of the graph in --no-llm mode.

This exercises every node without hitting the LLM, but does still hit arXiv
and OpenAlex. Marked `network` so offline CI can skip it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lit_review.config import Settings
from lit_review.graph import build_graph
from lit_review.report.template import SECTIONS
from lit_review.report.writer import write_report
from lit_review.state import GraphState


@pytest.mark.network
def test_graph_no_llm_writes_full_report(tmp_path: Path, settings: Settings):
    state = GraphState(
        topic="transformer",
        language="en",
        years=(2022, 2026),
        top_k=10,
        max_iter=1,
        no_llm=True,
        output_path=str(tmp_path / "report.md"),
        verbose=False,
    )
    graph = build_graph(settings)
    final = graph.invoke(state)

    out = write_report(final, output_path=tmp_path / "report.md")
    text = out.read_text(encoding="utf-8")

    # All section headings present.
    for spec in SECTIONS:
        assert f"## {spec.title}" in text

    # References section with at least one paper.
    assert "## References" in text
    assert "[1]" in text

    # TOC present.
    assert "## Table of Contents" in text


def test_skeleton_only_path_when_llm_missing(monkeypatch, tmp_path: Path, settings: Settings):
    """Run the graph entirely offline with no-llm + mocked sources.

    We monkeypatch the node-level ``_run_sources`` helper (the seam the graph
    nodes actually call) so the test never touches the network — neither the
    async fan-out nor the sync fallback.
    """
    from lit_review.graph import nodes as nodes_mod
    from lit_review.state import Paper

    fake = [
        Paper(title=f"Paper {i}", authors=["X"], year=2024, abstract=f"abs {i}", citation_count=i, source="openalex")
        for i in range(1, 6)
    ]

    monkeypatch.setattr(
        nodes_mod,
        "_run_sources",
        lambda *a, **kw: (fake, []),
    )

    state = GraphState(
        topic="transformer",
        language="en",
        years=(2023, 2025),
        top_k=10,
        max_iter=1,
        no_llm=True,
        output_path=str(tmp_path / "report.md"),
        verbose=False,
    )
    graph = build_graph(settings)
    final = graph.invoke(state)

    out = write_report(final, output_path=tmp_path / "report.md")
    text = out.read_text(encoding="utf-8")

    for spec in SECTIONS:
        assert f"## {spec.title}" in text
    assert "## References" in text
    assert "[1] X" in text  # author in refs
    assert "Paper 1" in text  # a paper title appears



def test_synthesize_node_works_under_running_event_loop(settings: Settings):
    """The node must not call asyncio.run() from an already-running loop.

    Regression: v0.2's try/except both called ``asyncio.run``, which raises
    ``RuntimeError`` when a loop is already bound to the thread (Gradio/Jupyter
    hosts, LangGraph async streaming). It must offload to a worker thread.
    """
    import asyncio

    from lit_review.graph import nodes as nodes_mod
    from lit_review.state import GraphState

    state = GraphState(topic="x", language="en", no_llm=True, merged=[])

    async def _call_sync_in_coroutine():
        # Runs in the same thread as the (running) loop — the buggy path.
        return nodes_mod.synthesize_sections_node(state, settings)

    result = asyncio.run(_call_sync_in_coroutine())
    names = {spec.name for spec in nodes_mod.SECTIONS}
    assert set(result["sections"].keys()) == names
    # Every section has a body (skeleton in no-llm mode).
    assert all(v for v in result["sections"].values())
