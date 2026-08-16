"""HuggingFace Daily Papers tool tests."""

from __future__ import annotations

import pytest

from lit_review.config import Settings
from lit_review.tools.huggingface import _parse, search_huggingface
from lit_review.state import Paper


def _row(**kw) -> dict:
    base = {
        "id": "2401.00001",
        "title": "Some HF Paper",
        "thumbnailUrl": "",
        "upvotes": 5,
        "publishedAt": "2024-01-15T00:00:00.000Z",
        "authors": [{"_id": "a1", "name": "Alice", "hidden": False}],
        "summary": "Full abstract body.",
        "ai_summary": "TLDR.",
    }
    base.update(kw)
    return base


def test_parse_basic():
    p = _parse(_row(), year_filter=None)
    assert p is not None
    assert p.title == "Some HF Paper"
    assert p.year == 2024
    assert p.arxiv_id == "2401.00001"
    assert p.authors == ["Alice"]
    assert p.abstract.startswith("Full abstract")  # prefers summary over ai_summary
    assert p.citation_count == 5
    assert p.source == "huggingface"


def test_parse_falls_back_to_ai_summary():
    p = _parse(_row(summary=""), year_filter=None)
    assert p is not None
    assert p.abstract == "TLDR."


def test_parse_year_filter():
    assert _parse(_row(publishedAt="2020-01-01T00:00:00.000Z"), year_filter=(2022, 2024)) is None


def test_parse_skips_empty_title():
    assert _parse(_row(title=""), year_filter=None) is None


def test_parse_keeps_empty_arxiv_id():
    """_parse itself doesn't require arxiv_id; the dedupe gate in search_huggingface does."""
    p = _parse(_row(id=""), year_filter=None)
    assert p is not None
    assert p.arxiv_id == ""


@pytest.mark.network
def test_search_huggingface_live(settings: Settings):
    # Look back only 3 days for a fast + reliable hit.
    papers = search_huggingface(
        settings,
        ["diffusion"],  # substring filter on title/abstract
        max_per_query=5,
        years=(2024, 2026),
        lookback_days=3,
    )
    # We don't insist on >0 because HF rotates their trending list daily;
    # the structure assertion is what matters.
    if not papers:
        pytest.skip("HF daily list did not include 'diffusion' today")
    p = papers[0]
    assert isinstance(p, Paper)
    assert p.arxiv_id
    assert p.title
