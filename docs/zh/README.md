# 📚 文献综述 Agent —— 中文文档

> 该目录是 `literature-review-agent` 项目的完整中文技术文档。
> 与 README.md 互补：README 面向**使用者**（如何跑起来），本文档面向**开发者 / 架构师**（如何理解、扩展、运维）。

## 目录索引

| 文档 | 内容 | 受众 |
|---|---|---|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 整体技术架构、数据流、时序、模块依赖 | 架构师、新人 |
| [DESIGN.md](./DESIGN.md) | 关键设计决策与权衡（为什么这样设计） | 架构师 |
| [STATE.md](./STATE.md) | AgentState / Paper / Section 字段规范 | 开发者 |
| [SOURCES.md](./SOURCES.md) | 数据源接口契约与扩展指南 | 开发者贡献者 |
| [RANKING.md](./RANKING.md) | 去重 + 评分算法详解与可调参数 | 算法 / 开发者 |
| [LLM.md](./LLM.md) | LLM 客户端抽象、缓存、重试与 function calling | 开发者 |
| [OBSERVABILITY.md](./OBSERVABILITY.md) | 指标、日志、追踪、JSON 副产品 | SRE / 运维 |
| [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) | 常见故障、限流来源、排查清单 | 用户 / 运维 |
| [DEVELOPMENT.md](./DEVELOPMENT.md) | 开发约定、测试策略、贡献流程 | 贡献者 |

## 项目定位一句话

> 把任意 AI 研究课题（任意人类语言）交给**单一 ReAct Agent**：由 LLM 自主规划检索、
> 调用学术检索工具、反思检索覆盖度、撰写五段式综述、反思草稿质量并修订，最终提交
> Markdown 报告。参考文献由工具从真实语料生成，杜绝编造引用。

## 30 秒项目地图

```
CLI / Gradio UI
        │
        ▼
   runner.run(state, settings) ── ReviewAgent
        │
        ▼
  ReAct 循环（bind_tools → tool_calls → ToolMessage → …）
        │
        ▼
  submit_report ── merge_and_rank ── write_report
        │
        ▼
    report.md  +  run-metrics.json  +  per-topic memory JSON
```

更多细节请直接读 [ARCHITECTURE.md](./ARCHITECTURE.md)。
