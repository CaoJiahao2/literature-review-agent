# 状态与数据结构参考

> 所有跨 Agent / 工具传递的数据结构都列在这里。改字段请同时更新本文件。

## 1. `Paper`（核心数据单元）

文件：`src/lit_review/state.py`

```python
class Paper(BaseModel):
    title: str = ""
    authors: list[str] = []
    year: Optional[int] = None
    abstract: str = ""
    venue: str = ""
    url: str = ""
    doi: str = ""
    arxiv_id: str = ""
    categories: list[str] = []
    citation_count: Optional[int] = None
    source: str = ""               # "arxiv" | "openalex" | "huggingface" | "semantic_scholar" | "crossref" | "merged"
    extra: dict[str, Any] = {}

    # 由 merge_and_rank 填充：
    dedupe_key: str = ""
    score: float = 0.0
```

### 字段语义

| 字段 | 来源 / 用途 |
|---|---|
| `title` | 必须非空，否则该 Paper 被丢弃 |
| `authors` | 显示时截前 3 + "et al." |
| `year` | 不在年份范围时被丢弃 |
| `abstract` | 排名公式中的 `abstract_richness` 来源 |
| `venue` | 期刊 / 会议名 |
| `url` | 当 doi / arxiv_id 都缺失时显示这个；优先选择非 OpenAlex 链接 |
| `doi` | 跨源去重的最强键（但 arxiv-only 论文无 DOI） |
| `arxiv_id` | 跨源去重的第二强键 |
| `categories` | arXiv 的 primary categories |
| `citation_count` | openalex/s2/crossref 有；arxiv 没有（None） |
| `source` | 标记来源，便于展示时回溯 |

### 派生方法

- **`short_id()`** → 用于去重的标准 key：`doi:<lower>` / `arxiv:<lower>` / `title:<normalized>`。
- **`canonical_url()`** → 优先 DOI / arXiv canonical URL；若只剩 openalex 内部 ID 则返回空串。
- **`display_ref(n)`** → 一行参考文献：`[n] author1, author2, author3 et al. (year). title. venue _source_ [url](url)`。

## 2. `SearchPlan`

保留的轻量计划结构（反思工具可引用查询历史）：

```python
class SearchPlan(BaseModel):
    topic_summary: str = ""
    queries: list[str] = []
    rationale: str = ""
```

## 3. `AgentState`

继承自 `dict`，作为 ReAct Agent 的**运行内工作记忆**。每次实例化都获得独立的默认值，
可变容器（list / dict）在实例化时做防御性拷贝，不会跨实例共享。

| key | 类型 | 默认 | 含义 |
|---|---|---|---|
| `topic` | str | "" | 课题原文 |
| `language` | "en"\|"zh" | "en" | 输出语言 |
| `years` | (int,int)? | None | 出版年份闭区间 |
| `top_k` | int | 30 | 入选论文数 |
| `sources` | list[str] | [] | 启用的 source 名 |
| `output_path` | str | "report.md" | 报告输出路径 |
| `verbose` | bool | False | 流式打印 Agent 步进度 |
| `messages` | list[BaseMessage] | [] | ReAct 消息转录（SystemMessage / HumanMessage / AIMessage / ToolMessage） |
| `papers` | list[Paper] | [] | 本次运行收集的论文（按 short_id 去重） |
| `plan` | dict | {} | 可选查询计划 |
| `drafts` | dict[str,str] | {} | 章节草稿（反思与提交之间） |
| `reflections` | list[dict] | [] | 反思记录（`{"step", "type", "data"}`） |
| `merged` | list[Paper] | [] | 提交时的 Top-K |
| `sections` | dict[str,str] | {} | 最终章节正文 |
| `step` | int | 0 | 当前 ReAct 步数 |
| `tool_calls` | int | 0 | 已执行工具调用次数 |
| `done` | bool | False | 是否已调用 submit_report |
| `errors` | list[str] | [] | 非致命错误 |

