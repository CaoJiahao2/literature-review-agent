# 数据源接口契约与扩展指南

> 任何人都能加新数据源。本文档定义接口契约、注册位置和测试模板。

## 1. 接口契约

每个数据源是一个可调用对象：

```python
SearchFn = Callable[
    [Settings, Iterable[str]],
    list[Paper],
]

# 展开：
def search_<name>(
    settings: Settings,
    queries: Iterable[str],
    *,
    max_per_query: Optional[int] = None,
    years: Optional[tuple[int, int]] = None,
    # 工具特有参数通过 kw-only 传入
) -> list[Paper]: ...
```

**规则**：

1. 输入：`settings`、`queries`（可迭代 str），可选 `max_per_query`、`years`
2. 输出：`list[Paper]`，允许为空；空时仍应在 state 上登记 0 命中
3. **绝不抛异常**：HTTP / 解析错误要捕获并 log warning，**返回空列表**；
   ReAct 工具的包装层（`agent/tools.py`）会把异常聚到 `state.errors`
4. **去重**：源内已查到的论文要去重（用 `arxiv_id`/`doi` 缓存 `seen` 集合）
5. **年份过滤**：在客户端做（拿到结果后立刻丢），不依赖远端

## 2. 注册位置

数据源客户端注册在 `src/lit_review/tools/__init__.py`：

```python
from .<new_source> import search_<new_source>

ALL_SOURCES = (
    "arxiv",
    "openalex",
    "huggingface",
    "semantic_scholar",
    "crossref",
    "<new_source>",          # 1. 添加到元组
)

SOURCE_FNS = {
    "arxiv": search_arxiv,
    ...
    "<new_source>": search_<new_source>,    # 2. 注册函数
}
```

**作为 ReAct 工具暴露给 Agent**：在 `src/lit_review/agent/tools.py` 的
`build_tools()` 里加一行包装，复用 `_make_search_tool`：

```python
tools: list[StructuredTool] = [
    _make_search_tool(runtime, "search_arxiv", "arxiv", search_arxiv),
    ...
    _make_search_tool(runtime, "search_<new_source>", "<new_source>", search_<new_source>),
]
```

`_make_search_tool` 会生成统一的 function-calling schema
`(query: str, max_results: int, years: list[int] | None) -> str`，
并把返回的 `Paper` 记录进 `AgentState.papers`（按 `short_id` 去重）。

也必须更新：

- `src/lit_review/config.py` 添加 `<source>_max_per_query` 字段
- `.env.example` 加 `<SOURCE>_MAX_PER_QUERY=...`
- `src/lit_review/ui.py:SOURCE_LABELS` 加面向用户的中文描述
- `README.md` 数据源表加一行
- `docs/zh/SOURCES.md`（本文件）加一个章节

## 3. Async 接口（v0.2+）

新数据源优先实现 async 版本：

```python
async def search_<name>_async(
    client: httpx.AsyncClient,
    settings: Settings,
    queries: Iterable[str],
    *,
    max_per_query: int,
    years: Optional[tuple[int, int]],
) -> list[Paper]: ...
```

在 `tools/__init__.py` 里调用注册函数（注册表本身在
`tools/async_runner.py:ASYNC_SOURCE_FNS`）：

```python
from .<name>_async import search_<name>_async
_async_runner.register_async_source("<name>", search_<name>_async)
```

> 注意：当前 ReAct Agent 的搜索工具走同步客户端（每个查询一次调用），
> async 注册表保留给 `tools.run_sources_async` 与后续并发搜索优化使用。
> 未实现 async 的源自动回退到 `asyncio.to_thread`（调用同步版）。
> async 路径的每查询返回上限来自 `Settings` 的 `<source>_max_per_query`
> 字段（`async_runner._cap_from_settings`），与同步路径一致，新增源时记得
> 在 `config.py` 加对应字段。

## 4. 必须的测试

每个数据源在 `tests/test_<source>_tool.py` 提供两类测试：

### 4.1 离线解析器测试（建议）

```python
def test_parse_xxx_basic():
    raw = {...}   # 1-2 条最简最小样本
    p = _parse(raw, year_filter=None)
    assert p.title == "..."
    ...
```

### 4.2 网络端到端测试（标记 network）

```python
@pytest.mark.network
def test_search_xxx_live(settings):
    out = search_<name>(settings, ["RAG"], max_per_query=5)
    assert len(out) > 0
    assert out[0].title
```

CI 推荐：`pytest -m "not network"` 跑默认。
ReAct 工具层的离线测试见 `tests/test_tools.py`（用 `respx` mock HTTP）。
