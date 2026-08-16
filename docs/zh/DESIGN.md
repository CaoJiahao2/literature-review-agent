# 关键设计决策（Design Decisions）

> 每条决策给出**问题 → 方案 → 取舍 → 替代方案**。所有"为什么"集中在此。

## D1. 用 LangGraph 而不是普通函数管线？

**问题**：流程是 DAG，但有条件分支（refine 循环）和可失败节点（5 个不同 source）。

**方案**：采用 LangGraph `StateGraph`，每个节点是纯函数 `(state, settings) -> partial_state`，
图的状态完全由 `GraphState`（dict）承载。

**为什么**：
- 节点失败被隔离到 `state.errors`，主流程不停（生产 LLM 任务里"宁可少一个 section 也不要全跑挂"）
- 条件边 `should_refine` 是声明式的，方便后续插入新条件
- 兼容 LangGraph 自带的 streaming、子图、checkpoint（后续可启用）

**替代**：直接写 `while True: ...`。问题：测试困难，无法观察到中间状态，无法流式输出。

## D2. 不用 ReAct / Tool Calling，让 LLM 调工具？

**问题**：让 LLM 自己选 source、决定何时调多少次，看起来更"智能"。

**方案**：固定管线：plan → parallel search → dedupe → synthesize。把"用 LLM 选 source"
放到 **plan 节点**（生成 queries），把"用 LLM 综合多源"放到 **synthesize 节点**。

**为什么**：
- 论文调研需要**可复现**："同一课题、同一时间，得到的文献池应大致相同"
- Tool Calling 在小模型上不稳定，固定管线可以用任何 OpenAI-compatible 模型
- 5 个 source 接口差异巨大（XML / JSON / GraphQL / 倒排索引），用 LLM 调度容易出错

**替代**：ReAct Agent。代价：成本更高，结果更不可预测。

## D3. 跨源去重为什么用"并集 (union-find)" 而不是"按 source 优先级去重"？

**问题**：arXiv-only 论文没有 DOI，OpenAlex-only 论文没有 arxiv id，怎么办？

**方案**：把 doi / arxiv_id / 标准化标题看作 *等价类签名*，任何一项匹配即合并。

**为什么**：
- 同一篇论文在 arXiv 和 OpenAlex 上只有 arxiv_id 或只有 doi 是常见情况
- 用"哪个 source 先来"决定保留，会因为去重顺序不同而丢失信息
- 并集合并可以无状态扩张，未来加第 6、7 个 source 也不破坏兼容

**替代**：以 title 模糊匹配为主。问题：标题歧义多（同名论文很多）、依赖语言（中英文标题）。

## D4. 排序公式为何选 0.5·citation + 0.3·recency + 0.2·abstract_richness？

**问题**：如何给异构数据源出一个**公平的单维度**评分？

**方案**：三维加权，全部归一化到 [0,1]。

| 维度 | 公式 | 用意 |
|---|---|---|
| citation | `log1p(cites) / log1p(max_cites)` | 让 0~10 引用与 1000+ 引用在同一尺度 |
| recency | `(year - min_year) / (max_year - min_year)` | 让 2018 与 2024 在同一尺度 |
| abstract_richness | `min(1, len(abstract) / 1500)` | 抽象长度 ≥1500 字即满分，避免空洞摘要拖累 |

**为什么 0.5 / 0.3 / 0.2**：
- 0.5 citation：引用是对**学术价值**最通用的代理信号
- 0.3 recency：综述读者通常希望优先看到近 2 年的进展
- 0.2 abstract_richness：宁可读到完整摘要的论文，也不要只有 title 的 stub

**替代**：纯 citation。会漏掉当年新论文；纯 recency 会奖励水文。
**当前共识**：用户最常反馈的两个问题是"包含太老的论文"和"包含没摘要的 stub"，
所以 recency + abstract_richness 各保留一个槽位。

**扩展性**：如要引入 venue / categories / source diversity，
请到 [RANKING.md](./RANKING.md) 看扩展步骤。

## D5. 为什么 source 搜索串行调？还要不要全异步并发？

> 该决策对应 v0.2.0 的优化。

**问题（v0.1）**：5 个 source × 5 个 query = 25 个 HTTP 调用，全部串行，
一次完整调研动辄 60-120 秒。

**方案（v0.2）**：
1. 顶层 fan-out：5 个 source 用 `asyncio.gather(...)` 并发
2. 同 source 内多 query 用 `asyncio.Semaphore(N)` 受控并发
3. 默认上限：`max_concurrent_sources=4`，单源内 query 并发
   `max_concurrent_queries_per_source=3`，并叠加每源并发提示
   （arXiv=3 / OpenAlex=2 / S2=1 / Crossref=2 / HF=1）

**为什么不全放开**：
- OpenAlex / S2 的 429 限流对并发敏感，盲目全并发反而触发退避
- 每个 source 的 `<source>_max_per_query` 是单查询返回上界，不是越大越好
- 用户的网速 / DNS 也常常成为瓶颈

