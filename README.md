# 📚 Literature Review Agent

> A LangGraph-based agent that turns an AI research topic into a comprehensive Markdown literature review — pulled from 5 academic sources, deduplicated, ranked, and written by an LLM.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![LangGraph](https://img.shields.io/badge/orchestration-LangGraph-1f6feb)](https://github.com/langchain-ai/langgraph)
[![Sources: 5](https://img.shields.io/badge/sources-arXiv%20%7C%20OpenAlex%20%7C%20HF%20%7C%20S2%20%7C%20Crossref-orange)]()

**English** | [简体中文](./README.zh.md)

---

## ✨ Features

- 🔍 **Multi-source search** — concurrent queries to arXiv, OpenAlex, Hugging Face Daily Papers, Semantic Scholar, and Crossref.
- 🧠 **LLM-driven planning + synthesis** — an LLM proposes search queries and writes each section. Graceful skeleton-only fallback when no key is set.
- 🕸️ **LangGraph StateGraph** — explicit nodes for plan → parallel search → dedupe/rank → conditional refine → section synthesis → assembly.
- 🌐 **English + Chinese** — auto-detects CJK characters; `--language en|zh` to override.
- 📊 **Explainable ranking** — `0.5·citation + 0.3·recency + 0.2·abstract_richness`; cross-source dedupe by DOI / arXiv ID / normalized title (union).
- 🖥️ **CLI + Web UI** — `lit-review review "topic"` for one-shot; `lit-review ui` for a Gradio dashboard with source checkboxes.

## 📦 Installation

```bash
git clone https://github.com/CaoJiahao2/literature-review-agent.git
cd literature-review-agent
pip install -e ".[dev,ui]"
cp .env.example .env
# edit .env and set LLM_API_KEY (any OpenAI-compatible endpoint)
```

## 🚀 Quick start

```bash
# 1. CLI: search + synthesize
lit-review review "RAG with knowledge graphs" --output reports/rag.md

# 2. Chinese topic (auto-detected)
lit-review review "扩散模型在图像生成中的进展" --language zh --output reports/diffusion.md

# 3. Skeleton-only (no LLM)
lit-review review "RLHF" --no-llm --top-k 20 --years 2020..2025

# 4. Web UI
lit-review ui
# then open http://127.0.0.1:7860

# 5. Show resolved config
lit-review config
```

## 🗂️ Architecture

```
[START] → plan → search_sources → dedupe_rank → filter_top_k → synthesize_sections → assemble → [END]
                                       ↑              │
                                       └── refine_search ┘ (loop, max 2)
```

| Node | Purpose |
|---|---|
| `plan` | LLM proposes 4–6 keyword queries (deterministic fallback) |
| `search_sources` | Run every enabled source against the queries |
| `dedupe_rank` | Cluster by DOI / arXiv ID / title-hash union; score & sort |
| `filter_top_k` | Keep the top K |
| `should_refine` | Conditional edge: if corpus is thin and `iteration < max_iter`, refine |
| `refine_search` | LLM proposes new queries; search again |
| `synthesize_sections` | Write each of 6 sections (background → open problems) |
| `assemble` | Render Markdown and write to disk |

## 📚 Sources

| Source | Free? | API key | What it gives you |
|---|---|---|---|
| **arXiv** | ✅ | — | Preprints (AI-heavy), full abstracts |
| **OpenAlex** | ✅ | — | Broad coverage, citation counts, venues |
| **Hugging Face Daily Papers** | ✅ | — | Curated trending AI papers (last 7 days), TLDRs |
| **Semantic Scholar** | ✅ | optional `S2_API_KEY` | Rich metadata; rate-limited without key |
| **Crossref** | ✅ | — | DOI / citation metadata across all disciplines |

Default in `.env`: `arxiv,openalex,huggingface` (the three no-key sources with best AI coverage).

## ⚙️ Configuration (`.env`)

```bash
# LLM (any OpenAI-compatible endpoint)
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# Per-source per-query caps
ARXIV_MAX_PER_QUERY=15
OPENALEX_MAX_PER_QUERY=15
HUGGINGFACE_MAX_PER_QUERY=20
SEMANTIC_SCHOLAR_MAX_PER_QUERY=10
CROSSREF_MAX_PER_QUERY=10

# HuggingFace trending-papers lookback
HUGGINGFACE_LOOKBACK_DAYS=7

# Default sources (comma-separated)
DEFAULT_SOURCES=arxiv,openalex,huggingface

# Semantic Scholar optional key: https://www.semanticscholar.org/product/api
S2_API_KEY=

# HTTP
REQUEST_TIMEOUT=30
USER_AGENT=LiteratureReviewAgent/0.1 (mailto:agent@example.com)
```

## 🛠️ CLI reference

```bash
lit-review review "topic" [options]
  --output, -o PATH      Markdown output path (default report.md)
  --language, -l en|zh   Report language (default auto-detect)
  --top-k INT            Papers to keep after ranking (default 30)
  --years YYYY..YYYY     Year range (default last 5 years)
  --max-iter INT         Max search-refinement iterations (default 2)
  --sources, -s LIST     Comma-separated sources to query
  --no-llm               Force skeleton-only output
  --verbose, -v          Stream node-by-node progress

lit-review ui [--host H] [--port N] [--share]
lit-review config
```

## 🧪 Development

```bash
pip install -e ".[dev]"
pytest -q                       # all tests (offline + network-marked)
pytest -q -m "not network"      # offline only
```

Test layout:

- **35 offline tests** — state, templates, rank/dedupe, CLI, graph, UI assembly (~0.7s)
- **6 network tests** — live arXiv / OpenAlex / Crossref / HF / S2 calls; CI should skip with `-m "not network"`

## 🐛 Troubleshooting

- **OpenAlex 429** — register a contact email and put it in `USER_AGENT` (`mailto=...`), or set `S2_API_KEY` to lean on other sources.
- **`Unknown scheme for proxy URL (socks://…)`** — we disable env proxies by default. To use an HTTP proxy, set `HTTPS_PROXY=http://proxy:port`.
- **Gradio UI won't start** — `pip install -e ".[ui]"` (the base install doesn't pull gradio).

## 🤝 Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). Adding a new source is straightforward — implement the `(settings, queries, *, max_per_query, years) -> list[Paper]` interface, register it in `tools/__init__.py`, and add a test.

## 📄 License

[MIT](./LICENSE) — see the file for the full text.
