# 故障排查

> 给出**症状 → 原因 → 排查 → 解决**四段式清单。

## T1. 报告只剩骨架，每个章节都是"占位"

**症状**：报告里只有 `_No background section was generated. ...**Relevant papers from the corpus:** ..._`

**原因**：
1. `LLM_API_KEY` 未设置
2. Key 设置错误（如 base_url 写错）
3. 模型被网络拦截

**排查**：
```bash
lit-review config   # 看 has_llm 是否为 True
echo $LLM_API_KEY | head -c 4   # 看前缀
```

**解决**：
- 真未设置：复制 `.env.example` → `.env`，填 `LLM_API_KEY`
- Key 错：用 `curl https://<base_url>/v1/models -H "Authorization: Bearer $LLM_API_KEY"` 验证
- 模型不对：在 `.env` 改 `LLM_MODEL`

## T2. OpenAlex 一直报 429 Too Many Requests

**症状**：WARN 列表里出现多次 `openalex: HTTPError 429`

**原因**：未进 polite pool；OpenAlex 对匿名 IP 限流严

**排查**：检查 `USER_AGENT` 是否包含 `mailto=`：
```bash
grep USER_AGENT .env
```
应该看到 `mailto=your@email.com`

**解决**：
- 改 `.env`：`USER_AGENT=LiteratureReviewAgent/0.1 (mailto:your@email.com)`
- 实在不行：暂时禁用 OpenAlex：`--sources arxiv,huggingface`

## T3. `langchain-openai` 报 `Unknown scheme for proxy URL (socks://...)`

**症状**：ImportError / 启动崩溃

**原因**：`ALL_PROXY=socks://...` 时，httpx 默认会读出来用，而 socks 协议不在 httpx 支持范围

**解决**：
- 我们默认 `trust_env=False`，应该不会出现这条
- 如果出现：手动清掉 `ALL_PROXY` / `all_proxy`：
  ```bash
  unset ALL_PROXY all_proxy
  ```
- 或用 HTTP proxy：`HTTPS_PROXY=http://proxy:8080`

## T4. Gradio UI 起不来

**症状**：`ModuleNotFoundError: No module named 'gradio'`

**原因**：`gradio` 在 `[ui]` extra 里，没装

**解决**：
```bash
pip install -e ".[ui]"
```

## T5. Semantic Scholar 全部返回 0 个论文

**症状**：`source 'semantic_scholar' returned 0 results` + 一条 `rate-limited (no S2_API_KEY set)`

**原因**：S2 强限流，无 key 时 1 IP 每秒 1 请求

**解决**：
- 获取 free key：[semanticscholar.org/product/api#api-key-form](https://www.semanticscholar.org/product/api#api-key-form)
- 加到 `.env`：`S2_API_KEY=...`
- 或禁用：`--sources arxiv,openalex,huggingface`

## T6. 报告章节质量很差，引用错位

**症状**：正文里出现 "[2]" 但 References 里只有 5 篇

**原因**：
1. LLM 的引用编号与 `merged` 列表顺序不一致
2. 实际可被引用数 < LLM 期望数

**排查**：
```bash
lit-review review "..." --verbose 2>&1 | grep "synthesize_sections"
```
看 `papers_kept` 是多少；与 LLM prompt 中的论文数对比

**解决**：
- 调小 `top_k`（避免 prompt 拥挤）
- 用更强的 `LLM_MODEL`（gpt-4o > gpt-4o-mini）

## T7. refine 节点没有触发，结果很烂

**症状**：首次拉到的论文 < 5 篇，但 refine 没启动

**原因**：`should_refine` 用了 `iteration < max_iter`，`iteration` 在 plan 节点已经 +1

**排查**：看 `metrics.json`：
```json
"iteration": 1,
"max_iter": 2
```
如果 `iteration > max_iter`，下一轮不会触发

**解决**：
- `--max-iter 3`
- 或在 `Settings` 调高默认值

## T8. 报告生成很慢（>2 分钟）

**症状**：每次大约 100s+

**排查**：
```bash
lit-review review "..." --verbose --emit-metrics
cat reports/*.metrics.json | jq '.nodes'
```

可能原因：
- 单 source 卡顿：换 `--sources` 组合
- LLM 慢：模型换成 mini 或换 provider
- 大量联网重试：`--no-llm` 试一下

**解决**：
- v0.2 引入异步并发：默认 4 source 同时跑
- 必要时把 `request_timeout` 调到 15s

## T9. `lit-review` 命令找不到

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

