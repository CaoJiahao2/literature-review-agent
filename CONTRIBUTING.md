# Contributing

Thanks for your interest in making **literature-review-agent** better! 🎉

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

Network-marked tests (`@pytest.mark.network`) hit arXiv, OpenAlex, Crossref, etc. They should pass within a few seconds on a normal connection. If you don't have outbound HTTPS, skip them in CI.

## Adding a new source

A source is a plain Python function with the signature:

```python
def search_<source>(settings, queries, *, max_per_query=None, years=None) -> list[Paper]:
    ...
```

Steps:

1. Drop your implementation in `src/lit_review/tools/<source>.py`.
2. Add an entry to `ALL_SOURCES` and `SOURCE_FNS` in `src/lit_review/tools/__init__.py`.
3. Add per-source limits to `Settings` (`src/lit_review/config.py`) and `.env.example`.
4. Write tests in `tests/test_<source>_tool.py` — both offline (parser tests) and `@pytest.mark.network` (live endpoint).
5. Mention the source in `README.md` (Sources table) and the Gradio `SOURCE_LABELS` in `src/lit_review/ui.py`.

## Style

- Python 3.10+ syntax (PEP 604 unions, `match` statements where helpful).
- Type hints everywhere.
- `pydantic` models for any data structure that crosses node boundaries.
- Logging via `logging.getLogger(__name__)`; never `print()` in production code.
- Tests with `pytest`; prefer offline + a small, named network test.

## Commit messages

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Semantic Scholar source
fix(rank): prefer non-OpenAlex URL when both sources expose one
docs: surface OpenAlex 429 troubleshooting
chore(deps): bump langgraph to 1.2.x
```

## Releasing

1. Bump version in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Tag the commit: `git tag -a v0.2.0 -m "v0.2.0"`.
4. Push the tag: `git push origin main --tags`.

## Code of conduct

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/). Be kind, be respectful.
