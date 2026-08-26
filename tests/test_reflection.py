"""Tests for the two explicit self-reflection tools."""

from __future__ import annotations

import json

from lit_review.agent.tools import AgentRuntime, build_tools
from lit_review.config import Settings
from lit_review.state import AgentState


class _FakeClient:
    def invoke_json(self, *, system, user, tag, temperature=0.2, fallback=None):
        if tag == "reflection.search_coverage":
            return {
                "coverage_gaps": ["missing benchmark papers"],
                "suggested_queries": ["rag benchmark"],
                "verdict": "insufficient",
            }
        if tag == "reflection.report_draft":
            return {
                "per_section": {"background": "cite specific papers"},
                "overall": "draft is too vague",
            }
        return {}


def _tools(state: AgentState, settings: Settings):
    runtime = AgentRuntime(settings=settings, state=state, client=_FakeClient())
    tools = build_tools(runtime)
    return {t.name: t for t in tools}, runtime


def test_search_coverage_reflection_returns_suggestions():
    settings = Settings(llm_api_key="", max_reflections=1)
    state = AgentState(topic="RAG", papers=[])
    tools, runtime = _tools(state, settings)

    result = tools["review_search_coverage"].invoke({})
    data = json.loads(result)
    assert data["verdict"] == "insufficient"
    assert "rag benchmark" in data["suggested_queries"]
    assert len(state["reflections"]) == 1
    assert state["reflections"][0]["type"] == "search_coverage"


def test_report_draft_reflection_returns_revision_notes():
    settings = Settings(llm_api_key="", max_reflections=1)
    state = AgentState(
        topic="RAG",
        drafts={"background": "vague text", "methods": "m"},
    )
    tools, _ = _tools(state, settings)

    result = tools["review_report_draft"].invoke({})
    data = json.loads(result)
    assert data["overall"] == "draft is too vague"
    assert data["per_section"]["background"] == "cite specific papers"
    assert state["reflections"][-1]["type"] == "report_draft"


def test_reflection_tools_respect_limit():
    settings = Settings(llm_api_key="", max_reflections=1)
    state = AgentState(topic="RAG", drafts={"background": "x"})
    tools, _ = _tools(state, settings)

    tools["review_report_draft"].invoke({})
    second = json.loads(tools["review_report_draft"].invoke({}))
    assert second["status"] == "limit_reached"
    assert len(state["reflections"]) == 1
