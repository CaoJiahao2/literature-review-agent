# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- 35 offline tests + 6 network-marked tests.
- MIT license, README (English + 简体中文), CONTRIBUTING, SECURITY, GitHub issue/PR templates.

[Unreleased]: https://github.com/CaoJiahao2/literature-review-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CaoJiahao2/literature-review-agent/releases/tag/v0.1.0
