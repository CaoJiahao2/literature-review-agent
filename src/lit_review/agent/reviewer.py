"""Single ReAct agent that produces a literature review end-to-end.

The loop is intentionally explicit (rather than relying on a prebuilt agent)
so memory, tool execution, reflection gating, and termination are all visible
and testable:

    system prompt + memory context
        -> model.bind_tools(TOOLS)
        -> parse AIMessage.tool_calls
        -> execute tool -> append ToolMessage
        -> repeat until submit_report or max_agent_steps

The two reflection tools (`review_search_coverage`, `review_report_draft`)
run nested LLM calls and return critiques the agent can act on.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ..config import ConfigurationError, Settings
from ..llm_client import LLMClient
from ..memory.store import inject_memory_context
from ..report.template import SECTIONS
from ..state import AgentState
from .tools import AgentRuntime, build_tools

log = logging.getLogger(__name__)


def _tc_field(tool_call: Any, key: str) -> Any:
    """Read a field from a LangChain tool call (dict or object shape)."""
    if isinstance(tool_call, dict):
        return tool_call.get(key)
    return getattr(tool_call, key, None)


class AgentRunError(RuntimeError):
    """Raised when the agent terminates without producing a report."""


class ReviewAgent:
    """One run of the literature-review ReAct agent."""

    def __init__(self, settings: Settings, state: AgentState) -> None:
        self.settings = settings
        self.state = state

    def run(self, on_node: Optional[Callable[[str, dict[str, Any]], None]] = None) -> AgentState:
        if not self.settings.has_llm():
            raise ConfigurationError(
                "LLM_API_KEY is not set. This agent requires any OpenAI-compatible "
                "endpoint; copy .env.example to .env and configure LLM_API_KEY."
            )

        self.state.setdefault("messages", [])
        self.state.setdefault("papers", [])
        self.state.setdefault("drafts", {})
        self.state.setdefault("reflections", [])
        self.state.setdefault("errors", [])
        self.state["tool_calls"] = int(self.state.get("tool_calls", 0))

        client = LLMClient(self.settings)
        runtime = AgentRuntime(
            settings=self.settings,
            state=self.state,
            client=client,
        )
        tools = build_tools(runtime)
        model = client.bind_tools(tools)
        if model is None:
            raise ConfigurationError("Failed to build the chat model with tool calling support.")

        tool_map = {t.name: t for t in tools}
        self.state["messages"] = [SystemMessage(content=self._system_prompt())]

        max_steps = int(self.settings.max_agent_steps)
        while int(self.state.get("step", 0)) < max_steps and not bool(self.state.get("done")):
            self.state["step"] = int(self.state.get("step", 0)) + 1
            step = int(self.state["step"])

            response = client.invoke_chat(model, self.state["messages"], tag=f"agent.step.{step}")
            if response is None:
                self.state.setdefault("errors", []).append(f"agent step {step}: LLM call failed")
                break

            self.state["messages"].append(response)

            tool_calls = list(getattr(response, "tool_calls", None) or [])
            if not tool_calls:
                # The model produced text without a tool call. Nudge it back into
                # the tool-calling contract; this also keeps the transcript honest.
                self.state["messages"].append(
                    HumanMessage(
                        content=(
                            "Continue by calling one of your tools. Search if coverage is "
                            "insufficient, review_search_coverage or review_report_draft to "
                            "reflect, list_papers to align citations, and submit_report when done."
                        )
                    )
                )
                if on_node is not None:
                    self._emit(on_node, "agent_step", {"step": step, "tool_calls": 0, "note": "no tool call"})
                continue

            for tc in tool_calls:
                name = str(_tc_field(tc, "name"))
                args = _tc_field(tc, "args") or {}
                tool = tool_map.get(name)
                tool_id = _tc_field(tc, "id") or f"call_{step}_{len(tool_calls)}"
                try:
                    result = tool.invoke(args) if tool is not None else f"Unknown tool: {name}"
                    content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                except Exception as exc:
                    log.warning("tool %s failed: %s", name, exc)
                    content = json.dumps({"error": str(exc)}, ensure_ascii=False)
                    self.state.setdefault("errors", []).append(f"{name}: {exc}")
                self.state["tool_calls"] = int(self.state.get("tool_calls", 0)) + 1
                self.state["messages"].append(ToolMessage(content=content, tool_call_id=str(tool_id)))

            if on_node is not None:
                self._emit(
                    on_node,
                    "agent_step",
                    {
                        "step": step,
                        "tool_calls": [str(t.get("name", "")) for t in tool_calls],
                        "papers": len(self.state.get("papers", []) or []),
                        "drafts": len(self.state.get("drafts", {}) or {}),
                    },
                )

        self.state["llm_usage"] = client.snapshot()

        if not bool(self.state.get("done")):
            self.state["max_steps_reached"] = True
            self.state.setdefault("errors", []).append(
                f"Agent reached max_agent_steps={max_steps} without calling submit_report."
            )
            raise AgentRunError(self.state["errors"][-1])

        if on_node is not None:
            self._emit(on_node, "submit_report", {"sections": list(self.state.get("sections", {}).keys())})

        return self.state

    # ------------------------------------------------------------------

    def _system_prompt(self) -> str:
        lang = str(self.state.get("language", "en")).lower().startswith("zh")
        lang_name = "Chinese (Simplified)" if lang else "English"
        sources = ", ".join(self.state.get("sources") or self.settings.enabled_sources())
        years = self.state.get("years")
        years_text = f"{years[0]}..{years[1]}" if years else "recent years"
        top_k = int(self.state.get("top_k", 30))

        resume_block = ""
        if bool(self.settings.resume_memory):
            _, resume_text = inject_memory_context(self.settings, self.state.get("topic", ""))
            if resume_text:
                resume_block = "Previous memory context:\n" + resume_text

        return f"""You are a literature-review agent. Produce a rigorous, grounded review on the topic below in {lang_name}.

