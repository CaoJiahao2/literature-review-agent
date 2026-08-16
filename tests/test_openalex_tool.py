"""OpenAlex tool tests."""

from __future__ import annotations

import pytest

from lit_review.config import Settings
from lit_review.tools.openalex import _parse_work, _reconstruct_abstract, search_openalex


def test_reconstruct_abstract_basic():
    inv = {"hello": [0], "world": [1]}
    assert _reconstruct_abstract(inv) == "hello world"


def test_reconstruct_abstract_empty():
    assert _reconstruct_abstract(None) == ""
    assert _reconstruct_abstract({}) == ""
    assert _reconstruct_abstract({"foo": "bar"}) == ""


def test_parse_work_extracts_core_fields():
    work = {
        "id": "https://openalex.org/W123",
        "title": "  Attention is all you need  ",
        "publication_year": 2017,
        "authorships": [{"author": {"display_name": "A. Vaswani"}}, {"author": {"display_name": "N. Shazeer"}}],
        "primary_location": {"source": {"display_name": "NeurIPS"}},
        "doi": "https://doi.org/10.5555/test",
        "ids": {},
        "locations": [],
        "cited_by_count": 999,
        "abstract_inverted_index": {"attention": [0], "transformer": [2]},
    }
    p = _parse_work(work, year_filter=None)
    assert p is not None
    assert p.title == "Attention is all you need"
    assert p.year == 2017
    assert p.authors == ["A. Vaswani", "N. Shazeer"]
    assert p.venue == "NeurIPS"
    assert p.doi == "10.5555/test"
    assert p.citation_count == 999
    assert "attention" in p.abstract


def test_parse_work_drops_outside_year_filter():
    work = {"title": "Foo", "publication_year": 2010, "authorships": [], "primary_location": {}, "doi": "", "ids": {}, "locations": [], "cited_by_count": 0, "abstract_inverted_index": {}}
    assert _parse_work(work, year_filter=(2020, 2025)) is None


def test_parse_work_extracts_arxiv_id_from_locations():
    work = {
        "title": "X",
        "publication_year": 2023,
        "authorships": [],
        "primary_location": {},
        "doi": "",
        "ids": {},
        "locations": [{"external_id": "https://arxiv.org/abs/2301.12345v2"}],
        "cited_by_count": 0,
        "abstract_inverted_index": {},
    }
    p = _parse_work(work, year_filter=None)
    assert p is not None
    assert p.arxiv_id == "2301.12345"


@pytest.mark.network
def test_search_openalex_live(settings: Settings):
    papers = search_openalex(settings, ["attention is all you need"], max_per_query=3, years=(2017, 2018))
    assert len(papers) >= 1
    p = papers[0]
    assert p.title
    assert p.abstract  # reconstructed from inverted index
    assert p.year and 2017 <= p.year <= 2018
