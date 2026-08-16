# 开发约定与测试策略

> 写给"接下来要碰这个项目的人"。

## 1. 开发环境

```bash
git clone https://github.com/CaoJiahao2/literature-review-agent.git
cd literature-review-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ui]"
cp .env.example .env  # 编辑 LLM_API_KEY 等
```

## 2. 代码风格

- Python 3.10+
- 类型提示必填；尽量用 `from __future__ import annotations`
- `print()` 仅用于 CLI 交互输出；生产路径一律 `logging.getLogger(__name__)`
- pydantic 模型用于所有跨节点数据结构
- 私有 helper 用下划线前缀（`_foo`）

## 3. 目录结构

```
src/lit_review/
├── __init__.py
├── __main__.py
├── cli.py              # Typer CLI
├── ui.py               # Gradio Blocks
├── runner.py           # 新增：唯一执行入口
├── config.py           # Settings（.env）
├── state.py            # Paper / GraphState / SearchPlan
├── llm.py              # ChatOpenAI 工厂
├── llm_client.py       # 新增：缓存 + 重试 + 计量
├── graph/
│   ├── __init__.py
│   ├── builder.py      # StateGraph 装配
│   ├── nodes.py        # 所有节点函数
│   └── edges.py        # 条件边
├── tools/
│   ├── __init__.py     # ALL_SOURCES / SOURCE_FNS / run_sources / run_sources_async
│   ├── async_runner.py # 新增：async fan-out
│   ├── _http.py
│   ├── arxiv.py
│   ├── openalex.py
│   ├── huggingface.py
│   ├── semantic_scholar.py
│   ├── crossref.py
│   └── rank.py
└── report/
    ├── __init__.py
    ├── template.py     # SectionSpec + render_report
    └── writer.py       # write_report
```

## 4. 测试策略

### 4.1 三层测试

| 层 | 文件 | 慢？ | 外部依赖 |
|---|---|---|---|
| Unit | `tests/test_state.py` / `test_rank.py` | 毫秒 | 无 |
| Component | `tests/test_graph_smoke.py` | 秒级 | (可选 mock source) |
| Network | 各 `test_<source>_tool.py`（`@pytest.mark.network`） | 数秒 | 真实 HTTP |

### 4.2 离线测试：默认 CI 必跑

```bash
pytest -m "not network" -q
```

应在 < 1s 完成。

### 4.3 网络测试：开发者自验

```bash
pytest -m "network" -q          # 全跑
pytest tests/test_arxiv_tool.py -m "network" -q    # 单源
```

CI 上加 `pytest -m "not network"` 就够了。

### 4.4 增加新节点的测试模板

```python
def test_my_node_smoke(settings, monkeypatch):
    state = GraphState(topic="X", language="en", top_k=5, max_iter=1, no_llm=True)
    monkeypatch.setattr("lit_review.llm.build_chat_model", lambda *a, **kw: None)
    out = my_node(state, settings)
    assert "expected_key" in out
```

## 5. 提交流程

1. 在 feature branch 上开发：`git checkout -b feat/<name>`
2. 加测试；保证离线测试通过
3. 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)：
   ```
   feat(runner): add async source fan-out
   fix(llm_client): respect retry-after on HTTP 429
   docs(zh): update architecture for async runner
   ```
4. 开 PR 触发 CI；若改了 source 接口或 state 字段，要在 description 中明确

## 6. 发布

1. `pyproject.toml` 升版本号（semver）
2. 在 `CHANGELOG.md` `[Unreleased]` 加条目
3. 标签 `vX.Y.Z` 推到 origin
4. （可选）PyPI：`python -m build && python -m twine upload dist/*`

## 7. 公共 API 兼容性约定

- `Paper` 字段只增不删；删除前先标记 `deprecated`
- `GraphState` keys 同样只增；被取代的 key 至少保留 1 个 minor 版本
- 公开函数（无下划线前缀）签名只增参数、不改语义
- 内部重构可自由

