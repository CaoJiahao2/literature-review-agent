# Contributing

Thanks for your interest in making **literature-review-agent** better! 🎉

> Need a deeper architectural tour before you start? See
> [`docs/zh/DEVELOPMENT.md`](./docs/zh/DEVELOPMENT.md) (in 中文) and
> [`docs/zh/ARCHITECTURE.md`](./docs/zh/ARCHITECTURE.md).

## Development setup

```bash
git clone https://github.com/CaoJiahao2/literature-review-agent.git
cd literature-review-agent
pip install -e ".[dev,ui]"
cp .env.example .env   # then set LLM_API_KEY
```

Python 3.10+ is required.

## Running tests

```bash
pytest -q                       # full suite (offline + network-marked)
pytest -q -m "not network"      # offline only
```

Network-marked tests (`@pytest.mark.network`) hit arXiv, OpenAlex, Crossref, etc.
They should pass within a few seconds on a normal connection. If you don't have
outbound HTTPS, skip them in CI.

## Adding a new source

A source is a plain Python function with the signature:

```python
def search_<source>(settings, queries, *, max_per_query=None, years=None) -> list[Paper]:
    ...
```

Steps:

1. Drop your implementation in `src/lit_review/tools/<source>.py`.
2. Add an entry to `ALL_SOURCES` and `SOURCE_FNS` in
   `src/lit_review/tools/__init__.py`.
3. *(Optional, recommended)* Drop an async implementation in
   `src/lit_review/tools/<source>_async.py` and call
   `tools.register_async_source("<source>", search_<source>_async)` in
   `tools/__init__.py`. The async runner will pick it up automatically.
4. Add per-source limits to `Settings` (`src/lit_review/config.py`) and
   `.env.example`.
5. Write tests in `tests/test_<source>_tool.py` — both offline (parser tests)
   and `@pytest.mark.network` (live endpoint).
6. Mention the source in `README.md` (Sources table), the Gradio
   `SOURCE_LABELS` in `src/lit_review/ui.py`, and the Chinese guide
   `docs/zh/SOURCES.md`.

## Architecture seam

The codebase has two stable seams for contributors:

* **`lit_review.runner`** — the only execution entry point used by CLI/UI.
  Any orchestration refactor must end up calling `runner.run()`. New CLI
  flags belong here too.
* **`lit_review.llm_client.LLMClient`** — every LLM call goes through this
  facade. Cache hits and per-call errors are tracked automatically; do not
  call `ChatOpenAI` directly from a node.

If your change crosses either seam, update:
* `docs/zh/ARCHITECTURE.md` (high-level diagram + data flow)
* `docs/zh/DESIGN.md` (the corresponding *D<n>* design decision)
* `docs/zh/STATE.md` (only if you add/remove AgentState keys)

## Style

- Python 3.10+ syntax (PEP 604 unions, `match` statements where helpful).
- Type hints everywhere.
- `pydantic` models for any data structure that crosses node boundaries.
- Logging via `logging.getLogger(__name__)`; never `print()` in production code.
- Tests with `pytest`; prefer offline + a small, named network test.
- All new offline-only tests should land in `tests/test_<module>.py`.

## Commit messages

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Semantic Scholar source
fix(rank): prefer non-OpenAlex URL when both sources expose one
docs: surface OpenAlex 429 troubleshooting
chore(deps): bump langchain-openai
```

## Releasing

1. Bump version in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Tag the commit: `git tag -a v0.2.0 -m "v0.2.0"`.
4. Push the tag: `git push origin main --tags`.

## Code of conduct

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/).
Be kind, be respectful.