Your available tools:
- search_arxiv / search_openalex / search_huggingface / search_semantic_scholar / search_crossref: search academic sources. Prefer several short, specific queries across sources; start with the core topic and then cover subfields, methods, datasets, and recent work.
- list_papers: show the current numbered reference list. Call it before drafting so inline [#] citations match the references actually written to disk.
- review_search_coverage: self-critique the search coverage; use its suggestions to run any missing searches.
- review_report_draft: self-critique the current section drafts (pass them via its optional `sections` argument); revise them using its notes before finalizing.
- submit_report: write the final report. The reference list is generated automatically from the papers you collected — never invent references.

Workflow:
1. Plan 3-6 search queries (you may store them mentally; no planning tool is required).
2. Search the sources in parallel/sequence until you have enough coverage (aim for at least {top_k} unique, relevant papers).
3. Call review_search_coverage, act on missing angles with additional searches if needed.
4. Call list_papers, then draft the five sections and pass them to review_report_draft as its `sections` argument.
5. Revise the drafts based on the review_report_draft feedback, then call submit_report with the final section bodies.
6. Call submit_report with the final five section bodies. Cite papers inline as [#] using the numbered list from list_papers. Do not invent facts or citations.

Constraints:
- Topic: {self.state.get("topic", "")}
- Publication years: {years_text}
- Enabled sources: {sources}
- Target papers kept: {top_k}
- Use the five section keys exactly: background, methods, datasets, trends, open_problems.
- Be honest about gaps; never fabricate a reference. A paper must appear in list_papers before you cite it.
{resume_block}
""".strip()

    @staticmethod
    def _emit(on_node: Callable[[str, dict[str, Any]], None], name: str, payload: dict[str, Any]) -> None:
        try:
            on_node(name, payload)
        except Exception:  # pragma: no cover
            log.warning("on_node callback failed for %s", name, exc_info=True)
