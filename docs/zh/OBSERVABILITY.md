# 可观测性：指标、日志、追踪、调试

## 1. 三种信号

| 信号 | 格式 | 触发 | 用途 |
|---|---|---|---|
| **日志** | 文本 + 时间戳 | 默认（DEBUG off）/ `--verbose` | 排障 |
| **指标** | JSON | `--emit-metrics` | 离线分析、计费 |
| **追踪** | 控制台文本 | `--verbose` | 实时观察每个节点 |

## 2. 日志

通过 Python `logging`：

```python
log = logging.getLogger(__name__)
log.warning("OpenAlex rate limited (HTTP 429), backing off")
```

CLI：
- 默认 `WARNING` 级别 → 只看异常 / 重试
- `--verbose`/`-v` → `INFO` 级别 + 节点进度

不要用 `print()`，会被 Gradio 捕获、污染 Web 终端。

### 关键日志

| logger | 何时记 | 关键字 |
|---|---|---|
| `lit_review.graph.nodes` | plan LLM 失败 | `plan LLM call failed` |
| `lit_review.graph.nodes` | refine LLM 失败 | `refine LLM call failed` |
| `lit_review.tools.*` | 单 source 抛错 | `source <name> failed` |
| `lit_review.tools._http` | 重试 | `GET %s failed (attempt %d)` |
| `lit_review.llm_client` | 429 | `rate-limited` |

## 3. 指标 (`metrics.json`)

启用：`lit-review review ... --emit-metrics`
输出：`reports/<topic>.metrics.json`（与 .md 同目录）

```json
{
  "started_at": "2026-08-16T10:42:13.420+08:00",
  "finished_at": "2026-08-16T10:42:35.910+08:00",
  "duration_ms": 22490,
  "sources": {
    "arxiv": 28,
    "openalex": 17,
    "huggingface": 8,
    "semantic_scholar": 0,
    "crossref": 3
  },
  "merged": {"clusters_before": 56, "papers_after_dedupe": 41, "kept_after_topk": 30},
  "llm": {
    "calls": 7,
    "cache_hits": 2,
    "tokens_in": 12480,
    "tokens_out": 4200,
    "by_tag": {
      "plan": 1,
      "refine.search": 1,
      "synthesize.background": 1,
      "synthesize.methods": 1,
      "synthesize.datasets": 1,
      "synthesize.trends": 1,
      "synthesize.open_problems": 1
    }
  },
  "nodes": {
    "plan": 1234,
    "search_sources": 8421,
    "dedupe_rank": 42,
    "filter_top_k": 1,
    "refine_search": 0,
    "synthesize_sections": 11003,
    "assemble": 2
  },
  "papers_collected": 56,
  "papers_kept": 30,
  "sections": {
    "background": 950,
    "methods": 880,
    "datasets": 410,
    "trends": 720,
    "open_problems": 660
  },
  "errors": []
}
```

### 3.1 字段说明

- `sources.<x>`：从单源返的论文数（**未去重**前）
- `merged.papers_after_dedupe`：union-find 后剩余 cluster 数
- `llm.cache_hits`：LRU/磁盘缓存命中次数
- `nodes.<x>`：节点耗时（ms），由 `@timed_node` 装饰器累加
- `errors`：与 `state.errors` 一致

### 3.2 使用建议

- **调试性能**：`nodes.synthesize_sections` 占比 > 60% → 调小 top_k 或并发章节
- **调试质量**：`llm.cache_hits / llm.calls` > 70% → prompt 太固定，需要 prompt 工程升级
- **调试 source**：`sources` 字典差很大 → 考虑调启用源

## 4. 追踪 (`--verbose`)

```bash
lit-review review "RAG with knowledge graphs" --verbose
```

输出类似（`runner.run()` 通过 `on_node` 逐节点回调，CLI 用 rich 打印）：
```
▶ plan
▶ search_sources
▶ dedupe_rank
▶ filter_top_k
▶ synthesize_sections
▶ assemble
```

更详细的每节点耗时与各源命中数请看 `--emit-metrics` 生成的 `metrics.json`：
`metrics.nodes.<node>`（毫秒）、`metrics.sources.<source>`（未去重命中数）。

`-v` 重点观察哪个 source 卡顿、哪个 LLM 调用慢；完整链路到报告级时长看
`metrics.duration_ms`。

## 5. 状态快照

`--emit-state` 选项额外写 `reports/<topic>.state.json`（**注意：可能包含 LLM API
回包全文，若不希望透出请勿用**）。

调试用：

```python
import json
state = json.load(open("reports/foo.state.json"))
state["merged"][:3]   # 看 top-3
```

## 6. 集成 OpenTelemetry（可选，v0.4）

`LLMClient` 提供了 tracer hook，可注入 OTel：

```python
from lit_review.llm_client import LLMClient

client = LLMClient(settings)
client.set_tracer(otel_tracer)      # 实例方法；span name = "<tag>.llm"
```

详细 span attributes：
- `gen_ai.system` ("openai")
- `gen_ai.request.model`
- `gen_ai.usage.input_tokens`
- `gen_ai.usage.output_tokens`
- `lit_review.cache_hit` (bool)

详细配置不在本节范围，参见 `contrib/otel.py`（实验性）。

