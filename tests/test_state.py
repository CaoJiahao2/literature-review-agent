"""State model tests."""

from __future__ import annotations

from lit_review.state import Paper


def test_paper_short_id_prefers_doi():
    p = Paper(title="T", doi="10.1/ABC", arxiv_id="2401.00001")
    assert p.short_id() == "doi:10.1/abc"


def test_paper_short_id_uses_arxiv_fallback():
    p = Paper(title="T", arxiv_id="2401.00001")
    assert p.short_id() == "arxiv:2401.00001"


def test_paper_short_id_normalizes_title():
    p = Paper(title="  Hello,  World!!  ")
    assert p.short_id().startswith("title:hello world")


def test_paper_display_ref_truncates_authors():
    p = Paper(
        title="Big Paper",
        authors=["A", "B", "C", "D", "E"],
        year=2023,
        venue="NeurIPS",
        url="https://example.com/x",
        source="openalex",
    )
    s = p.display_ref(7)
    assert s.startswith("[7] A, B, C et al.")
    assert "(2023)" in s
    assert "Big Paper" in s
    assert "NeurIPS" in s
    assert "https://example.com/x" in s
    assert "_openalex_" in s  # source tag


def test_canonical_url_prefers_doi():
    p = Paper(title="x", doi="10.1/abc", arxiv_id="2401.00001", url="https://openalex.org/works/W1")
    assert p.canonical_url() == "https://doi.org/10.1/abc"


def test_canonical_url_uses_arxiv_when_no_doi():
    p = Paper(title="x", arxiv_id="2401.00001", url="https://openalex.org/works/W1")
    assert p.canonical_url() == "https://arxiv.org/abs/2401.00001"


def test_canonical_url_skips_openalex_when_no_doi_no_arxiv():
    p = Paper(title="x", url="https://openalex.org/works/W1")
    assert p.canonical_url() == ""  # openalex URLs are useless for readers


def test_canonical_url_keeps_non_openalex_when_no_doi_no_arxiv():
    p = Paper(title="x", url="https://example.com/paper")
    assert p.canonical_url() == "https://example.com/paper"
