# 关键设计决策（Design Decisions）

> 每条决策给出**问题 → 方案 → 取舍 → 替代方案**。所有"为什么"集中在此。

## D1. 为什么用单一 ReAct Agent，而不是确定性 LangGraph 流水线？

**问题**：文献调研的难点不是"固定步骤"，而是**检索覆盖度不可预知**——什么时候该补搜、
该搜什么、什么时候可以开始写，不同课题差异很大。

**方案**：把 plan → search → dedupe → refine → synthesize 替换为一个 ReAct Agent。
LLM 通过原生 function calling 自主决定何时检索、何时反思、何时撰写、何时提交。

**为什么**：
- 让"检索覆盖度"和"草稿质量"成为 LLM 可自主评估与修正的对象，而不是写死的条件边
- 工具调用序列本身就是可观测的"思考轨迹"，便于调试与教学演示
- 最大步数（`max_agent_steps`）作为安全阀，防止 Agent 空转

**替代**：保留 LangGraph 固定管线。问题：refine 逻辑硬编码，新增数据源要改图；
不同课题的检索策略无法自适应。

## D2. 为什么用 LangChain 原生 function calling（`bind_tools`），不做 JSON 降级？

**问题**：部分 OpenAI-compatible 端点 / 小模型对 `tool_calls` 支持不稳定。

**方案**：只走 `model.bind_tools(TOOLS)` 原生 function calling。端点不支持 `tool_calls`
时直接报 `ConfigurationError`，不提供"解析 JSON 文本"的降级。

**为什么**：
- JSON 降级路径会让工具调用格式与错误处理分裂成两套，维护成本高
- 原生 `tool_calls` 的结构化 args 能保证工具入参类型安全
- 目标用户（配置 LLM 的开发者）的端点普遍支持 function calling

**替代**：解析模型输出文本中的 JSON。问题：格式脆弱，参数校验困难，容易注入错误。

## D3. 为什么彻底移除 `--no-llm` 骨架模式？

**问题**：旧版无 Key 时降级为确定性骨架报告，试图降低试用门槛。

**方案**：删除骨架路径；无 `LLM_API_KEY` 时 `require_llm()` 抛 `ConfigurationError`。

**为什么**：
- 骨架报告与 Agent 主线是两套并行的输出逻辑，任何改动都要双份维护
- 本项目的定位是"展示 Agent 工程实现"，确定性骨架反而稀释了 Agent 的主体性
- 清晰报错比静默降级更符合"工程实现"的透明度要求

**替代**：保留骨架。问题：两套路径测试爆炸，且用户无法感知自己到底跑的是哪套。

## D4. 两段自我反思为什么要显式做成两个工具？

**问题**：自我反思可以藏在 system prompt 里让模型"脑内完成"，但不可观测、不可控。

**方案**：
1. `review_search_coverage()` — 用嵌套 LLM 调用批判当前查询覆盖度，返回 gaps 与补搜建议
2. `review_report_draft(sections=...)` — 用嵌套 LLM 调用批判当前草稿，返回逐节修订意见

两者都写入 `state.reflections`，受 `max_reflections` 上限约束。

**为什么**：
- 反思成为**可审计的工具调用**，而不是不可见的心智活动
- 嵌套 LLM 调用复用 `LLMClient.invoke_json` 的缓存 / 重试 / fallback，成本可控
- 反思结果作为 ToolMessage 回流给主模型，形成真正的"评价 → 行动"闭环

**替代**：在 system prompt 中要求模型自我检查。问题：不可观测，小模型容易跳过。

## D5. 引用防幻觉：为什么 `submit_report` 只接收正文？

**问题**：让 LLM 直接输出参考文献，容易编造不存在的论文。

**方案**：`submit_report(sections: dict[str,str])` 只接收章节正文；参考文献由工具从
`AgentState.papers` 的真实语料经 `merge_and_rank` 去重排序后自动生成。

**为什么**：
- 引用来源被工具强制约束为"真正检索到的论文"
- 正文中的 `[#]` 编号与工具生成的 References 一一对应
- LLM 仍可负责论证与组织，但无法凭空创造引用

**替代**：让 LLM 直接写 References。问题：幻觉引用是文献综述 Agent 的致命伤。

