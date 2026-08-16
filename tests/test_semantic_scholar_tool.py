"""Semantic Scholar tool tests."""

from __future__ import annotations

import pytest

from lit_review.config import Settings
from lit_review.tools.semantic_scholar import _parse, search_semantic_scholar
from lit_review.state import Paper


def _paper_dict(**kw) -> dict:
    base = {
        "paperId": "abc123",
        "title": "Attention is all you need",
        "abstract": "We propose a new architecture.",
        "year": 2017,
        "authors": [{"name": "Vaswani"}, {"name": "Shazeer"}],
        "venue": "NeurIPS",
        "externalIds": {"DOI": "10.5555/test", "ArXiv": "1706.03762"},
        "url": "https://www.semanticscholar.org/paper/abc123",
        "citationCount": 999,
    }
    base.update(kw)
    return base


def test_parse_basic():
    p = _parse(_paper_dict(), year_filter=None)
    assert p is not None
    assert p.title == "Attention is all you need"
    assert p.year == 2017
    assert p.authors == ["Vaswani", "Shazeer"]
    assert p.venue == "NeurIPS"
    assert p.doi == "10.5555/test"
    assert p.arxiv_id == "1706.03762"
    assert p.citation_count == 999
    assert p.source == "semantic_scholar"


def test_parse_year_filter_drops():
    assert _parse(_paper_dict(year=2010), year_filter=(2020, 2025)) is None


def test_parse_skips_empty_title():
    assert _parse(_paper_dict(title=""), year_filter=None) is None


@pytest.mark.network
def test_search_semantic_scholar_live(settings: Settings):
    papers = search_semantic_scholar(settings, ["attention is all you need"], max_per_query=2, years=(2017, 2018))
    if not papers:
        pytest.skip("S2 rate-limited (no API key) — expected without S2_API_KEY")
    p = papers[0]
    assert p.title
    assert p.year and 2017 <= p.year <= 2018
