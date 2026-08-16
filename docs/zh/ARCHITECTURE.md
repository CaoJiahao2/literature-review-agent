# 技术架构

> 本文聚焦**系统级视图**：组件划分、数据流、生命周期、可观测点。
> 配合 [DESIGN.md](./DESIGN.md) 阅读，后者说明**为什么这么设计**。

## 1. 设计目标

| 目标 | 说明 |
|---|---|
| **零配置可用** | 不强制依赖任何 LLM Key；无 Key 时降级为"骨架报告" |
| **多源覆盖** | 一次性并发查询 5 个异构开放学术数据源 |
| **可解释** | 排序公式和去重规则完全透明，可在 README 中复现 |
| **可扩展** | 添加新数据源 = 新建一个文件 + 在注册表中加一行 |
| **可重放** | 一次研究运行结束后，可回看每个节点的输出与耗时 |

## 2. 顶层视图

```
┌─────────────────────────────────────────────────────────────────┐
│  Entry Points                                                    │
│   ├─ CLI  (Typer)        lit-review review <topic>               │
│   ├─ CLI  (Typer)        lit-review ui                          │
│   └─ Gradio Blocks        Web UI (theme=Soft)                    │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  Runner Layer  (cli._do_run / ui._run_ui → lit_review.runner) │
│   • 解析参数、检测语言（CJK → zh）                           │
│   • 装配 Settings + GraphState                                │
│   • 调用 build_graph → stream/invoke                         │
│   • 写报告（Markdown + 可选 JSON 指标）                      │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  LangGraph StateGraph  (src/lit_review/graph/builder.py)      │
│                                                              │
│  plan ─▶ search_sources ─▶ dedupe_rank ─▶ filter_top_k       │
│                                       │                      │
│                                       ▼                      │
│                              should_refine (条件边)           │
│                              /            \                  │
│                            refine         synthesize_sections │
│                              │                   │           │
│                              ▼                   ▼           │
│                       search_sources           assemble ─▶ END │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  Sidecars                                                     │
│   ├─ Tools: arxiv / openalex / huggingface / s2 / crossref   │
│   ├─ Rank: merge_and_rank (union-find 去重 + 加权打分)        │
│   ├─ LLMClient: 缓存 + 重试 + 用量统计                        │
│   └─ Report: template (骨架) + writer (写盘)                  │
└──────────────────────────────────────────────────────────────┘
```

## 3. 详细数据流（一次 `lit-review review` 调用）

```
time ──▶
─────────────────────────────────────────────────────────────────────────────
[Runner]
  1. parse CLI            topic, language, top_k, years, sources, no_llm, ...
  2. load_settings()      读 .env + env vars, 实例化 Settings
  3. build GraphState     准备上下文（topic, years, ...）
  4. build_graph(s)       编译 StateGraph

[Graph @start]
  ┌─────────────────────────────────────────────────────────────────┐
  │ plan_node                                                       │
  │   • 调用 LLM  → JSON{topic_summary, queries[4-6], rationale}    │
  │   • 失败 → _plan_queries_deterministic（5 个模板查询）         │
  │   • 输出 → state.plan (SearchPlan)                              │
  └─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ search_sources_node                                             │
  │   • 遍历 state.sources（或 settings.enabled_sources）          │
  │   • 每个 source: parallel await search_<source>(queries)        │
  │   • 异常 → 收集到 state.errors, 不中断流程                    │
  │   • 输出 → state.source_results, state.errors                   │
  └─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ dedupe_rank_node                                                │
  │   • merge_and_rank(source_results.values())                    │
  │   • 集群 by DOI ∪ arxiv_id ∪ norm_title                         │
  │   • 同集群内部做字段合并，URL 偏好去 OpenAlex                  │
  │   • 评分 0.5·citation + 0.3·recency + 0.2·abstract_richness    │
  │   • 输出 → state.merged (按 score 降序)                         │
  └─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ filter_top_k_node                                               │
  │   • state.merged = state.merged[:top_k]                         │
  └─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼  (条件边 should_refine)
  ┌────────────────────────────────────────────────────────────────────────────────┐
  │ should_refine(state)                                                            │
  │   if iteration < max_iter and len(merged) < max(5, top_k // 2):                │
  │       return "refine"                                                          │
  │   return "synthesize"                                                          │
  └────────────────────────────────────────────────────────────────────────────────┘
                          │                          │
                  "refine" ▼                "synthesize" ▼
  ┌────────────────────────────────────┐  ┌─────────────────────────────────────┐
  │ refine_search_node                 │  │ synthesize_sections_node            │
  │  • LLM 给 2-3 个新查询            │  │  • 串/并行遍历 SECTIONS (5 章节)    │
  │  • 调 search_sources_with(...)    │  │  • 每章: 模板化 prompt + LLM       │
  │  • 重新跑 dedupe_rank             │  │  • 失败 → skeleton_body(...)       │
  │  • iteration += 1                 │  │  • 输出 → state.sections           │
  └────────────────────────────────────┘  └─────────────────────────────────────┘
                          │                          │
                          ▼                          ▼
                  (回到 search_sources)       ┌──────────────────────────┐
                                              │ assemble_node            │
                                              │  • 透传关键字段          │
                                              │  • 标记 final_report     │
                                              └──────────────────────────┘
                                                          │
                                                          ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ write_report(state, output_path)                                │
  │   • 拼 Markdown (template.render_report)                        │
  │   • 写盘 out.write_text(md)                                     │
  │   • 可选写 metrics.json（--emit-metrics 时）                   │
  └─────────────────────────────────────────────────────────────────┘
```

