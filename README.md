# 📚 Literature Review Agent（文献调研 Agent）

> 一个**单一 ReAct Agent**：根据研究课题自主规划检索、调用多源学术检索工具、自我反思覆盖度与草稿质量，最终生成结构化 Markdown 综述报告。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![ReAct Agent](https://img.shields.io/badge/architecture-ReAct%20Agent-8b5cf6)]()
[![Sources: 5](https://img.shields.io/badge/sources-arXiv%20%7C%20OpenAlex%20%7C%20HF%20%7C%20S2%20%7C%20Crossref-orange)]()

[English](./README.en.md) | **简体中文**

📘 **架构与设计文档**（中文）位于 [`docs/zh/`](./docs/zh/)。

---

## ✨ 功能特性

- 🤖 **单一 ReAct Agent** — LLM 通过原生 function calling 自主完成「规划 → 检索 → 反思 → 撰写 → 修订 → 提交」全流程，不再依赖预定义的确定性流水线。
- 🔍 **多源工具调用** — 将 arXiv、OpenAlex、Hugging Face Daily Papers、Semantic Scholar、Crossref 五大数据库包装为 5 个原生 function-calling 工具，Agent 可自主决定何时、以何种查询调用。
- 🧠 **自我反思（两段式）** — `review_search_coverage` 批判检索覆盖度并给出补搜建议；`review_report_draft` 逐节批判草稿并给出修订意见。Agent 根据反馈补搜 / 修订后再提交。
- 🧩 **运行内工作记忆** — LLM 消息转录、已收集论文、章节草稿、反思记录都驻留在 `AgentState` 中；论文按 DOI / arXiv ID / 标题去重。
- 💾 **可选跨轮持久化** — 每个课题在 `~/.lit_review/memory/` 存一份 JSON 快照，`--resume` 可加载历史章节与论文清单作为上下文继续调研。
- 🎯 **引用防幻觉** — `submit_report` 只接收章节正文，参考文献由工具从真实检索到的论文语料自动生成，杜绝 LLM 编造引用。
- 🌐 **中英双语** — 自动检测 CJK 字符判定语言，也支持 `--language en|zh` 强制指定。
- 📊 **可解释的排名** — 引用数 + 时新度 + 摘要丰富度三维评分，跨源去重按 DOI / arXiv ID / 标准化标题匹配。
- 🖥️ **CLI + Web UI** — `lit-review review` 命令行一行产出；`lit-review ui` 启动 Gradio 浏览器界面。
- 🤖 **LLM 客户端封装** — 透明缓存（`LIT_REVIEW_CACHE_DIR`）、重试 / 退避、token 用量统计。
- 📊 **内建可观测性** — `--emit-metrics` 写出步数、工具调用数、反思轮数、各源命中数与 LLM 用量；`--emit-state` 写出调试用状态快照。

## 📦 安装

```bash
git clone https://github.com/CaoJiahao2/literature-review-agent.git
cd literature-review-agent
pip install -e ".[dev,ui]"
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（任何 OpenAI 兼容端点都可以；Agent 无 LLM 时不会运行）
```

## 🚀 快速开始

```bash
# 1. 命令行：检索 + 综述
lit-review review "RAG with knowledge graphs" --output reports/rag.md

# 2. 中文课题（自动识别）
lit-review review "扩散模型在图像生成中的进展" --language zh --output reports/diffusion.md

# 3. 复用该课题的历史记忆
lit-review review "RAG with knowledge graphs" --resume

# 4. 启动 Web UI
lit-review ui
# 浏览器访问 http://127.0.0.1:7860

# 5. 查看配置
lit-review config

# 6. 启用可观测性（会写 `<report>.metrics.json` 与 `<report>.state.json`）
lit-review review "RAG with knowledge graphs" --emit-metrics --emit-state
```

> ⚠️ `LLM_API_KEY` 未配置时，程序会直接报错并提示配置，不再降级为骨架报告。

## 🗂️ 架构

```
[构建系统提示 + 记忆上下文]
            │
            ▼
   模型.bind_tools(TOOLS) ◄─────────────┐
            │                          │
            ▼                          │
    解析 AIMessage.tool_calls           │ ToolMessage
            │                          │
     ┌──────┴──────────────┐           │
     ▼                     ▼           │
 search_* × 5        review_search_     │
     │                coverage          │
     ▼                     │            │
  record_papers      review_report_     │
     │                 draft            │
     ▼                     │            │
 list_papers ──► submit_report ─────────┘
```

核心循环是一个**手写 ReAct 循环**：模型每次调用返回 `tool_calls`，Agent 执行对应工具并把结果作为 `ToolMessage` 追加回消息历史，直到调用 `submit_report` 或达到 `max_agent_steps`。

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
# LLM（任何 OpenAI 兼容端点，必填）
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# Agent 循环
MAX_AGENT_STEPS=12      # 最大 LLM/工具步数
MAX_REFLECTIONS=1       # 两种反思各最多轮数

# 跨轮记忆
MEMORY_DIR=~/.lit_review/memory
RESUME_MEMORY=false

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
USER_AGENT=LiteratureReviewAgent/0.2 (mailto:agent@example.com)
```

## 🛠️ CLI 参考

```bash
lit-review review "topic" [选项]
  --output, -o PATH      输出 Markdown 路径（默认 report.md）
  --language, -l en|zh   报告语言（默认按 CJK 自动检测）
  --top-k INT            保留前 K 篇（默认 30）
  --years YYYY..YYYY     年份范围（默认近 5 年）
  --sources, -s LIST     逗号分隔的源列表
  --resume               加载该课题历史记忆
  --verbose, -v          打印每个 Agent 步的进度

lit-review ui [--host H] [--port N] [--share]
lit-review config
```

## 🧪 开发

```bash
pip install -e ".[dev]"
pytest -q -m "not network"      # 离线测试
pytest -q                       # 含网络标记测试
```

## 🐛 故障排查

- **启动即报 `LLM_API_KEY is not set`** — 这是预期行为：ReAct Agent 没有骨架降级路径，请在 `.env` 中配置 `LLM_API_KEY`。
- **OpenAlex 一直 429** — 在 OpenAlex 注册邮箱并设置 `mailto=...` 到 `USER_AGENT`，或加 `S2_API_KEY` 增强其它源。
- **`langchain-openai` 报 `Unknown scheme for proxy URL`** — 我们已禁用环境代理；如需使用 HTTP 代理，请设置 `HTTPS_PROXY=http://...`。
- **Gradio UI 起不来** — 运行 `pip install -e ".[ui]"`，确认 `gradio` 已安装。

## 📄 许可证

本项目使用 [MIT License](./LICENSE)。