**替代**：把 source 也丢到 LangGraph 节点，每个 source 一个节点并行。
问题：langgraph 节点之间共享 client 不如 asyncio 灵活，且需引入 conditional edge 判断"是否完成"。

## D6. LLM 章节合成为什么不用一次 prompt 生成整篇报告？

**问题**：一次 prompt 让 LLM 输出一整篇 5 章节综述。

**方案**：每章节一次 LLM 调用，输入只用 Top-K 子集 + 章节 prompt。

**为什么**：
- 长上下文会让小模型把章节混在一起
- 章节之间独立失败，便于降级到骨架
- 章节之间可以并行（v0.2 引入）
- 后续可以做"对单章节重写"的人机循环

**替代**：一次 prompt。代价：出错概率随长度指数增长，无法局部重写。

## D7. 为什么默认不带 LLM Key 也能跑？

**问题**：用户首次试用不想配 Key，但又需要看到价值。

**方案**：
- `Settings.has_llm()` 检测，无 Key 时 `build_chat_model() → None`
- 节点代码路径：`if model is None: skeleton_body()`
- 章节就是"占位文字 + 来自 Top-K 的论文清单 + 引用"

**为什么**：骨架报告足以让用户体验"我搜到了 N 篇相关论文"，是真正的价值锚点。

**替代**：强制要求 Key。结果：试用人数减半。

## D8. LangChain 的 BaseChatModel 而不是裸调用 openai SDK？

**问题**：选 `langchain-openai` 还是直接 `openai.OpenAI()`。

**方案**：用 `langchain_openai.ChatOpenAI`，任何 OpenAI-compatible 端点都通过。

**为什么**：
- 与 LangGraph 节点无缝集成
- `BaseChatModel.invoke()` 返回 `AIMessage`，自带 retry / token 用量元数据
- 切换 Anthropic / Gemini 也只需换一个 import

**替代**：直接 openai SDK。代价：绑定 OpenAI；retry 与 streaming 要自己写。

## D9. 配置：用 .env + pydantic-settings 而不是 argparse

**问题**：CLI 参数很多 + 又有环境变量要兼容。

**方案**：
- 静态配置（Key、端口、超时） → `Settings`（pydantic-settings 读 `.env`）
- 每次调用的查询语义（topic、top_k）→ CLI 参数

**为什么**：
- 配置和参数关注点分离
- `lit-review config` 子命令可打印当前生效值
- 多组件共用 `Settings`，没有"再读一遍 .env"的散落

**替代**：全 CLI。问题：长度爆炸；运行时改 config 不方便。

## D10. Gradio UI 与 CLI 的关系？

**问题**：两条执行链怎么避免实现重复？

**方案（v0.2）**：
- 引入 `runner.run(state) -> RunResult` 作为**唯一入口**
- CLI 调用 `runner.run()` 后再渲染 console 输出
- UI 调用 `runner.run()` 然后用 Gradio 渲染

**为什么**：
- 之前 UI 直接 import `cli._do_run`（私有函数），耦合严重
- `runner` 既能被 stream 也能被 invoke（同步包装），通用

**替代**：复制一份 `_do_run`。问题：维护地狱，测试时两边都要改。

## D11. 为什么不内置引文图 / 主题聚类？

**问题**：综述作者通常还会想要"研究主题聚类"。

**现状**：不做。

**为什么**：
- 主题聚类（非监督）质量不稳定
- 引文图（要拉 Semantic Scholar 的 `citations` 字段，会 429）
- 都属于"可以做但不值得糊一脸"的特性，先保持轻量

**未来路线**：可作为 v0.4.0 的可选插件，不进入主线。

## D12. 测试策略：离线优先 + 网络标记

**问题**：开发期间不能每次都跑真实 API，但又要保证 source 客户端可用。

**方案**：
- 离线测试：`tests/test_<x>.py` 用最小 fixture 验证解析器 / 去重 / 评分
- 网络测试：`@pytest.mark.network` 标记，默认 CI 跳过
- CI 推荐命令：`pytest -m "not network"`

**替代**：mock 一切。问题：mock 与真实 API 行为分歧会随着版本漂移。

## D13. 在哪里放可观测性？(metrics / structured logs)

**决策**：v0.2 起在每节点用 `timed_node` 上下文管理器计时，把耗时写进
   `__node_times__` 通道（graph builder 的包装器把它带出节点，`runner` 汇总进
   `metrics.json`）；`LLMClient` 内部累加 token usage 和调用次数；CLI
   `--verbose` 通过 `runner.on_node` 逐节点实时打印。
   完整规格见 [OBSERVABILITY.md](./OBSERVABILITY.md)。

## D14. 安全/限流：USER_AGENT 与 mailto

**决策**：所有 HTTP 都带 `USER_AGENT=LiteratureReviewAgent/0.1 (mailto:you@example.com)`。
   OpenAlex/Crossref 解析 `mailto=` 并自动加入参数，进入 polite pool。
   README 显式提示这点。

