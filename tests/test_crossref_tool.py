"""Crossref tool tests."""

from __future__ import annotations

import pytest

from lit_review.config import Settings
from lit_review.tools.crossref import _parse, search_crossref
from lit_review.state import Paper


def _item(**kw) -> dict:
    base = {
        "DOI": "10.5555/test",
        "title": ["A Crossref Paper"],
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "issued": {"date-parts": [[2018, 5, 1]]},
        "container-title": ["Journal of Testing"],
        "URL": "https://example.org/paper",
        "is-referenced-by-count": 42,
        "abstract": "We did the thing.",
        "link": [],
    }
    base.update(kw)
    return base


def test_parse_basic():
    p = _parse(_item(), year_filter=None)
    assert p is not None
    assert p.title == "A Crossref Paper"
    assert p.year == 2018
    assert p.authors == ["Ada Lovelace"]
    assert p.venue == "Journal of Testing"
    assert p.doi == "10.5555/test"
    assert p.citation_count == 42
    assert p.source == "crossref"


def test_parse_strips_jats_tags():
    p = _parse(_item(abstract="<jats:p>Hello</jats:p> <jats:p>world</jats:p>"), year_filter=None)
    assert p is not None
    assert p.abstract == "Hello world"


def test_parse_extracts_arxiv_from_links():
    p = _parse(_item(link=[{"URL": "https://arxiv.org/abs/2301.12345"}]), year_filter=None)
    assert p is not None
    assert p.arxiv_id == "2301.12345"


def test_parse_year_filter_drops():
    assert _parse(_item(issued={"date-parts": [[2010, 1]]}), year_filter=(2020, 2025)) is None


def test_parse_skips_empty_title():
    assert _parse(_item(title=[]), year_filter=None) is None


@pytest.mark.network
def test_search_crossref_live(settings: Settings):
    papers = search_crossref(settings, ["attention is all you need"], max_per_query=2, years=(2017, 2018))
    assert papers, "expected at least one Crossref hit"
    p = papers[0]
    assert p.title
    assert p.doi or p.url