## 4. 模块依赖图

```
                ┌─────────────┐
                │   cli.py    │──┐
                └─────────────┘  │ import
                     │ ▲         │
                     ▼ │         │
                ┌─────────────┐  │
                │   ui.py     │──┘
                └─────────────┘
                     │
                     ▼
            ┌────────────────────┐
            │     runner.py      │   ← 新增：唯一执行入口（CLI/UI 共用）
            └────────────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
 ┌──────────┐  ┌──────────┐  ┌──────────┐
 │ graph/   │  │ config.py│  │ state.py │
 │ builder  │  └──────────┘  └──────────┘
 └────┬─────┘
      ▼
 ┌──────────┐   ┌────────────┐   ┌──────────────┐
 │ nodes.py │──▶│ tools/...  │──▶│ llm.py +     │
 └──────────┘   └────────────┘   │ llm_client.py│
                                 └──────────────┘
                     │
                     ▼
              ┌──────────────┐
              │ report/      │
              │ template.py  │
              │ writer.py    │
              └──────────────┘
```

**依赖原则**：
- `state.py` 不依赖任何业务模块（只依赖 pydantic）
- `config.py` 同样无业务依赖
- `tools/*.py` 只能依赖 `state`、`config`、`llm` 之外的公共层
- 业务模块之间**单向依赖**，杜绝循环

## 5. 状态契约（GraphState keys）

仅列关键字段，全字段见 [STATE.md](./STATE.md)。

| key | 类型 | 含义 |
|---|---|---|
| `topic` | str | 输入课题 |
| `language` | "en"\|"zh" | 输出语言（基于 CJK 自动检测，可覆盖） |
| `years` | (int,int)? | 出版年份闭区间 |
| `top_k` | int | 最终入选论文数 |
| `max_iter` | int | refine 上限 |
| `sources` | list[str] | 本次运行启用的 source 名 |
| `plan` | SearchPlan | LLM 生成的查询计划 |
| `source_results` | dict[str, list[Paper]] | 每源原始结果 |
| `merged` | list[Paper] | 去重 + 评分 + 截断后的 Top-K |
| `iteration` | int | 当前 refine 轮数 |
| `sections` | dict[str, str] | 章节名 → Markdown body |
| `errors` | list[str] | 非致命错误（用于 WARN 输出） |

另有 3 个 `__dunder__` 内部通道（`__node_times__` / `__dedupe_stats__` /
`__llm_client__`）**必须声明在 `GraphState` 上**，否则 LangGraph 会在节点间
丢弃它们。用途见 [STATE.md](./STATE.md) §4。

## 6. 并发与时延模型

优化后的并发模型：

