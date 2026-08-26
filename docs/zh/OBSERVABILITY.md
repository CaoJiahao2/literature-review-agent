# 可观测性：指标、日志、追踪、调试

## 1. 三种信号

| 信号 | 格式 | 触发 | 用途 |
|---|---|---|---|
| **日志** | 文本 + 时间戳 | 默认（WARNING）/ `--verbose` | 排障 |
| **指标** | JSON | `--emit-metrics` | 离线分析、计费 |
| **追踪** | 控制台文本 | `--verbose` | 实时观察每个 Agent 步 |

## 2. 日志

通过 Python `logging`；不要用 `print()`（会被 Gradio 捕获，污染 Web 终端）。

CLI：
- 默认 `WARNING` → 只看异常 / 重试
- `--verbose`/`-v` → `INFO` + Agent 步进度

### 关键日志

| logger | 何时记 | 关键字 |
|---|---|---|
| `lit_review.agent.reviewer` | 工具执行失败 | `tool <name> failed` |
| `lit_review.agent.reviewer` | 步调用失败 | `agent step N: LLM call failed` |
| `lit_review.agent.tools` | 搜索工具异常 | `<name> search failed` |
| `lit_review.tools._http` | 重试 | `GET %s failed (attempt %d)` |
| `lit_review.llm_client` | 429 / 重试 | `retryable error on attempt` |
| `lit_review.memory.store` | 记忆读写 | `saved topic memory` / `failed to load` |

## 3. 指标 (`metrics.json`)

启用：`lit-review review ... --emit-metrics`
输出：`<report>.metrics.json`（与 .md 同目录）

```json
{
  "started_at": "2026-08-26T10:42:13.420+08:00",
  "finished_at": "2026-08-26T10:42:35.910+08:00",
  "duration_ms": 22490,
  "sources": {"arxiv": 28, "openalex": 17, "huggingface": 8, "crossref": 3},
  "merged": {
    "papers_collected": 56,
    "papers_after_dedupe": 41,
    "kept_after_topk": 30
  },
  "llm": {
    "calls": 9,
    "cache_hits": 2,
    "tokens_in": 12480,
    "tokens_out": 4200,
    "by_tag": {
      "agent.step.1": 1,
      "agent.step.2": 1,
      "reflection.search_coverage": 1,
      "reflection.report_draft": 1
    },
    "errors": []
  },
  "nodes": {"agent_steps": 6, "submit_report": 1},
  "sections": {
    "background": 950,
    "methods": 880,
    "datasets": 410,
    "trends": 720,
    "open_problems": 660
  },
  "papers_collected": 56,
  "papers_kept": 30,
  "errors": [],
  "steps": 6,
  "tool_calls": 12,
  "reflections": 2,
  "max_steps_reached": false
}
```

### 3.1 字段说明

- `sources.<x>`：从单源返回的论文数（**未去重**前，来自 `source_counts`）
- `merged.papers_after_dedupe`：union-find 去重后的 cluster 数
- `llm.*`：`LLMClient.snapshot()`；`by_tag` 区分 Agent 步与反思子调用
- `steps`：Agent 实际执行的 ReAct 步数
- `tool_calls`：Agent 实际执行的工具调用总数
- `reflections`：两条反思链产生的反思记录总数
- `max_steps_reached`：是否因达到 `max_agent_steps` 而终止（此时 run 会抛错）

### 3.2 使用建议

- **Agent 是否高效**：`tool_calls / steps` 过高 → 工具粒度太碎，考虑合并工具
- **反思是否生效**：`reflections` 应为 `2 × max_reflections`（正常路径）
- **成本**：`llm.calls` 中 `agent.step.*` 占大头；`cache_hits` 高说明反思 prompt 可复用
- **质量**：`papers_kept` 明显低于 `top_k` → 检索覆盖不足，Agent 应已补搜

## 4. 追踪 (`--verbose`)

```bash
lit-review review "RAG with knowledge graphs" --verbose
```

输出类似（`ReviewAgent.run` 通过 `on_node` 回调，CLI 用 rich 打印）：

```
▶ step 1: search_arxiv, search_openalex
▶ step 2: search_huggingface
▶ step 3: review_search_coverage
▶ step 4: search_arxiv, search_crossref
▶ step 5: list_papers
▶ step 6: review_report_draft
✓ submit_report
```

`-v` 重点观察每个步调用了哪些工具；完整链路到报告级时长看 `metrics.duration_ms`。

## 5. 状态快照

`--emit-state` 额外写 `<report>.state.json`（**注意：可能包含 LLM 回包全文，
若不希望透出请勿用**）。

调试用：

```python
import json
state = json.load(open("reports/foo.state.json"))
state["papers"][:3]     # 收集到的论文
state["reflections"]    # 反思记录
state["messages"][:3]   # 消息转录（含 LLM 原文）
```

## 6. 集成 OpenTelemetry（可选）

`LLMClient` 提供 tracer hook：

```python
client = LLMClient(settings)
client.set_tracer(otel_tracer)   # span name = "<tag>.llm"
```

span attributes：`gen_ai.system` / `gen_ai.request.model` /
`gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` / `lit_review.cache_hit`。
