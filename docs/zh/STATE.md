# 状态与数据结构参考

> 所有跨节点传递的数据结构都列在这里。改字段请同时更新本文件。

## 1. `Paper` （核心数据单元）

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

- **`short_id()`** → 用于 dedupe 的标准 key 格式：
  ```text
  doi:<lowercase doi>   (if doi)
  arxiv:<lowercase id>   (else if arxiv_id)
  title:<normalized title>  (else)
  ```

- **`canonical_url()`** → 优先 DOI / arXiv canonical URL；如果都缺但 url 字段指向 openalex 内部 ID，则返回空串避免读者点进无意义链接。

- **`display_ref(n)`** → 一行参考文献：
  ```text
  [n] author1, author2, author3 et al. (year). title. venue _source_ [url](url)
  ```

## 2. `SearchPlan`

`plan` 节点的输出：

```python
class SearchPlan(BaseModel):
    topic_summary: str = ""     # 用 1-2 句概括课题
    queries: list[str] = []     # 4-6 个关键词查询
    rationale: str = ""         # LLM 解释为何选这些查询
```

## 3. `SectionDraft`

理论上供后续"分章缓存"使用，当前未启用，仅占位。

## 4. `GraphState`

继承自 `dict`，作为 LangGraph 跨节点传递的载体。完整 keys 列表：

### 输入（由 Runner 写入）

| key | 类型 | 默认 | 含义 |
|---|---|---|---|
| `topic` | str | 必填 | 课题原文 |
| `language` | "en"\|"zh" | 自动检测 | 输出语言 |
| `years` | (int,int)? | 近 5 年 | 出版年份闭区间 |
| `top_k` | int | 30 | 入选论文数 |
| `max_iter` | int | 2 | refine 上限 |
| `no_llm` | bool | False | 强制走骨架模式 |
| `output_path` | str | "report.md" | 报告输出路径 |
| `verbose` | bool | False | 流式打印节点进度 |
| `sources` | list[str] | settings.enabled_sources() | 启用的源 |

### 中间（节点写入）

| key | 类型 | 写入节点 |
|---|---|---|
| `plan` | `SearchPlan` | plan_node |
| `source_results` | `dict[str, list[Paper]]` | search_sources_node |
| `arxiv_results` | `list[Paper]` | 向后兼容 v1 |
| `openalex_results` | `list[Paper]` | 向后兼容 v1 |
| `merged` | `list[Paper]` | dedupe_rank_node / refine_search_node |
| `iteration` | int | plan_node / refine_search_node |
| `sections` | `dict[str, str]` | synthesize_sections_node |
| `errors` | `list[str]` | 各节点累积 |

### 内部通道（`__dunder__`，声明在 GraphState 上以便 LangGraph 跨节点携带）

| key | 类型 | 写入者 | 消费方 |
|---|---|---|---|
| `__node_times__` | dict[str, int] | 各节点 `timed_node`（由 builder 的包装器带出） | `runner` → `metrics.nodes` |
| `__dedupe_stats__` | dict[str, int] | dedupe_rank_node / refine_search_node | `runner` → `metrics.merged` |
| `__llm_client__` | `LLMClient` | `runner.run()` 注入 | 各节点 `_client()` 复用同一实例 |

> 这些 key 之所以要**声明**在 `GraphState` 上，是因为 LangGraph 只会把
> schema 里已知的 key 在节点间传递；不声明的 key 会被静默丢弃（v0.2 曾因此
> 丢失节点耗时与去重统计）。

### 输出（仅 assemble 标记）

| key | 类型 | 含义 |
|---|---|---|
| `final_report` | str | 哨兵值 `"ok"`，表示跑完 |

## 5. `Settings`（配置）

文件：`src/lit_review/config.py`。通过 `pydantic-settings` 从 `.env` / 环境变量读取。
所有字段名都小写，映射 `.env` 中大写同名 key：

| 字段 | 默认 | 含义 |
|---|---|---|
| `llm_api_key` | "" | 任何 OpenAI-compatible Key；空 → 骨架 |
| `llm_base_url` | "https://api.openai.com/v1" | API endpoint |
| `llm_model` | "gpt-4o-mini" | 模型名 |
| `arxiv_max_per_query` | 15 | arXiv 每查询最大返回 |
| `openalex_max_per_query` | 15 | OpenAlex 每查询最大返回 |
| `huggingface_max_per_query` | 20 | HF 每查询最大返回 |
| `semantic_scholar_max_per_query` | 10 | S2 每查询最大返回 |
| `crossref_max_per_query` | 10 | Crossref 每查询最大返回 |
| `huggingface_lookback_days` | 7 | HF trending 回溯天数 |
| `default_sources` | "arxiv,openalex,huggingface" | 默认启用源 |
| `request_timeout` | 30.0 | HTTP 超时秒 |
| `user_agent` | "LiteratureReviewAgent/0.1 (mailto:agent@example.com)" | 必须含 mailto= 才能进 polite pool |
| `default_year_window` | 5 | 默认拉最近 5 年 |

### 派生方法

- `enabled_sources() -> list[str]`：解析 `default_sources` 为列表
- `year_window() -> (int, int)`：基于 `default_year_window` 与当前年
- `has_llm() -> bool`：判断是否启用 LLM

## 6. `Metrics`（可选输出）

文件：`src/lit_review/runner.py`（v0.2 新增）。与 `--emit-metrics` 同步落盘
为 `<report>.metrics.json`：

```python
@dataclasses.dataclass
class Metrics:
    started_at: str                       # ISO8601
    finished_at: str
    duration_ms: int                      # 整次 run 的墙钟耗时
    sources: dict[str, int]               # source_name -> 去重前命中数
    merged: dict[str, int]                # {"clusters_before", "papers_after_dedupe", "kept_after_topk"}
    llm: dict[str, Any]                   # {"calls", "cache_hits", "tokens_in", "tokens_out", "by_tag", "errors"}
    nodes: dict[str, int]                 # node_name -> ms（timed_node）
    sections: dict[str, int]              # section_name -> body 字符数
    papers_collected: int                 # 各源原始论文总数（未去重）
    papers_kept: int                      # top-k 截断后保留数
    errors: list[str]
```

`merged` 由 `dedupe_rank_node` / `refine_search_node` 写入的
`__dedupe_stats__` 汇总而来；`llm` 由 `LLMClient.snapshot()` 提供。