## D6. 记忆分层：运行内 vs 跨轮

**问题**：Agent 需要短期上下文，也需要跨运行复用历史成果。

**方案**：
- **运行内记忆**：`AgentState.messages`（消息转录）+ `papers` + `drafts` + `reflections`
- **跨轮记忆**：每个规范化课题一个 JSON 文件（`sha256(topic).json`），只保存最终报告、
  论文清单与摘要指标；`--resume` 时注入 system prompt

**为什么**：
- 完整消息转录体积大、含隐私，不落盘
- 最终报告 + 论文清单已足够支撑"继续调研"的复用价值
- 文件按 topic 哈希命名，规范化后大小写 / 空白不影响命中

**替代**：持久化完整消息。问题：体积、隐私、且旧消息可能与新参数冲突。

## D7. 跨源去重为什么用"并集 (union-find)"？

**问题**：arXiv-only 论文没有 DOI，OpenAlex-only 论文没有 arxiv id。

**方案**：把 doi / arxiv_id / 标准化标题看作等价类签名，任何一项匹配即合并（union-find）。

**为什么**：
- 同一篇论文跨源可能只有部分标识重叠，并集能捕获这些情况
- 无状态扩张，未来加第 6、7 个 source 也不破坏兼容

**替代**：按 source 优先级去重 / title 模糊匹配。问题：去重顺序敏感 / 同名论文歧义多。

## D8. 排序公式为何选 0.5·citation + 0.3·recency + 0.2·abstract_richness？

**问题**：如何给异构数据源出一个公平的单维度评分。

**方案**：三维加权，全部归一化到 [0,1]（详见 [RANKING.md](./RANKING.md)）。

| 维度 | 权重 | 用意 |
|---|---|---|
| citation | 0.5 | 学术价值的通用代理信号 |
| recency | 0.3 | 综述读者优先看近 2 年进展 |
| abstract_richness | 0.2 | 宁可读到完整摘要的论文 |

**替代**：纯 citation 会漏掉新论文；纯 recency 会奖励水文。

## D9. 工具签名为什么用 `list[int]` 而不是 `tuple[int,int]`？

**问题**：计划要求 `years: tuple[int,int]|None`。

**方案**：function-calling 的 JSON Schema 天然只有数组，工具暴露 `Optional[list[int]]`，
内部 `_year_tuple` 转换为 `(start, end)` 传给 source 客户端。

**为什么**：JSON 没有 tuple 概念；模型也只能生成数组。外部契约与内部类型各取所需。

## D10. LLMClient 为什么新增 `bind_tools` / `invoke_chat`，而不缓存 Agent 步？

**问题**：Agent 循环调用依赖完整演化中的消息转录，缓存是否还适用？

**方案**：
- `bind_tools()` / `invoke_chat()` 专门服务 Agent 循环：带重试、token 统计，但**不缓存**
- 反思子调用继续走 `invoke_json`（有缓存与 fallback）

**为什么**：Agent 步的输入是动态增长的消息列表，缓存命中率低且可能返回过期结果；
反思 prompt 则相对静态，缓存收益明显。

**替代**：全缓存。问题：Agent 步缓存 key 巨大且内容易变，命中率低。

## D11. 为什么 `run()` 保持入口签名不变？

**问题**：重构后 CLI / UI 已改动，`runner.run` 的签名还要不要变？

**方案**：`runner.run(state, settings, *, emit_metrics, emit_state, on_node) -> RunResult`
保持不变，内部改调 `ReviewAgent`；`RunResult.state` 从 `GraphState` 换成 `AgentState`。

**为什么**：CLI 与 UI 都通过 `runner.run` 这个唯一入口，签名稳定可减少上层改动；
测试也只需替换 `ReviewAgent` 即可离线跑通全流程。

## D12. 测试策略：离线优先 + 网络标记

**问题**：开发期间不能每次都跑真实 API，但又要保证 source 客户端可用。

**方案**：
- 离线测试：`tests/test_<x>.py` 用 fake model / respx mock 验证循环、工具、记忆、反思
- 网络测试：`@pytest.mark.network` 标记，默认 CI 跳过
- CI 推荐命令：`pytest -q -m "not network"`

**替代**：mock 一切。问题：mock 与真实 API 行为分歧会随版本漂移。
