"""Dedupe + scoring — fully offline."""

from __future__ import annotations

from lit_review.state import Paper
from lit_review.tools.rank import merge_and_rank, _identity_keys, score_and_sort


def _p(title: str, *, doi: str = "", arxiv: str = "", year: int = 2023, citations: int | None = None, abstract: str = "x") -> Paper:
    return Paper(
        title=title,
        authors=["A. Author"],
        year=year,
        abstract=abstract,
        doi=doi,
        arxiv_id=arxiv,
        citation_count=citations,
        source="openalex",
    )


def test_identity_keys_include_doi():
    p = _p("Foo", doi="10.1/abc", arxiv="2401.00001")
    keys = _identity_keys(p)
    assert "doi:10.1/abc" in keys
    assert "arxiv:2401.00001" in keys


def test_identity_keys_include_arxiv_when_no_doi():
    p = _p("Foo", arxiv="2401.00001")
    assert "arxiv:2401.00001" in _identity_keys(p)
    assert not any(k.startswith("doi:") for k in _identity_keys(p))


def test_identity_keys_normalize_title():
    p1 = _p("  Foo: A Study!!! ")
    p2 = _p("foo a study")
    assert _identity_keys(p1) == _identity_keys(p2)


def test_merge_combines_by_doi():
    a = _p("Same Paper", doi="10.1/abc", year=2023, citations=10, abstract="short")
    b = _p("Same Paper — full title", doi="10.1/abc", year=2024, citations=99, abstract="a much longer abstract body here")
    out = merge_and_rank([a, b])
    assert len(out) == 1
    m = out[0]
    assert m.year == 2024
    assert m.citation_count == 99
    assert "longer abstract" in m.abstract
    assert m.source == "merged"


def test_rank_orders_by_score_desc():
    old_unused = _p("old unused", year=2018, citations=0)
    recent = _p("recent cited", year=2024, citations=200)
    mid = _p("mid", year=2022, citations=30)
    out = score_and_sort([old_unused, recent, mid], top_k=3)
    assert [p.title for p in out] == ["recent cited", "mid", "old unused"]
    assert all(out[i].score >= out[i + 1].score for i in range(len(out) - 1))


def test_rank_empty_input():
    assert merge_and_rank([]) == []


def test_rank_top_k_truncates():
    papers = [_p(f"p{i}", year=2023, citations=i) for i in range(10)]
    out = merge_and_rank(papers, top_k=3)
    assert len(out) == 3


def test_merge_prefers_arxiv_url_over_openalex_url():
    """When two sources both have a URL, keep the non-OpenAlex one."""
    arxiv = Paper(
        title="Same Paper",
        arxiv_id="2401.00001",
        url="https://arxiv.org/abs/2401.00001",
        year=2024,
        abstract="abstract from arxiv",
        source="arxiv",
    )
    openalex = Paper(
        title="Same Paper",
        arxiv_id="2401.00001",
        doi="10.1234/xyz",
        url="https://openalex.org/works/W123",
        year=2024,
        abstract="longer abstract from openalex with more detail",
        citation_count=10,
        source="openalex",
    )
    out = merge_and_rank([arxiv, openalex])
    assert len(out) == 1
    m = out[0]
    assert m.canonical_url() == "https://doi.org/10.1234/xyz"
    assert m.citation_count == 10  # richer record kept


def test_score_is_source_agnostic():
    """Two papers identical except for source should score equally."""
    a = Paper(title="x", year=2024, citation_count=10, abstract="y", source="arxiv")
    b = Paper(title="x", year=2024, citation_count=10, abstract="y", source="openalex")
    from lit_review.tools.rank import score_and_sort
    out = score_and_sort([a, b])
    assert abs(out[0].score - out[1].score) < 1e-9