```
                    ┌──────────┐         ┌─ arxiv ──┐
plan (1×LLM) ──▶    │ search_  │ ──fan-out┤           │ ──▶
                    │ sources  │         ├─ openalex ┤
                    │ (async)  │         ├─ HF        ┤
                    └──────────┘         ├─ S2        ┤
                                          └─ crossref ┘
                              ▲
                              │ 共享 semaphore=N
                              ▼
                    同一 source 内不同 query 并发
```

并发度由 `tools/async_runner.run_sources_async()` 的两个参数控制：
`max_concurrent_sources=4`（全局同时跑的源数上限）与
`max_concurrent_queries_per_source=3`（单源内并发的 query 数）。
每个源还有自己的并发提示（arXiv=3 / OpenAlex=2 / S2=1 / Crossref=2 / HF=1），
以尊重各服务商的限流（OpenAlex、S2 对 429 很敏感）。

LLM 章节合成阶段独立并发（每章之间不共享 KV cache，互不阻塞）：
`synthesize_sections_node` 用 `asyncio.gather` 并行触发 5 个章节调用，
在已有事件循环的主机（Gradio/Jupyter）上会自动把合成工作放到独立线程的私有
事件循环中执行，避免 `asyncio.run()` 与运行中循环冲突。

## 7. 失败模型与降级

| 失败类别 | 检测 | 降级策略 | 用户可见后果 |
|---|---|---|---|
| `LLM_API_KEY` 未设 | `Settings.has_llm()` | 全文回退到 `skeleton_body()` | 报告中章节为骨架+论文清单 |
| 某 source 抛 5xx/超时 | try/except per source | 跳过该 source，其他继续 | CLI 输出黄色 WARN |
| 某 source 抛 429/5xx | `safe_get(_async)` 识别 | 重试至多 2 次（异步带 0.25s 退避）后返回空，其他 source 继续 | WARN 中告知 |
| LLM 章节合成抛错 | try/except per section | 该章回退到 skeleton | WARN + 该章节缺正文 |
| 全 source 失败但有 LLM | iteration==0 但 merged 为空 | 直接 synthesize，提示"未搜到论文" | 章节里写明确说明 |
| 全部失败 | search & LLM 都失败 | 抛出 `RunnerError` | CLI 退出码 2 + 错误信息 |

## 8. 可观测点

详见 [OBSERVABILITY.md](./OBSERVABILITY.md)。简要：

| 指标 | 收集位置 | 暴露方式 |
|---|---|---|
| 各 source 命中数 | `tools/async_runner.py:run_sources_async` | `metrics.json` |
| 去重统计 | `dedupe_rank_node` / `refine_search_node` 写入 `__dedupe_stats__` | `metrics.merged` |
| LLM 调用次数 / tokens | `LLMClient` | `metrics.json` + log |
| 各 node 耗时 | 节点内 `timed_node` 上下文管理器 | `--verbose` 控制台 + JSON |
| 错误明细 | `state.errors` | WARN 列表 + JSON |
| 状态快照 | `--emit-state` | `<report>.state.json` |

## 9. 扩展点

| 扩展目标 | 入口文件 | 修改范围 |
|---|---|---|
| 新增数据源 | `src/lit_review/tools/<source>.py` + `tools/__init__.py` | ~50 行 Python |
| 新增 LLM Provider | `src/lit_review/llm_client.py` | 替换 `ChatOpenAI` 为其他 `BaseChatModel` |
| 新增章节 | `src/lit_review/report/template.py:SECTIONS` | 改 dataclass 元组 |
| 新增节点 | `src/lit_review/graph/nodes.py + builder.py` | 写节点函数 + 加边 |
| 替换排序公式 | `src/lit_review/tools/rank.py:score_and_sort` | 改系数、加特征 |

## 10. 已知非目标

以下事项当前**不在范围**，避免误用：

- ❌ 实时订阅 / 增量更新（每次运行是独立的）
- ❌ 用户身份 / 协作（无账户、无数据库）
- ❌ PDF 全文解析（仅依赖摘要）
- ❌ 引文图谱可视化（仅以 References 节呈现）
- ❌ 多语言综述（仅中英双语）

