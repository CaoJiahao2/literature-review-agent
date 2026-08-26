# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.html).

## [Unreleased]

## [0.2.0] — 2026-08-26

### Added
- 🤖 **Single ReAct Agent** — the deterministic LangGraph pipeline
  (plan → search → dedupe → refine → synthesize) is replaced by one ReAct
  agent (`lit_review.agent.ReviewAgent`) that drives the whole flow via native
  function calling (`bind_tools`). The hand-written loop parses
  `AIMessage.tool_calls`, executes tools, and appends `ToolMessage` results back
  into the running message memory.
- 🧰 **Native function-calling tools** (`lit_review.agent.tools`) — the five
  data sources are wrapped as `search_arxiv` / `search_openalex` /
  `search_huggingface` / `search_semantic_scholar` / `search_crossref`, plus
  `list_papers`, `submit_report`, and two explicit self-reflection tools.
- 🪞 **Self-reflection** — `review_search_coverage` critiques the current query
  coverage and suggests follow-up searches; `review_report_draft` critiques the
  current section drafts with per-section revision notes. Both use nested LLM
  calls through `LLMClient.invoke_json`.
- 🧠 **Working memory + cross-run persistence** (`lit_review.memory`) — the
  message transcript, collected papers, drafts, and reflections live in
  `AgentState` during a run; on completion a per-topic JSON snapshot is saved
  under `MEMORY_DIR`. `--resume` / `resume_memory` injects prior sections and
  paper list back into the system prompt.
- 🛡️ **Hallucination-resistant references** — `submit_report` only accepts
  section bodies; the reference list is generated from the *actually collected*
  corpus via `merge_and_rank`, so the model cannot invent citations.
- 🧪 **New offline tests** — `tests/test_agent.py` (ReAct loop, tool execution,
  message memory, `max_agent_steps`), `tests/test_tools.py` (respx-mocked search
  tools), `tests/test_reflection.py` (both reflection tools), and
  `tests/test_memory.py` (snapshot roundtrip + resume injection).
- 📊 **Expanded metrics** — `steps`, `tool_calls`, `reflections`, and
  `max_steps_reached` are now emitted alongside source / LLM / section stats.

### Changed
- **No more deterministic fallback** — `--no-llm` and the skeleton-body path are
  removed. `require_llm()` raises `ConfigurationError` when `LLM_API_KEY` is
  missing (CLI exit code 2), and endpoints without `tool_calls` support fail
  fast instead of degrading to JSON mode.
- **`runner.run` keeps its signature** but now delegates to `ReviewAgent`;
  `RunResult.state` carries the new `AgentState` instead of `GraphState`.
- **`LLMClient`** gains `bind_tools()` and `invoke_chat()` for the agent loop
  (intentionally uncached), while `invoke_json` keeps its cache / retry /
  fallback behavior for reflection sub-calls.
- **CLI** drops `--max-iter`, adds `--resume`; the Gradio UI replaces the
  skeleton checkbox with a “Resume prior memory” switch.

### Removed
- `src/lit_review/graph/` (builder, nodes, edges) and `tests/test_graph_smoke.py`.
- `langgraph` runtime dependency and the `skeleton_body` report path.

## [0.1.0] — 2026-08-16

### Added
- Initial release.
- LangGraph-based agent: plan → search → dedupe → filter → (refine) → synthesize → assemble.
- Five literature sources: arXiv, OpenAlex, Hugging Face Daily Papers, Semantic Scholar, Crossref.
- Cross-source dedupe by DOI / arXiv ID / normalized-title union.
- Source-agnostic scoring: `0.5·citation + 0.3·recency + 0.2·abstract_richness`.
- Pluggable OpenAI-compatible LLM backend; graceful skeleton-only fallback when no key set.
- English + Chinese report generation (auto-detect CJK).
- typer CLI (`lit-review review`, `lit-review config`) and Gradio Web UI (`lit-review ui`).
- MIT license, README (English + 简体中文), CONTRIBUTING, SECURITY, GitHub issue/PR templates.

[Unreleased]: https://github.com/CaoJiahao2/literature-review-agent/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/CaoJiahao2/literature-review-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/CaoJiahao2/literature-review-agent/releases/tag/v0.1.0
