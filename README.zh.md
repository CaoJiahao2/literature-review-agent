# 📚 Literature Review Agent（文献调研 Agent）

> 一个基于 LangGraph 的 AI 文献调研 Agent：根据研究课题自动检索多源学术数据库、生成结构化 Markdown 综述报告。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![LangGraph](https://img.shields.io/badge/orchestration-LangGraph-1f6feb)](https://github.com/langchain-ai/langgraph)
[![Sources: 5](https://img.shields.io/badge/sources-arXiv%20%7C%20OpenAlex%20%7C%20HF%20%7C%20S2%20%7C%20Crossref-orange)]()

[English](./README.md) | **简体中文**

---

## ✨ 功能特性

- 🔍 **多源检索** — 同时从 arXiv、OpenAlex、Hugging Face Daily Papers、Semantic Scholar、Crossref 五大数据库并发检索 AI 文献。
- 🧠 **LLM 驱动规划** — 用大语言模型生成关键词查询、撰写综述正文。如果未配置 LLM，会自动降级为确定性的骨架报告。
- 🕸️ **LangGraph 状态图** — 显式 StateGraph，包含查询规划 → 并行检索 → 去重排序 → 条件式细化 → 章节合成 → 报告拼装的完整流程。
- 🌐 **中英双语** — 自动检测 CJK 字符判定语言，也支持 `--language en|zh` 强制指定。
- 📊 **可解释的排名** — 引用数 + 时新度 + 摘要丰富度三维评分，跨源去重按 DOI / arXiv ID / 标准化标题匹配。
- 🖥️ **CLI + Web UI** — `lit-review review` 命令行一行产出；`lit-review ui` 启动 Gradio 浏览器界面可勾选数据源。

## 📦 安装

```bash
git clone https://github.com/<your-org>/literature-review-agent.git
cd literature-review-agent
pip install -e ".[dev,ui]"
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（任何 OpenAI 兼容端点都可以）
```

## 🚀 快速开始

```bash
# 1. 命令行：检索 + 综述
lit-review review "RAG with knowledge graphs" --output reports/rag.md

# 2. 中文课题（自动识别）
lit-review review "扩散模型在图像生成中的进展" --language zh --output reports/diffusion.md

# 3. 不要 LLM（纯骨架报告）
lit-review review "RLHF" --no-llm --top-k 20 --years 2020..2025

# 4. 启动 Web UI
lit-review ui
# 浏览器访问 http://127.0.0.1:7860

# 5. 查看配置
lit-review config
```

## 🗂️ 架构

```
[START] → plan → search_sources → dedupe_rank → filter_top_k → synthesize_sections → assemble → [END]
                                       ↑              │
                                       └── refine_search ┘ (loop, max 2)
```

节点详情：

| 节点 | 作用 |
|---|---|
| `plan` | 让 LLM（或回退模板）生成 4–6 个关键词查询 |
| `search_sources` | 在每个已启用的数据源上并发搜索 |
| `dedupe_rank` | 按 DOI / arXiv ID / 标题三元并集去重，按 0.5·引用 + 0.3·时新度 + 0.2·摘要丰富度排名 |
| `filter_top_k` | 保留前 K 篇 |
| `should_refine` | 条件边：若语料不足且未达迭代上限，转入细化 |
| `refine_search` | 让 LLM 生成新查询，再搜一遍 |
| `synthesize_sections` | 按"背景 / 方法 / 数据集 / 趋势 / 开放问题"逐章调用 LLM 撰写 |
| `assemble` | 拼接为 Markdown 报告并落盘 |

## 📚 数据源

| 数据源 | 是否免费 | 是否需要 Key | 特点 |
|---|---|---|---|
| **arXiv** | ✅ | ❌ | 预印本，AI 文献密集，含完整摘要 |
| **OpenAlex** | ✅ | ❌ | 跨学科覆盖，含引用数与会议期刊 |
| **Hugging Face Daily Papers** | ✅ | ❌ | 社区策展的近 14 天 AI 热门，含 AI 摘要 |
| **Semantic Scholar** | ✅ | 可选 `S2_API_KEY` | 丰富的元数据与 TLDR；无 key 会被限流 |
| **Crossref** | ✅ | ❌ | DOI / 引用元数据，跨学科 |

默认启用（`DEFAULT_SOURCES` in `.env`）：`arxiv,openalex,huggingface`。

## ⚙️ 配置（`.env`）

```bash
# LLM（任何 OpenAI 兼容端点）
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# 各源单查询上限
ARXIV_MAX_PER_QUERY=15
OPENALEX_MAX_PER_QUERY=15
HUGGINGFACE_MAX_PER_QUERY=20
SEMANTIC_SCHOLAR_MAX_PER_QUERY=10
CROSSREF_MAX_PER_QUERY=10

# HuggingFace 回溯天数
HUGGINGFACE_LOOKBACK_DAYS=7

# 默认启用的源（逗号分隔）
DEFAULT_SOURCES=arxiv,openalex,huggingface

# Semantic Scholar 可选 Key：https://www.semanticscholar.org/product/api
S2_API_KEY=

# 网络
REQUEST_TIMEOUT=30
USER_AGENT=LiteratureReviewAgent/0.1 (mailto:agent@example.com)
```

## 🛠️ CLI 参考

```bash
lit-review review "topic" [选项]
  --output, -o PATH      输出 Markdown 路径（默认 report.md）
  --language, -l en|zh   报告语言（默认按 CJK 自动检测）
  --top-k INT            保留前 K 篇（默认 30）
  --years YYYY..YYYY     年份范围（默认近 5 年）
  --max-iter INT         细化搜索最大轮数（默认 2）
  --sources, -s LIST     逗号分隔的源列表
  --no-llm               强制骨架模式
  --verbose, -v          打印每个节点的进度

lit-review ui [--host H] [--port N] [--share]
lit-review config
```

## 🧪 开发

```bash
pip install -e ".[dev]"
pytest -q                       # 离线 + 网络标记测试
pytest -q -m "not network"      # 仅离线
```

测试组成：

- **离线（35 个）** — 状态、模板、排序、CLI、UI 装配，毫秒级
- **网络标记（6 个）** — arXiv / OpenAlex / Crossref / HuggingFace / Semantic Scholar 真实端到端

## 🐛 故障排查

- **OpenAlex 一直 429** — 在 OpenAlex 注册邮箱并设置 `mailto=...` 到 `USER_AGENT`，或加 `S2_API_KEY` 增强其它源。
- **`langchain-openai` 报 `Unknown scheme for proxy URL`** — 我们已禁用环境代理；如需使用 HTTP 代理，请设置 `HTTPS_PROXY=http://...`。
- **Gradio UI 起不来** — 运行 `pip install -e ".[ui]"`，确认 `gradio` 已安装。

## 📄 许可证

本项目使用 [MIT License](./LICENSE)。
