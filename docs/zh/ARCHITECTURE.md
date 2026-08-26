# 技术架构

> 本文聚焦**系统级视图**：单一 ReAct Agent 的组件划分、数据流、生命周期与可观测点。
> 配合 [DESIGN.md](./DESIGN.md) 阅读，后者说明**为什么这样设计**。

## 1. 设计目标

| 目标 | 说明 |
|---|---|
| **单一 Agent** | 一个 ReAct Agent 贯穿「规划 → 检索 → 反思 → 撰写 → 修订 → 提交」全流程 |
| **原生 function calling** | 用 `bind_tools` 驱动工具调用，无 JSON 降级；LLM 端点不支持 `tool_calls` 直接报错 |
| **两段自我反思** | 检索覆盖度反思 + 草稿质量反思，显式暴露为两个工具 |
| **运行内记忆** | 消息转录、已收集论文、章节草稿、反思记录都驻留在 `AgentState` |
| **可选跨轮持久化** | 每个课题一份 JSON 快照，`--resume` 注入历史上下文 |
| **引用防幻觉** | 参考文献由工具从真实语料生成，LLM 只写正文 |
| **可解释 / 可扩展** | 排序公式透明；新增数据源 = 新增一个客户端 + 包装一个工具 |

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
│   • require_llm(settings)（无 Key 直接报错）                 │
│   • 装配 AgentState + Settings                                │
│   • 调用 ReviewAgent.run() → 写报告 + 可选 JSON 指标          │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  ReviewAgent  (src/lit_review/agent/reviewer.py)              │
│                                                              │
│   system prompt + 记忆上下文                                  │
│        → model.bind_tools(TOOLS)                             │
│        → 解析 AIMessage.tool_calls                           │
│        → 执行工具 → 追加 ToolMessage                         │
│        → 直到 submit_report 或 max_agent_steps               │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  Sidecars                                                     │
│   ├─ Tools: arxiv / openalex / huggingface / s2 / crossref   │
│   │         + list_papers / review_search_coverage /          │
│   │           review_report_draft / submit_report             │
│   ├─ Rank: merge_and_rank (union-find 去重 + 加权打分)        │
│   ├─ Memory: per-topic JSON snapshot                          │
│   ├─ LLMClient: 缓存 + 重试 + token 统计 + bind_tools        │
│   └─ Report: template (五段结构) + writer (写盘)              │
└──────────────────────────────────────────────────────────────┘
```

## 3. 详细数据流（一次 `lit-review review` 调用）

```
time ──▶
─────────────────────────────────────────────────────────────────────────────
[Runner]
  1. parse CLI            topic, language, top_k, years, sources, resume, ...
  2. load_settings()      读 .env + env vars, 实例化 Settings
  3. require_llm()        无 LLM_API_KEY 抛 ConfigurationError（无骨架降级）
  4. _normalize_state()   补默认值：sources/years/top_k/...
  5. ReviewAgent.run()    进入 ReAct 循环

[ReviewAgent]
  6. 构建 system prompt；--resume 时注入历史记忆上下文
  7. LLMClient.bind_tools(TOOLS) → bound model
  8. loop:
       a. model.invoke(messages) → AIMessage
       b. 无 tool_calls → 追加 HumanMessage 提示继续（防空转）
       c. 有 tool_calls → 逐个执行，结果作为 ToolMessage 追加回消息
       d. state.step += 1，直到 submit_report 或 max_agent_steps
  9. 达到 max_agent_steps 仍未提交 → 抛 AgentRunError

[Tools]
  search_*       → 调用 tools/<source>.py 客户端 → record_papers 写入工作记忆
  list_papers    → 返回带编号的当前文献列表（merge_and_rank 去重）
  review_search_coverage → 嵌套 LLM 调用批判覆盖度，返回补搜建议
  review_report_draft    → 嵌套 LLM 调用批判草稿，返回逐节修订意见
  submit_report  → 章节正文 + 真实论文语料 → write_report 落盘，done=True

[Runner 收尾]
 10. 汇总 metrics（sources / papers / llm / steps / tool_calls / reflections）
 11. 可选写 <report>.metrics.json 与 <report>.state.json
 12. 自动保存 topic memory 快照（sections + papers + metrics summary）
```

## 4. 模块依赖

```
cli.py / ui.py
      │
      ▼
   runner.py ──────► agent/reviewer.py ──────► agent/tools.py
      │                    │                        │
      │                    ▼                        ▼
      │              llm_client.py            tools/<source>.py
      │                    │                        │
      ▼                    ▼                        ▼
   memory/store.py    llm.py                tools/rank.py
      │                    │                        │
      └────────────┬───────┴────────────────────────┘
                   ▼
            config.py / state.py  (无业务依赖)