### 附加键（运行期写入）

| key | 类型 | 写入者 | 消费方 |
|---|---|---|---|
| `source_counts` | dict[str, int] | `AgentRuntime.record_papers` | `runner` → `metrics.sources` |
| `llm_usage` | dict | `ReviewAgent.run`（`LLMClient.snapshot()`） | `runner` → `metrics.llm` |
| `max_steps_reached` | bool | `ReviewAgent.run`（超步时） | `runner` → `metrics.max_steps_reached` |

## 4. `Settings`（配置）

文件：`src/lit_review/config.py`。通过 `pydantic-settings` 从 `.env` / 环境变量读取：

| 字段 | 默认 | 含义 |
|---|---|---|
| `llm_api_key` | "" | 任何 OpenAI-compatible Key；空 → 启动报错 |
| `llm_base_url` | "https://api.openai.com/v1" | API endpoint |
| `llm_model` | "gpt-4o-mini" | 模型名 |
| `max_agent_steps` | 12 | ReAct 最大步数 |
| `max_reflections` | 1 | 每种反思的最大轮数 |
| `resume_memory` | False | 是否加载该课题历史记忆 |
| `memory_dir` | "~/.lit_review/memory" | 跨轮记忆目录 |
| `arxiv_max_per_query` | 15 | arXiv 每查询最大返回 |
| `openalex_max_per_query` | 15 | OpenAlex 每查询最大返回 |
| `huggingface_max_per_query` | 20 | HF 每查询最大返回 |
| `semantic_scholar_max_per_query` | 10 | S2 每查询最大返回 |
| `crossref_max_per_query` | 10 | Crossref 每查询最大返回 |
| `huggingface_lookback_days` | 7 | HF trending 回溯天数 |
| `default_sources` | "arxiv,openalex,huggingface" | 默认启用源 |
| `request_timeout` | 30.0 | HTTP 超时秒 |
| `user_agent` | "LiteratureReviewAgent/0.2 (mailto:agent@example.com)" | 必须含 mailto= 才能进 polite pool |
| `default_year_window` | 5 | 默认拉最近 5 年 |

### 派生方法

- `resolved_memory_dir` → 展开 `~` 并 resolve 后的记忆目录
- `enabled_sources()` → 解析 `default_sources` 为列表
- `year_window()` → 基于 `default_year_window` 与当前年
- `has_llm()` → 是否配置 LLM Key

## 5. 记忆快照（per-topic JSON）

文件：`src/lit_review/memory/store.py`。文件名为 `sha256(normalized topic).json`，
存于 `memory_dir`。内容：

```json
{
  "topic": "...",
  "language": "en",
  "years": [2020, 2025],
  "run_id": "...",
  "created_at": "...",
  "updated_at": "...",
  "output_path": "...",
  "sections": {"background": "...", "..."},
  "papers": [{"title": "...", "year": 2024, "..."}],
  "metrics_summary": {}
}
```

只保存最终报告、论文清单与摘要指标，**不保存完整消息转录**（控制体积与隐私）。

## 6. `Metrics`（可选输出）

文件：`src/lit_review/runner.py`。与 `--emit-metrics` 同步落盘为 `<report>.metrics.json`：

```python
@dataclasses.dataclass
class Metrics:
    started_at: str
    finished_at: str
    duration_ms: int
    sources: dict[str, int]     # source_name -> 去重前命中数
    merged: dict[str, int]      # {"papers_collected", "papers_after_dedupe", "kept_after_topk"}
    llm: dict[str, Any]         # LLMClient.snapshot()
    nodes: dict[str, int]       # {"agent_steps", "submit_report"}
    sections: dict[str, int]    # section_name -> body 字符数
    papers_collected: int
    papers_kept: int
    errors: list[str]
    steps: int
    tool_calls: int
    reflections: int
    max_steps_reached: bool
```
