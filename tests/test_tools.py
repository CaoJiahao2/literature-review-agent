"""Tests for the LangChain tools exposed to the ReAct agent."""

from __future__ import annotations

import json
from pathlib import Path

from lit_review.agent.tools import AgentRuntime, build_tools
from lit_review.config import Settings
from lit_review.llm_client import LLMClient
from lit_review.state import AgentState, Paper


def _settings() -> Settings:
    return Settings(llm_api_key="", request_timeout=10.0)


def _paper(title="Attention Paper", year=2023, source="arxiv") -> Paper:
    return Paper(title=title, authors=["A"], year=year, abstract="an abstract", source=source, arxiv_id="2401.00001")


def _fake_source(paper: Paper):
    def _fn(settings, queries, *, max_per_query=None, years=None):
        return [paper]
    return _fn


def _build(monkeypatch, state: AgentState, paper: Paper):
    import lit_review.agent.tools as tmod

    fake = _fake_source(paper)
    for name in (
        "search_arxiv",
        "search_openalex",
        "search_huggingface",
        "search_semantic_scholar",
        "search_crossref",
    ):
        monkeypatch.setattr(tmod, name, fake)

    runtime = AgentRuntime(settings=_settings(), state=state, client=LLMClient(_settings()))
    tools = build_tools(runtime)
    return {t.name: t for t in tools}, runtime


def test_search_tool_records_papers(monkeypatch, tmp_path: Path):
    state = AgentState(topic="RAG", top_k=5, output_path=str(tmp_path / "r.md"))
    tools, runtime = _build(monkeypatch, state, _paper())

    result = tools["search_arxiv"].invoke({"query": "retrieval augmented generation", "max_results": 5})
    data = json.loads(result)

    assert data["source"] == "arxiv"
    assert data["count"] == 1
    assert data["added_to_memory"] == 1
    assert data["papers"][0]["title"] == "Attention Paper"
    assert len(runtime.current_papers()) == 1


def test_list_papers_returns_numbered_references(monkeypatch, tmp_path: Path):
    state = AgentState(topic="RAG", top_k=5, output_path=str(tmp_path / "r.md"))
    tools, _ = _build(monkeypatch, state, _paper())
    tools["search_arxiv"].invoke({"query": "attention"})

    data = json.loads(tools["list_papers"].invoke({}))
    assert data["count"] == 1
    assert data["references"][0]["number"] == 1
    assert data["references"][0]["title"] == "Attention Paper"


def test_submit_report_uses_real_papers_not_llm_references(monkeypatch, tmp_path: Path):
    out = tmp_path / "r.md"
    state = AgentState(topic="RAG", top_k=5, output_path=str(out))
    tools, _ = _build(monkeypatch, state, _paper("Real Paper Title"))
    tools["search_arxiv"].invoke({"query": "rag"})

    result = tools["submit_report"].invoke(
        {"sections": {"background": "A fabricated citation [99] Fake Paper."}}
    )
    data = json.loads(result)

    assert data["status"] == "written"
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    # The references section is generated from the real corpus; the fake paper
    # the LLM hallucinated must not appear as a reference entry.
    _body, _sep, refs = text.partition("## References")
    assert "Real Paper Title" in refs
    assert "Fake Paper" not in refs
    assert state["done"] is True
    assert len(state["merged"]) == 1
