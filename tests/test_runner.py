"""Tests for :mod:`lit_review.runner`.

These tests cover the pure-Python seams plus one full offline ``run()`` with
the ReAct agent replaced by a fake, so no network or LLM is required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lit_review.config import ConfigurationError, Settings
from lit_review.report.writer import write_report
from lit_review.runner import Metrics, RunResult, _normalize_state, _safe_jsonify, run
from lit_review.state import AgentState, Paper


def _settings() -> Settings:
    return Settings(llm_api_key="", request_timeout=10.0)


# --------------------------------------------------------------------------- #
# _normalize_state
# --------------------------------------------------------------------------- #


def test_normalize_state_pulls_defaults_from_settings():
    state = AgentState(topic="RAG", language="")
    out = _normalize_state(state, _settings())
    assert out["topic"] == "RAG"
    assert out["language"] == "en"
    assert out["sources"] == _settings().enabled_sources()
    assert out["years"] == _settings().year_window()
    assert out["top_k"] == 30
    assert out["step"] == 0
    assert out["done"] is False
    assert out["errors"] == []


def test_normalize_state_preserves_user_supplied_keys():
    state = AgentState(
        topic="X", language="zh", top_k=10,
        sources=["arxiv"], years=(2020, 2024),
        output_path="r.md", verbose=True,
    )
    out = _normalize_state(state, _settings())
    assert out["language"] == "zh"
    assert out["top_k"] == 10
    assert out["sources"] == ["arxiv"]
    assert out["years"] == (2020, 2024)
    assert out["output_path"] == "r.md"
    assert out["verbose"] is True


def test_normalize_state_coerces_language_to_known_value():
    state = AgentState(topic="X", language="fr")
    out = _normalize_state(state, _settings())
    assert out["language"] == "en"


# --------------------------------------------------------------------------- #
# RunResult
# --------------------------------------------------------------------------- #


def test_run_result_exposes_state_helpers():
    state = AgentState(
        topic="x",
        merged=[Paper(title="X", year=2024), Paper(title="Y", year=2023)],
        sections={"background": "body", "methods": "x"},
        errors=["a", "b"],
    )
    rr = RunResult(state=state, output_path=Path("/tmp/x.md"))
    assert len(rr.papers) == 2
    assert rr.papers[0].title == "X"
    assert rr.sections["background"] == "body"
    assert rr.errors == ["a", "b"]


def test_run_result_handles_missing_keys():
    rr = RunResult(state=AgentState(topic="x"), output_path=Path("/tmp/x.md"))
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
# run() with a fake agent
# --------------------------------------------------------------------------- #


class _FakeAgent:
    """Stands in for ReviewAgent; writes a report and marks the run done."""

    def __init__(self, settings: Settings, state: AgentState) -> None:
        self.settings = settings
        self.state = state

    def run(self, on_node=None):
        self.state["sections"] = {
            "background": "background body",
            "methods": "methods body",
        }
        self.state["merged"] = self.state.get("papers", [])
        self.state["done"] = True
        self.state["step"] = 2
        self.state["tool_calls"] = 3
        self.state["reflections"] = [{"type": "search_coverage"}, {"type": "report_draft"}]
        self.state["source_counts"] = {"arxiv": 2}
        write_report(
            {
                "topic": self.state.get("topic", ""),
                "language": self.state.get("language", "en"),
                "sections": self.state["sections"],
                "merged": self.state["merged"],
                "output_path": self.state.get("output_path", "report.md"),
            },
            output_path=Path(self.state.get("output_path", "report.md")),
        )
        if on_node is not None:
            on_node("agent_step", {"step": 1})
            on_node("submit_report", {"sections": list(self.state["sections"])})
        return self.state


def test_run_with_fake_agent_writes_metrics(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lit_review.agent.ReviewAgent", _FakeAgent)
    papers = [
        Paper(title=f"Paper {i}", authors=["X"], year=2024, abstract="abs", source="arxiv")
        for i in range(5)
    ]
    state = AgentState(
        topic="x", language="en", top_k=5, papers=papers,
        output_path=str(tmp_path / "r.md"),
    )
    settings = Settings(llm_api_key="sk-fake", request_timeout=10.0)

    result = run(state, settings, emit_metrics=True)

    assert result.metrics is not None
    assert result.metrics.steps == 2
    assert result.metrics.tool_calls == 3
    assert result.metrics.reflections == 2
    assert result.metrics.sources == {"arxiv": 2}
    assert result.output_path.exists()
    assert (tmp_path / "r.md.metrics.json").exists()


def test_run_requires_llm(tmp_path: Path):
    state = AgentState(topic="x", output_path=str(tmp_path / "r.md"))
    with pytest.raises(ConfigurationError):
        run(state, Settings(llm_api_key="", request_timeout=10.0))
