# LLM 客户端抽象、缓存、重试与工具绑定

文件：
- `src/lit_review/llm.py`            # ChatOpenAI 工厂（任何 OpenAI-compatible 端点）
- `src/lit_review/llm_client.py`     # 缓存 + 重试 + token 统计 + bind_tools/invoke_chat

## 1. 抽象层次

```
ReviewAgent（ReAct 循环）
  └─ LLMClient
       ├─ ChatOpenAI / 其他 BaseChatModel
       ├─ bind_tools(tools)          → 绑定了 TOOLS 的模型
       ├─ invoke_chat(model, messages) → Agent 步调用（不缓存）
       ├─ invoke_json(...)           → 反思子调用（缓存 + fallback）
       ├─ 缓存层 (LRU + 可选 disk)
       ├─ 重试循环（指数退避 + 尊重 Retry-After）
       └─ 用量统计 (counters / metrics.json)
```

## 2. 公共 API

```python
from lit_review.llm_client import LLMClient

client = LLMClient(settings)

# 1) 绑定工具（ReAct 主循环）
bound = client.bind_tools(tools, temperature=0.2)

# 2) Agent 步调用：返回原始 AIMessage（含 tool_calls），不缓存
resp = client.invoke_chat(bound, messages, tag="agent.step.1")

# 3) 反思子调用：结构化 JSON 输出，带缓存与 fallback
data = client.invoke_json(
    system="...JSON schema 提示...",
    user="...",
    tag="reflection.search_coverage",
    fallback={"coverage_gaps": [], "suggested_queries": [], "verdict": "sufficient"},
)
```

### 2.1 `bind_tools`

- 调 `_build_model(temperature)` 构造 `BaseChatModel`，再 `model.bind_tools(list(tools))`
- 无 Key 或绑定失败返回 `None`，由 `ReviewAgent.run` 抛 `ConfigurationError`

### 2.2 `invoke_chat`

- 带重试 / 退避 / token 统计，但**不缓存**（输入是动态增长的消息转录）
- 返回原始响应消息；`ReviewAgent` 自行解析 `tool_calls`

### 2.3 `invoke_json`

- 复用 `invoke_text` 的缓存与重试
- 容错解析 JSON（剥 ```json``` 包裹、找首个 `{...}` 块）
- 失败返回 `fallback` 并记入 `LLMClient.errors`

## 3. 缓存策略

- **进程内 LRU**（默认 256 条）—— 同一进程内立即复用
- **可选磁盘缓存**（`LIT_REVIEW_CACHE_DIR=.cache/llm/`）—— 跨进程复用
- 缓存文件命名：`<sha256>.json`，内容含 `text` / `tokens_in` / `created_at`
- 只有 `invoke_text` / `invoke_json` 走缓存；`invoke_chat` 明确不缓存

### 3.1 何时应清空缓存？

- 改了 `LLM_MODEL`：版本升级 → 必须清
- 改了 prompt 模板：`tag` 改了 → 自动失效（不同 tag 不同 key）
- 想强制重跑：删除 `LIT_REVIEW_CACHE_DIR` 对应目录

## 4. 重试策略

`invoke_text` / `invoke_chat` 内用手写重试循环，最多 `max_attempts=3` 次：

- 可重试错误：超时、连接错误、429、5xx、ServiceUnavailable
- 429 / 限流：尊重 `Retry-After` 头，或从消息 `try again in Ns` 解析退避秒数
- 三次都失败：`invoke_text` 返回 `None`（反思调用返回 `fallback`）；`invoke_chat` 返回 `None`
  （Agent 记录错误并中断）

## 5. 用量统计

`metrics.llm` 由 `LLMClient.snapshot()` 提供：

```json
{
  "calls": 7,
  "cache_hits": 2,
  "tokens_in": 12480,
  "tokens_out": 4200,
  "by_tag": {"agent.step.1": 1, "reflection.search_coverage": 1, "...": "..."},
  "errors": []
}
```

`tokens_in/out` 通过解析 `AIMessage.usage_metadata` 得到。

## 6. 无 Key / 不支持 tool_calls 的处理

- 无 `LLM_API_KEY` → `require_llm()` 抛 `ConfigurationError`，**不降级**
- 端点不支持 `tool_calls` → `bind_tools` 异常 → `ReviewAgent` 抛 `ConfigurationError`

## 7. 自定义 Provider

切换到 Anthropic / Gemini / DeepSeek，无需改 Agent 循环：

1. 改 `src/lit_review/llm.py:build_chat_model()`，按配置返回不同的 `BaseChatModel`
2. 或改 `LLMClient._build_model(temperature)` 的返回对象

只要最终对象是 `BaseChatModel`，缓存、重试、token 统计、`bind_tools` 自动生效。
