"""arXiv tool tests. Live API tests are marked `network`."""

from __future__ import annotations

import pytest

from lit_review.config import Settings
from lit_review.tools.arxiv import _parse_entry, _to_query, search_arxiv
from lit_review.state import Paper


def test_to_query_wraps_in_all():
    assert _to_query("transformer attention") == 'all:"transformer attention"'


def test_to_query_passthrough_when_already_structured():
    assert _to_query('ti:"foo"') == 'ti:"foo"'


def test_to_query_empty_returns_empty():
    assert _to_query("   ") == ""


def test_parse_entry_basic_xml():
    import xml.etree.ElementTree as ET

    xml = """<?xml version='1.0'?>
    <entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <id>http://arxiv.org/abs/2401.01234v1</id>
      <title>  A  Test  Paper </title>
      <summary>  An abstract.  </summary>
      <published>2024-01-15T00:00:00Z</published>
      <author><name>Alice</name></author>
      <author><name>Bob</name></author>
      <category term="cs.LG"/>
    </entry>"""
    p = _parse_entry(ET.fromstring(xml))
    assert p is not None
    assert p.title == "A Test Paper"
    assert p.abstract == "An abstract."
    assert p.year == 2024
    assert p.authors == ["Alice", "Bob"]
    assert p.arxiv_id == "2401.01234"
    assert "cs.LG" in p.categories
    assert p.url == "https://arxiv.org/abs/2401.01234"


def test_parse_entry_skips_empty_title():
    import xml.etree.ElementTree as ET

    xml = """<?xml version='1.0'?>
    <entry xmlns="http://www.w3.org/2005/Atom">
      <title></title>
    </entry>"""
    assert _parse_entry(ET.fromstring(xml)) is None


@pytest.mark.network
def test_search_arxiv_live(settings: Settings):
    papers = search_arxiv(settings, ["attention is all you need"], max_per_query=3, years=(2017, 2020))
    assert len(papers) >= 1
    p = papers[0]
    assert isinstance(p, Paper)
    assert p.title
    assert p.abstract
    assert p.authors
    assert p.year and 2017 <= p.year <= 2020
    assert p.arxiv_id