```

- `state.py`、`config.py` 无业务依赖（仅 pydantic）
- `tools/*.py` 只依赖 `state`、`config`、`llm`
- 业务模块单向依赖，杜绝循环

## 5. 状态契约（AgentState keys）

| key | 类型 | 含义 |
|---|---|---|
| `topic` | str | 输入课题 |
| `language` | "en"\|"zh" | 输出语言 |
| `years` | (int,int)? | 出版年份闭区间 |
| `top_k` | int | 最终入选论文数 |
| `sources` | list[str] | 本次运行启用的 source 名 |
| `messages` | list[BaseMessage] | ReAct 消息转录（运行内记忆） |
| `papers` | list[Paper] | 本次运行收集的全部论文（按 short_id 去重） |
| `plan` | dict | 可选查询计划（供反思工具引用） |
| `drafts` | dict[str,str] | 章节草稿（反思与提交之间的中间态） |
| `reflections` | list[dict] | 反思记录（类型 + 数据） |
| `merged` | list[Paper] | 提交时的 Top-K |
| `sections` | dict[str,str] | 最终章节正文 |
| `step` | int | 当前 ReAct 步数 |
| `tool_calls` | int | 已执行工具调用次数 |
| `done` | bool | 是否已调用 submit_report |
| `errors` | list[str] | 非致命错误 |

完整字段见 [STATE.md](./STATE.md)。

## 6. 并发与时延模型

搜索工具由 Agent 自主调用（可能出现在同一轮多个 `tool_calls`）。每个搜索工具内部
复用 `tools/<source>.py` 客户端：单查询同步执行。Agent 可以在一个模型回合内并行
请求多个 `search_*` 调用，模型返回多个 `tool_calls`，循环逐个执行。

反思工具（`review_search_coverage` / `review_report_draft`）发起嵌套 LLM 调用，
沿用 `LLMClient.invoke_json` 的缓存与 fallback。

## 7. 失败模型

| 失败类别 | 检测 | 处理 | 用户可见后果 |
|---|---|---|---|
| `LLM_API_KEY` 未设 | `require_llm()` | 抛 `ConfigurationError` | CLI 退出码 2 + 清晰提示 |
| 某 source 抛 5xx/超时 | 搜索工具 try/except | 返回 JSON 错误对象，记录到 `state.errors` | Agent 可换查询 / 换源继续 |
| 某 source 429 | 客户端重试/退避 | 重试后仍失败返回空 | WARN |
| Agent 达到 `max_agent_steps` | ReAct 循环 | 抛 `AgentRunError` | CLI 退出码 1 + 错误信息 |
| 反思子调用失败 | `invoke_json` fallback | 返回保守 fallback critique | Agent 正常继续 |
| 端点不支持 tool_calls | `bind_tools` 异常 | 抛 `ConfigurationError` | 明确报错，不降级 |

## 8. 可观测点

详见 [OBSERVABILITY.md](./OBSERVABILITY.md)。简要：

| 指标 | 收集位置 | 暴露方式 |
|---|---|---|
| 各 source 命中数 | `AgentRuntime.record_papers` 维护 `source_counts` | `metrics.json` |
| 去重统计 | `submit_report` 调用 `merge_and_rank` | `metrics.merged` |
| LLM 调用次数 / tokens | `LLMClient.snapshot()` | `metrics.llm` |
| Agent 步数 / 工具调用数 | `state.step` / `state.tool_calls` | `metrics.steps` / `metrics.tool_calls` |
| 反思轮数 | `state.reflections` 长度 | `metrics.reflections` |
| 是否超步数 | `state.max_steps_reached` | `metrics.max_steps_reached` |
| 状态快照 | `--emit-state` | `<report>.state.json` |

## 9. 扩展点

| 扩展目标 | 入口文件 | 修改范围 |
|---|---|---|
| 新增数据源 | `src/lit_review/tools/<source>.py` + `tools/__init__.py` | 客户端 + `agent/tools.py` 包装一个工具 |
| 新增工具 | `src/lit_review/agent/tools.py` | 写一个 factory 并注册到 `build_tools` |
| 新增反思步骤 | `src/lit_review/agent/tools.py` | 仿照 `review_search_coverage` |
| 替换 LLM Provider | `src/lit_review/llm_client.py` / `llm.py` | 替换 `ChatOpenAI` 为其他 `BaseChatModel` |
| 新增章节 | `src/lit_review/report/template.py:SECTIONS` | 改 dataclass 元组 |
| 替换排序公式 | `src/lit_review/tools/rank.py:score_and_sort` | 改系数、加特征 |

## 10. 已知非目标

以下事项当前**不在范围**，避免误用：

- ❌ 完整消息转录的跨轮持久化（只保存最终报告、论文清单与摘要指标）
- ❌ 实时订阅 / 增量更新（每次运行是独立的）
- ❌ 用户身份 / 协作（无账户、无数据库）
- ❌ PDF 全文解析（仅依赖摘要）
- ❌ 引文图谱可视化（仅以 References 节呈现）
- ❌ 多语言综述（仅中英双语）
