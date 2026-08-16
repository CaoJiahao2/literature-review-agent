# LLM 客户端抽象、缓存、重试与降级

文件：
- `src/lit_review/llm.py`            # 原有 ChatOpenAI 工厂（保留兼容）
- `src/lit_review/llm_client.py`     # 新增：缓存 + 重试 + 用量统计（v0.2）

## 1. 抽象层次

```
Runner
  └─ LLMClient (v0.2 新增)
       ├─ ChatOpenAI / 其他 BaseChatModel
       ├─ 缓存层 (LRU + 可选 disk)
       ├─ 重试循环（指数退避 + 尊重 Retry-After）
       └─ 用量统计 (counters / metrics.json)
```

为何要这层封装？
- LangGraph 节点代码不应该操心"网络抖动是否需要重试"
- 缓存可以显著降低重复迭代的成本（refine 节点有时会触发相同 prompt）
- 用量统计是 LLM 服务的账单依据

## 2. LLMClient 的公共 API

```python
from lit_review.llm_client import LLMClient

llm = LLMClient(settings)
text = llm.invoke_text(
    system="...目标格式...",
    user="...实际内容...",
    tag="plan",              # 用作 cache key + 计数器名
    temperature=0.2,
)

# 结构化输出（v0.2.1+）：
data = llm.invoke_json(
    system="...JSON schema 提示...",
    user="...",
    tag="refine.search",
    fallback={"queries": []},   # 解析失败时的兜底
)
```

### 2.1 `invoke_text`

- 入参：`system: str`, `user: str`, `tag: str`, `temperature: float=0.2`
- 出参：`str`
- 行为：
  1. 计算缓存键 `(tag, system, user, temperature)` 的 sha256
  2. 命中 → 返回缓存，metrics.cache_hits += 1
  3. 未命中 → 调 BaseChatModel；最多 3 次重试（exponential backoff）
  4. 成功 → 写入缓存；metrics.calls += 1；metrics.tokens_in/out += ...

### 2.2 `invoke_json`

- 入参：同上 + `fallback: dict`
- 出参：`dict`
- 行为：
  1. 同 invoke_text
  2. 解析 JSON（容错：剥 ```json``` 包裹、找首个 `{...}` 块）
  3. 失败 → 返回 `fallback`；失败本身会记入 `LLMClient.errors`

## 3. 缓存策略

- **进程内 LRU**（默认 256 条）—— 同一 Python 进程内立即复用
- **可选磁盘缓存**（`LIT_REVIEW_CACHE_DIR=.cache/llm/`）—— 跨进程复用
- 缓存文件命名：`<sha256>.json`，内容包含 `text` / `tokens_in` / `created_at`
- 命中次数（`cache_hits`）与总调用次数（`calls`）会写入 metrics.json，可自行计算命中率

### 3.1 何时应清空缓存？

- 改了 `LLM_MODEL`：版本升级 → 必须清
- 改了 prompt 模板：`tag` 改了 → 自动失效（不同 tag 不同 key）
- 想强制重跑：`rm -rf .cache/llm/`

## 4. 重试策略

`invoke_text` 内部用**手写重试循环**（不依赖 tenacity），最多 `max_attempts=3` 次：

```python
for attempt in range(max_attempts):
    try:
        resp = model.invoke(messages)
        ...
        return text
    except Exception as exc:
        if not _is_retryable(exc):      # 非临时错误 → 直接放弃
            break
        sleep_for = _retry_after_seconds(exc) or 1.5 * (attempt + 1)
        sleep_for = min(sleep_for, 30.0)
        time.sleep(sleep_for)           # 指数退避（上限 30s）
```

429 / 限流单独识别：
- 命中 `Retry-After` 响应头，或消息里 `try again in Ns` → 按 N 秒退避
- 可重试错误包括：超时、连接错误、429、5xx、ServiceUnavailable
- 三次都失败 → `invoke_text` **返回 `None`**，由调用节点回退到骨架（不抛异常）

## 5. 用量统计

```python
metrics.llm  # 输出到 metrics.json
{
  "calls": int,
  "cache_hits": int,
  "tokens_in": int,
  "tokens_out": int,
  "by_tag": {"plan": int, "refine.search": int, "synthesize.background": int, ...}
}
```

`tokens_in/out` 通过解析 `AIMessage.usage_metadata`（langchain>=0.3 默认带）得到。

## 6. 降级路径

```
LLMClient.invoke_text
  ├─ 缓存命中       → 直接返回
  ├─ 调用成功       → 写缓存 + 计数
  ├─ 网络异常/超时  → 重试
  ├─ 429             → sleep → 重试
  └─ 全部失败        → 返回 None
        │
        ▼ (节点判断空值)
   skeleton_body(spec, papers)            # 全字段写入 state.sections
```

## 7. 自定义 Provider

要切到 Anthropic / Gemini / DeepSeek，**不需要改**其他节点代码。扩展点有两处：

1. **改 `src/lit_review/llm.py:build_chat_model()`**——默认工厂只构造
   `ChatOpenAI`（任意 OpenAI 兼容端点），可以在里面按配置返回不同的
   `BaseChatModel`（如 `ChatAnthropic` / `ChatGoogleGenerativeAI`）。
2. **改 `LLMClient._build_model(temperature)`**——该方法在 `has_llm()` 为真时
   调用工厂；替换它的返回对象即可，无需动 `invoke_text` / `invoke_json`。

只要最终对象是 `BaseChatModel`，缓存、重试、token 统计自动生效。

