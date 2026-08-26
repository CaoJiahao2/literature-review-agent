# 去重与评分算法详解

文件：`src/lit_review/tools/rank.py`

## 1. 去重

### 1.1 思路：等价类（union-find）

每个 `Paper` 用一组 *identity key* 描述：

```
doi:<lowercase-doi>       (if doi)
arxiv:<lowercase-id>      (if arxiv_id)
title:<normalized-title>  (always)
```

归一化的标题：
```python
_norm_title(t) = collapse_whitespace(strip_non_alnum(lowercase(t)))
```
例： `"Foo: A Study!!! "` → `"foo a study"`

合并策略：
- 任意两个 Paper **只要有一个 identity key 重叠，就放进同一 cluster**
- 这是 union-find 的"路径压扁"近似实现，用 `dict[key -> cluster_idx]`

### 1.2 为什么用 union 而不是优先级？

| 数据特征 | 仅靠 doi | 仅靠 arxiv_id | 仅靠 title | 我们的 union |
|---|---|---|---|---|
| arXiv-only 论文 | ✗ 无 doi | ✓ 匹配 | ✓ 标题漂移 | ✓ (arxiv_id) |
| OpenAlex-only 论文 | ✓ 匹配 | ✗ 无 arxiv_id | ✓ | ✓ (doi) |
| 同一标题的巧合论文 | — | — | ✓ 误合并 | ✓ (其他键区分) |
| DOI 格式不一（大小写、前缀） | — | — | — | ✓ 归一化 |

权衡：标题归一化可能误合并两个完全同名的文章，但这种概率远低于"丢一篇真论文"的概率。
再做兜底：cluster 内不止一个 Paper 时，会用 `_merge_two` 字段合并而不是简单替换。

### 1.3 字段合并规则

`_merge_two(a, b)`：

- **保留**更"富裕"的记录（abstract 长度 + 元数据完备度评分）
- 对方缺的字段从另一方补齐
- 引用数取 `max(a, b)`
- URL：优先 DOI/arxiv canonical；同源时优先非 OpenAlex
- authors / categories：取并集去重

## 2. 评分

### 2.1 公式

```
score = 0.5 * nc + 0.3 * ny + 0.2 * nr
```

| 维度 | 公式 | 范围 |
|---|---|---|
| `nc` (citation) | `log1p(cites) / log1p(max_cites_in_corpus)` | [0, 1] |
| `ny` (recency) | `(year - min_year) / (max_year - min_year)` | [0, 1] |
| `nr` (abstract_richness) | `min(1, len(abstract) / 1500)` | [0, 1] |

`min_year`/`max_year` 是当前 corpus 的极值，所以公式是**相对量**，与全局尺度无关。

### 2.2 为什么 log + 比 max？

- 一篇 100 引用和一篇 1000 引用的差距不应是 10×，应是 √10x
- 比 `log1p(max_cites)`：让空 corpus 不除零；让 corpus 中最被引用的论文 = 1.0

### 2.3 可调点

- 想要更"考古"风格：把 `0.3 recency` 调到 `0.2`，citation 调到 `0.6`
- 想要更"前沿"风格：把 recency 调到 `0.4`，abstract_richness 调到 `0.15`
- 想惩罚无摘要的论文：`abstract_richness` 调到 `0.3`，citation 调到 `0.4`

修改 `src/lit_review/tools/rank.py:score_and_sort` 中的 `p.score = ...` 一行即可。
新增特征（如 venue 质量、source diversity）作为第二阶段，参考 [STATE.md §6](./STATE.md)。

### 2.4 Top-K 截断

`merge_and_rank(papers, top_k=top_k)` 在 `score_and_sort` 之后做 `merged[:top_k]`。
在 ReAct 架构下，Top-K 截断发生在两个地方：

- `submit_report`：用 `AgentState.papers` 做 `merge_and_rank(..., top_k=settings.top_k)`，
  生成最终写入 References 的语料与 `state["merged"]`。
- `list_papers`：用 `merge_and_rank(..., top_k=None)` 返回当前**去重排序后**的完整编号列表，
  供 Agent 在撰写前对齐 `[#]` 引用编号。

实测发现 top_k=30 是"综述能看完所有引用"的上限；>50 就会让 Agent 的消息转录
（含论文清单 + 草稿）超过 16k tokens。

## 3. 章节相关性

ReAct Agent 中不再有硬编码的 `_papers_for_section` 切片。章节与论文的相关性由
LLM 自主决定：Agent 在 `system prompt` 的约束下，先 `list_papers` 拿到带编号的
参考文献列表，再逐节撰写，并按要求使用 `[#]` 引用编号。

**已计划 (v0.3)**：在 `list_papers` / `submit_report` 阶段用 BM25 on
`title + abstract` 为每个章节预选相关性最高的论文，缩小 Agent 撰写时的引用候选集。

参数（旧确定性骨架遗留，现无运行时作用）：
```python
offset = {
    "background": 0,
    "methods": 2,
    "datasets": 4,
    "trends": 6,
    "open_problems": 8,
}.get(name, 0)
```
