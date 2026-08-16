"""Tests for :mod:`lit_review.runner`.

These tests focus on the pure-Python seams plus one full offline ``run()``
execution (with the network seam monkeypatched):

* :func:`runner._normalize_state` applies defaults from Settings
* :class:`runner.RunResult` exposes ``papers``, ``sections``, ``errors``
* :func:`runner._safe_jsonify` recursively converts non-JSON values
* :func:`runner.run` populates ``metrics.merged`` dedupe statistics
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lit_review.config import Settings
from lit_review.state import GraphState, Paper
from lit_review.runner import (
    Metrics,
    RunResult,
    _normalize_state,
    _safe_jsonify,
)


# --------------------------------------------------------------------------- #
# _normalize_state
# --------------------------------------------------------------------------- #


def _settings() -> Settings:
    return Settings(llm_api_key="", request_timeout=10.0)


def test_normalize_state_pulls_defaults_from_settings():
    state = GraphState(topic="RAG", language="")
    out = _normalize_state(state, _settings())
    assert out["topic"] == "RAG"
    assert out["language"] == "en"
    assert out["sources"] == _settings().enabled_sources()
    assert out["years"] == _settings().year_window()
    assert out["top_k"] == 30
    assert out["max_iter"] == 2
    assert out["errors"] == []


def test_normalize_state_preserves_user_supplied_keys():
    state = GraphState(topic="X", language="zh", top_k=10, max_iter=3,
                       sources=["arxiv"], years=(2020, 2024),
                       output_path="r.md", no_llm=True, verbose=True)
    out = _normalize_state(state, _settings())
    assert out["language"] == "zh"
    assert out["top_k"] == 10
    assert out["max_iter"] == 3
    assert out["sources"] == ["arxiv"]
    assert out["years"] == (2020, 2024)
    assert out["output_path"] == "r.md"
    assert out["no_llm"] is True
    assert out["verbose"] is True


def test_normalize_state_coerces_language_to_known_value():
    state = GraphState(topic="X", language="fr")
    out = _normalize_state(state, _settings())
    assert out["language"] == "en"


# --------------------------------------------------------------------------- #
# RunResult
# --------------------------------------------------------------------------- #


def test_run_result_exposes_state_helpers():
    state = {
        "merged": [Paper(title="X", year=2024), Paper(title="Y", year=2023)],
        "sections": {"background": "body", "methods": "x"},
        "errors": ["a", "b"],
    }
    rr = RunResult(state=state, output_path=Path("/tmp/x.md"))
    assert len(rr.papers) == 2
    assert rr.papers[0].title == "X"
    assert rr.sections["background"] == "body"
    assert rr.errors == ["a", "b"]


def test_run_result_handles_missing_keys():
    state: dict = {}
    rr = RunResult(state=state, output_path=Path("/tmp/x.md"))
    assert rr.papers == []
    assert rr.sections == {}
    assert rr.errors == []


# --------------------------------------------------------------------------- #
# _safe_jsonify
# --------------------------------------------------------------------------- #


def test_safe_jsonify_basic():
    assert _safe_jsonify({"a": 1, "b": [1, 2, "x"]}) == {"a": 1, "b": [1, 2, "x"]}


def test_safe_jsonify_filters_private_keys():
    out = _safe_jsonify({"topic": "t", "__llm_client__": "secret", "__metrics__": 42})
    assert out == {"topic": "t"}


def test_safe_jsonify_falls_back_to_repr_for_unknown():
    class Weird:
        def __repr__(self):
            return "<Weird>"

    out = _safe_jsonify({"k": Weird()})
    assert isinstance(out["k"], str)
    assert "Weird" in out["k"]


# --------------------------------------------------------------------------- #
# run() -> metrics.merged
# --------------------------------------------------------------------------- #


def test_run_populates_merged_metrics(tmp_path: Path, monkeypatch):
    """``runner.run()`` fills ``metrics.merged`` from the dedupe stats nodes emit."""
    from lit_review.graph import nodes as nodes_mod
    from lit_review.runner import run

    fake = [
        Paper(title=f"Paper {i}", authors=["X"], year=2024, abstract=f"abs {i}", citation_count=i, source="arxiv")
        for i in range(5)
    ]
    monkeypatch.setattr(nodes_mod, "_run_sources", lambda *a, **kw: (fake, []))

    state = GraphState(
        topic="x", language="en", top_k=10, max_iter=1, no_llm=True,
        output_path=str(tmp_path / "r.md"),
    )
    result = run(state, _settings(), emit_metrics=True)

    assert result.metrics is not None
    assert result.metrics.merged["clusters_before"] == 5
    assert result.metrics.merged["papers_after_dedupe"] == 5
    assert result.metrics.merged["kept_after_topk"] == 5
    assert (tmp_path / "r.md.metrics.json").exists()
    assert result.output_path.exists()


def test_run_streams_per_node_callbacks_and_timings(tmp_path: Path, monkeypatch):
    """run() streams per-node events (``--verbose``) and keeps node timings.

    Regression: LangGraph strips state keys it doesn't know about, so
    ``__node_times__`` / ``__dedupe_stats__`` were silently lost and
    ``on_node`` only fired once at the end. Now every node is reported and
    metrics reflect real timings + dedupe stats.
    """
    from lit_review.graph import nodes as nodes_mod
    from lit_review.runner import run

    fake = [
        Paper(title=f"Paper {i}", authors=["X"], year=2024, abstract=f"abs {i}", citation_count=i, source="arxiv")
        for i in range(3)
    ]
    monkeypatch.setattr(nodes_mod, "_run_sources", lambda *a, **kw: (fake, []))

    calls: list[str] = []
    state = GraphState(
        topic="x", language="en", top_k=10, max_iter=1, no_llm=True,
        output_path=str(tmp_path / "r.md"),
    )
    result = run(
        state, _settings(), emit_metrics=True,
        on_node=lambda name, _updates: calls.append(name),
    )

    for node in ("plan", "search_sources", "dedupe_rank", "filter_top_k",
                 "synthesize_sections", "assemble"):
        assert node in calls, f"on_node missing {node}"
        assert node in result.metrics.nodes, f"timing missing {node}"

    assert result.metrics.merged["clusters_before"] == 3
    assert result.metrics.merged["papers_after_dedupe"] == 3
    assert result.metrics.merged["kept_after_topk"] == 3
    assert result.metrics.sources.get("arxiv") == 3


def test_run_llm_metrics_reflect_shared_client(tmp_path: Path, monkeypatch):
    """Nodes must use the runner's LLMClient so metrics.llm is populated.

    Regression: the runner stashed an LLMClient in ``__llm_client__`` but the
    key wasn't a declared LangGraph channel, so every node built its own
    private client and ``metrics.llm`` reported zeros.
    """
    from lit_review.graph import nodes as nodes_mod
    from lit_review.runner import run

    class _FakeResp:
        content = "A short synthesized paragraph for the section."
        usage_metadata = {"input_tokens": 12, "output_tokens": 7}

    class _FakeModel:
        def __init__(self):
            self.invoked = 0

        def invoke(self, messages):
            self.invoked += 1
            return _FakeResp()

    fake_model = _FakeModel()
    monkeypatch.setattr("lit_review.llm.build_chat_model", lambda *a, **kw: fake_model)
    monkeypatch.setattr(nodes_mod, "_run_sources", lambda *a, **kw: (
        [Paper(title=f"P{i}", year=2024, authors=["X"], abstract="a" * 60, source="arxiv") for i in range(3)],
        [],
    ))

    state = GraphState(
        topic="x", language="en", top_k=10, max_iter=1,
        output_path=str(tmp_path / "r.md"),
    )
    result = run(state, Settings(llm_api_key="sk-fake", request_timeout=10.0), emit_metrics=True)

    assert fake_model.invoked > 0
    assert result.metrics.llm["calls"] > 0
    assert result.metrics.llm["by_tag"]  # plan/synthesize.* present
