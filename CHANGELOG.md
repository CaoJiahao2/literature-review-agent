# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.html).

## [Unreleased]

### Added
- 🔌 **Unified runner** — `lit_review.runner.run()` is now the single seam for
  both CLI and Gradio UI. The UI no longer imports the CLI's private helpers.
- ⚡ **Async source fan-out** — `tools.async_runner.run_sources_async()` runs
  every (source, query) pair concurrently with a bounded semaphore. arXiv,
  OpenAlex, Semantic Scholar, and Crossref each have an async implementation;
  Hugging Face keeps its sync loop (one HTTP per day is already optimal).
- 🤖 **LLM client facade** — `lit_review.llm_client.LLMClient` adds:
  * In-process LRU + optional disk caching (`LIT_REVIEW_CACHE_DIR`)
  * Exponential backoff retry on `Timeout`/`Connection`/`RateLimit`/`5xx`
  * Token usage accumulation; surfaced through `metrics.json`
  * Lenient JSON parsing via `invoke_json()` for structured-output workflows
- 📊 **Observability** — `lit_review.metrics.Metrics` + `timed_node` decorate
  every node so we know how long it ran, how many papers each source yielded,
  and how many tokens the LLM consumed. `--emit-metrics` writes
  `<report>.metrics.json`; `--emit-state` writes `<report>.state.json` for
  debugging.
- 🇨🇳 **Chinese documentation set** — `docs/zh/` ships ARCHITECTURE,
  DESIGN, STATE, SOURCES, RANKING, LLM, OBSERVABILITY, TROUBLESHOOTING, and
  DEVELOPMENT guides.
- 🧪 **New tests** (offline-only):
  * `tests/test_metrics.py` — `Metrics`, `timed_node`
  * `tests/test_llm_client.py` — cache, retry, JSON parse
  * `tests/test_async_runner.py` — registration, sync fallback, error collection
  * `tests/test_runner.py` — `RunResult`, `_normalize_state`
- 🛠️ **CLI** flags: `--emit-metrics`, `--emit-state`

### Changed
- Section synthesis now runs the five chapter calls **in parallel** via
  `asyncio.gather`, halving wall-clock on `gpt-4o-mini`-class models.
- `Tools/_http.get_async_client` was added to mirror the sync helper.
- The Gradio UI exposes the **metrics.json** as a separate download link.

### Fixed
- **Async fan-out now honors per-source limits** — `async_runner` used to cap
  every source at a hardcoded 10, ignoring `ARXIV_MAX_PER_QUERY` etc. from
  `.env`. It now reads `<source>_max_per_query` from `Settings` (dead
  `_default_cap`/`settings_default_cap` helpers removed).
- **State channels survive LangGraph** — `__node_times__`, `__dedupe_stats__`,
  and `__llm_client__` are now declared on `GraphState`, so they are no longer
  stripped between nodes. Per-node timings and dedupe stats actually reach
  `metrics.json`, and nodes share the runner's `LLMClient` (so token usage is
  counted).
- **`--verbose` streams real per-node progress** — the runner now executes the
  graph via `graph.stream()` and fires `on_node` for every node (previously it
  was a no-op that only fired once at the end).
- **`synthesize_sections_node` no longer breaks under a running event loop** —
  the old try/except both called `asyncio.run()`, which raises
  `RuntimeError` when a loop is already bound to the thread (Gradio/Jupyter).
  Synthesis now runs on a dedicated worker thread with its own loop.
- The offline graph smoke test no longer touches the network (it mocked the
  wrong seam, `run_sources` instead of `_run_sources`; the async path was
  hitting live endpoints).
- Duplicate `_strip_socks_proxies` definitions in `ui.py` (the unused one is
  gone).
- `ui.py` no longer reaches into `cli._do_run` private helpers.

### Documentation
- Renamed developer-facing docs to `docs/zh/`; the user-facing README.zh.md
  remains unchanged.
- New `docs/zh/RANKING.md` explains the score formula and tuning knobs.
- **Primary README is now 简体中文** — `README.md` is the Chinese user guide;
  the English guide moved to `README.en.md`. Cross-links in `README.md`,
  `README.en.md`, `docs/README.md`, and `docs/zh/README.md` updated to match.

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
- 35 offline tests + 6 network-marked tests (74 offline tests as of v0.2).
- MIT license, README (English + 简体中文), CONTRIBUTING, SECURITY, GitHub issue/PR templates.

[Unreleased]: https://github.com/CaoJiahao2/literature-review-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CaoJiahao2/literature-review-agent/releases/tag/v0.1.0
