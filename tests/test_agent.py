"""Tests for the single ReAct agent loop."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from lit_review.agent.reviewer import AgentRunError, ReviewAgent
from lit_review.config import ConfigurationError, Settings
from lit_review.state import AgentState


def _settings(**kw) -> Settings:
    base = dict(llm_api_key="sk-fake", request_timeout=10.0, max_agent_steps=3)
    base.update(kw)
    return Settings(**base)


class _FakeClient:
    """Replaces LLMClient inside ReviewAgent; returns scripted AIMessages."""

    def __init__(self, settings: Settings, responses: list[AIMessage]) -> None:
        self.settings = settings
        self.responses = list(responses)
        self.index = 0

    def bind_tools(self, tools):
        return self  # the "bound model"

    def invoke_chat(self, model, messages, *, tag, max_attempts=3):
        if self.index >= len(self.responses):
            return AIMessage(content="")
        resp = self.responses[self.index]
        self.index += 1
        return resp

    def invoke_json(self, *, system, user, tag, temperature=0.2, fallback=None):
        return {"coverage_gaps": [], "suggested_queries": [], "verdict": "sufficient"}

    def snapshot(self):
        return {"calls": 0, "cache_hits": 0, "tokens_in": 0, "tokens_out": 0, "by_tag": {}, "errors": []}


def _submit_call(sections: dict[str, str] | None = None) -> dict:
    return {
        "name": "submit_report",
        "args": {"sections": sections or {"background": "body", "methods": "m", "datasets": "d", "trends": "t", "open_problems": "o"}},
        "id": "call_submit_1",
    }


def test_agent_submits_report_and_writes_file(tmp_path: Path, monkeypatch):
    out = tmp_path / "report.md"
    resp = AIMessage(content="", tool_calls=[_submit_call()])
    monkeypatch.setattr("lit_review.agent.reviewer.LLMClient", lambda settings: _FakeClient(settings, [resp]))

    state = AgentState(topic="RAG", language="en", top_k=3, output_path=str(out))
    agent = ReviewAgent(_settings(), state)
    final = agent.run()

    assert final["done"] is True
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "# Literature Review: RAG" in text
    assert "## References" in text


def test_agent_appends_tool_messages_to_memory(tmp_path: Path, monkeypatch):
    out = tmp_path / "report.md"
    list_call = {"name": "list_papers", "args": {}, "id": "call_list_1"}
    resp = AIMessage(content="", tool_calls=[list_call, _submit_call()])
    monkeypatch.setattr("lit_review.agent.reviewer.LLMClient", lambda settings: _FakeClient(settings, [resp]))

    state = AgentState(topic="RAG", language="en", top_k=3, output_path=str(out))
    ReviewAgent(_settings(), state).run()

    kinds = [type(m) for m in state["messages"]]
    assert SystemMessage in kinds
    assert AIMessage in kinds
    assert ToolMessage in kinds
    contents = [getattr(m, "content", "") for m in state["messages"] if isinstance(m, ToolMessage)]
    assert any('"count"' in c for c in contents)
    assert state["tool_calls"] == 2


def test_agent_max_steps_raises(monkeypatch):
    monkeypatch.setattr(
        "lit_review.agent.reviewer.LLMClient",
        lambda settings: _FakeClient(settings, [AIMessage(content="thinking only")]),
    )
    state = AgentState(topic="RAG", language="en", output_path="report.md")
    agent = ReviewAgent(_settings(max_agent_steps=2), state)

    with pytest.raises(AgentRunError):
        agent.run()

    assert state["max_steps_reached"] is True
    assert state["step"] == 2


def test_agent_requires_llm_key():
    agent = ReviewAgent(Settings(llm_api_key=""), AgentState(topic="x"))
    with pytest.raises(ConfigurationError):
        agent.run()
