"""Crossref API client.

Endpoint: https://api.crossref.org/works

Free, no key. Returns rich DOI/citation metadata for academic works across
all disciplines. Polite pool: append `mailto=` to get higher rate limits.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from ..config import Settings
from ..state import Paper
from ._http import get_client, safe_get

log = logging.getLogger(__name__)


def _parse(item: dict, year_filter: Optional[tuple[int, int]]) -> Optional[Paper]:
    title_list = item.get("title") or []
    if not title_list:
        return None
    title = re.sub(r"\s+", " ", title_list[0]).strip()

    # Published date can be issued["date-parts"][[YYYY,MM,DD]].
    issued = item.get("issued") or item.get("published-print") or item.get("published-online") or {}
    parts = (issued.get("date-parts") or [[None]])[0]
    year = parts[0] if parts and isinstance(parts[0], int) else None
    if year_filter and year and not (year_filter[0] <= year <= year_filter[1]):
        return None

    authors: list[str] = []
    for a in item.get("author") or []:
        name = " ".join([a.get("given") or "", a.get("family") or ""]).strip()
        if name:
            authors.append(name)

    venue_list = item.get("container-title") or []
    venue = venue_list[0] if venue_list else ""

    doi = (item.get("DOI") or "").lower()
    url = item.get("URL") or (f"https://doi.org/{doi}" if doi else "")

    cited = item.get("is-referenced-by-count")
    if not isinstance(cited, int):
        cited = None

    abstract = (item.get("abstract") or "").strip()
    # Crossref abstracts are often JATS XML; strip tags crudely if present.
    if abstract and "<" in abstract:
        abstract = re.sub(r"<[^>]+>", " ", abstract)
        abstract = re.sub(r"\s+", " ", abstract).strip()

    # Try to find an arxiv id in the link list.
    arxiv_id = ""
    for link in item.get("link") or []:
        url_l = (link.get("URL") or "").lower()
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]+\.[0-9]+)", url_l)
        if m:
            arxiv_id = m.group(1)
            break

    return Paper(
        title=title,
        authors=authors,
        year=year,
        abstract=abstract,
        venue=venue,
        url=url,
        doi=doi,
        arxiv_id=arxiv_id,
        categories=[],
        citation_count=cited,
        source="crossref",
    )


def search_crossref(
    settings: Settings,
    queries: Iterable[str],
    *,
    max_per_query: Optional[int] = None,
    years: Optional[tuple[int, int]] = None,
) -> list[Paper]:
    cap = max_per_query if max_per_query is not None else settings.crossref_max_per_query
    out: list[Paper] = []
    seen: set[str] = set()

    import re as _re
    mailto = ""
    m = _re.search(r"mailto:([^)\s]+)", settings.user_agent)
    if m:
        mailto = m.group(1)

    with get_client(settings) as client:
        for q in queries:
            q = q.strip()
            if not q:
                continue
            params = {
                "query.bibliographic": q,
                "rows": cap * 3,
                "select": "DOI,title,author,issued,published-print,published-online,container-title,URL,is-referenced-by-count,abstract,link",
            }
            if mailto:
                params["mailto"] = mailto
            if years:
                params["filter"] = f"from-pub-date:{years[0]}-01-01,until-pub-date:{years[1]}-12-31"

            resp = safe_get(client, "https://api.crossref.org/works", params=params)
            if resp is None:
                continue
            data = resp.json() if resp.content else {}
            items = (data.get("message") or {}).get("items") or []

            kept = 0
            for item in items:
                paper = _parse(item, years)
                if paper is None:
                    continue
                key = paper.short_id()
                if key in seen:
                    continue
                seen.add(key)
                out.append(paper)
                kept += 1
                if kept >= cap:
                    break
    return out
