# 故障排查

> 给出**症状 → 原因 → 排查 → 解决**四段式清单。

## T1. 启动即报 `LLM_API_KEY is not set`

**症状**：`lit-review review "..."` 立即退出，终端显示红色 `Configuration error`，退出码 2。

**原因**：ReAct Agent 没有确定性骨架降级路径；没有 LLM 就无法规划、调用工具与撰写报告。

**排查**：
```bash
lit-review config   # 看 LLM_API_KEY set 是否为 True
echo $LLM_API_KEY | head -c 4   # 看前缀
```

**解决**：
- 真未设置：复制 `.env.example` → `.env`，填 `LLM_API_KEY`
- Key 错：用 `curl https://<base_url>/v1/models -H "Authorization: Bearer $LLM_API_KEY"` 验证
- 模型不对：在 `.env` 改 `LLM_MODEL`

## T2. 启动即报 `Failed to build the chat model with tool calling support`

**症状**：报 `ConfigurationError`，提示无法构建带 tool calling 的模型。

**原因**：端点不支持原生 function calling（`tool_calls`）。

**解决**：
- 换用支持 `tool_calls` 的 OpenAI 兼容端点（OpenAI、DeepSeek、Qwen、OpenRouter 等）
- 本项目**不提供 JSON 降级**；端点不支持 `tool_calls` 直接报错，这是设计决策。

## T3. OpenAlex 一直报 429 Too Many Requests

**症状**：Agent 返回的搜索工具 JSON 里出现 `openalex` 错误或 WARN 列表里多次 `429`。

**原因**：未进 polite pool；OpenAlex 对匿名 IP 限流严。

**排查**：检查 `USER_AGENT` 是否包含 `mailto=`：
```bash
grep USER_AGENT .env
```
应该看到 `mailto=your@email.com`

**解决**：
- 改 `.env`：`USER_AGENT=LiteratureReviewAgent/0.2 (mailto:your@email.com)`
- 实在不行：暂时禁用 OpenAlex：`--sources arxiv,huggingface`

## T4. `langchain-openai` 报 `Unknown scheme for proxy URL (socks://...)`

**症状**：ImportError / 启动崩溃。

**原因**：`ALL_PROXY=socks://...` 时，httpx 默认会读出来用，而 socks 协议不在 httpx 支持范围。

**解决**：
- 我们默认 `trust_env=False`，应该不会出现这条
- 如果出现：手动清掉 `ALL_PROXY` / `all_proxy`：
  ```bash
  unset ALL_PROXY all_proxy
  ```
- 或用 HTTP proxy：`HTTPS_PROXY=http://proxy:8080`

## T5. Gradio UI 起不来

**症状**：`ModuleNotFoundError: No module named 'gradio'`

**原因**：`gradio` 在 `[ui]` extra 里，没装

**解决**：
```bash
pip install -e ".[ui]"
```

## T6. Semantic Scholar 全部返回 0 个论文

**症状**：Agent 搜索 `semantic_scholar` 返回 0 条，WARN 里出现 `rate-limited (no S2_API_KEY set)`

**原因**：S2 强限流，无 key 时 1 IP 每秒 1 请求

**解决**：
- 获取 free key：[semanticscholar.org/product/api#api-key-form](https://www.semanticscholar.org/product/api#api-key-form)
- 加到 `.env`：`S2_API_KEY=...`
- 或禁用：`--sources arxiv,openalex,huggingface`

## T7. Agent 达到 `max_agent_steps` 后失败

**症状**：终端显示红色 `Agent failed`，退出码 1；`.metrics.json` 里 `max_steps_reached: true`。

**原因**：模型在步数上限内没有调用 `submit_report`（反复搜索、空转或反思循环）。

**排查**：
```bash
lit-review review "..." --verbose --emit-metrics 2>&1 | grep "agent_step"
cat reports/*.metrics.json | jq '.steps, .tool_calls, .max_steps_reached'
```

**解决**：
- 调高 `MAX_AGENT_STEPS`（`.env` 或环境变量，默认 12）
- 调高 `MAX_REFLECTIONS` 会让反思更容易重复；默认 1 已足够
- 用更强的 `LLM_MODEL`（gpt-4o > gpt-4o-mini）提高一次到位的概率

## T8. 报告引用与 References 对不上 / 出现编造引用

**症状**：正文出现 `[12]` 但 References 只有 5 篇，或引用了一篇 `list_papers` 里不存在的论文。

**原因**：模型没有严格遵循「先 `list_papers` 再撰写」的约束，自行编造编号。

**排查**：
```bash
lit-review review "..." --verbose --emit-metrics 2>&1 | grep "list_papers"
cat reports/*.metrics.json | jq '.merged.papers_kept'
```

**解决**：
- 调小 `top_k`（避免 prompt 拥挤）
- 用更强的 `LLM_MODEL`
- 记住：`submit_report` 的参考文献**永远由工具从真实语料生成**，正文引用必须对得上 `list_papers` 的编号

## T9. 报告生成很慢（>2 分钟）

**症状**：每次大约 100s+。

**排查**：
```bash
lit-review review "..." --verbose --emit-metrics
cat reports/*.metrics.json | jq '.steps, .tool_calls, .llm'
```

可能原因：
- Agent 步数太多：模型反复搜索/反思
- 单 source 卡顿：换 `--sources` 组合
- LLM 慢：模型换成 mini 或换 provider

**解决**：
- 必要时把 `REQUEST_TIMEOUT` 调到 15s
- 用 `--resume` 复用历史记忆，减少从头检索

## T10. `lit-review` 命令找不到

**症状**：`bash: lit-review: command not found`

**原因**：未安装

**解决**：
```bash
pip install -e ".[dev,ui]"
```

如果在 conda/venv，确保在正确环境里：
```bash
which lit-review
```
