"""arXiv API client.

Endpoint: https://export.arxiv.org/api/query (Atom XML, HTTPS).

Query syntax we use:
    all:<terms>  OR  ti:<term> AND abs:<term>

We filter by year range after parsing (arXiv's API has no native date filter).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Iterable, Optional

from ..config import Settings
from ..state import Paper
from ._http import get_client, safe_get

log = logging.getLogger(__name__)

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def _to_query(q: str) -> str:
    """Turn a free-form query into arXiv's structured query.

    For short multi-word topics we search `all:`; otherwise we OR `ti:` and `abs:`
    on the raw string. Quotes around the raw string are safe for arXiv's parser.
    """
    q = q.strip()
    if not q:
        return ""
    if any(c in q for c in ['"', "'"]):
        return q
    # All-text search is the simplest and good enough for the literature review use-case.
    return f'all:"{q}"'


def _parse_entry(entry: ET.Element) -> Optional[Paper]:
    title = (entry.findtext(f"{ATOM}title") or "").strip()
    title = re.sub(r"\s+", " ", title)
    summary = (entry.findtext(f"{ATOM}summary") or "").strip()
    summary = re.sub(r"\s+", " ", summary)

    authors = [a.findtext(f"{ATOM}name", "").strip() for a in entry.findall(f"{ATOM}author")]
    authors = [a for a in authors if a]

    published = entry.findtext(f"{ATOM}published") or ""
    year = None
    if published[:4].isdigit():
        year = int(published[:4])

    arxiv_id = ""
    id_text = entry.findtext(f"{ATOM}id") or ""
    m = re.search(r"arxiv\.org/abs/([^v]+)(v\d+)?", id_text)
    if m:
        arxiv_id = m.group(1)

    categories = [c.attrib.get("term", "") for c in entry.findall(f"{ATOM}category")]
    categories = [c for c in categories if c]

    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else id_text

    if not title:
        return None

    return Paper(
        title=title,
        authors=authors,
        year=year,
        abstract=summary,
        venue="arXiv",
        url=url,
        arxiv_id=arxiv_id,
        categories=categories,
        citation_count=None,
        source="arxiv",
    )


def search_arxiv(
    settings: Settings,
    queries: Iterable[str],
    *,
    max_per_query: Optional[int] = None,
    years: Optional[tuple[int, int]] = None,
) -> list[Paper]:
    """Run each query against arXiv and return merged Paper records.

    `max_per_query` defaults to `settings.arxiv_max_per_query`.
    `years` is an inclusive (low, high) range; entries outside it are dropped.
    """
    cap = max_per_query if max_per_query is not None else settings.arxiv_max_per_query
    out: list[Paper] = []
    seen_ids: set[str] = set()

    with get_client(settings) as client:
        for q in queries:
            q = q.strip()
            if not q:
                continue
            query_str = _to_query(q)
            params = {
                "search_query": query_str,
                "start": 0,
                "max_results": cap * 3,  # over-fetch then filter
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
            resp = safe_get(client, "https://export.arxiv.org/api/query", params=params)
            if resp is None:
                continue
            try:
                root = ET.fromstring(resp.text)
            except ET.ParseError as exc:
                log.warning("arXiv XML parse failed for %r: %s", q, exc)
                continue

            kept_for_query = 0
            for entry in root.findall(f"{ATOM}entry"):
                paper = _parse_entry(entry)
                if paper is None or not paper.arxiv_id:
                    continue
                if paper.arxiv_id in seen_ids:
                    continue
                if years and paper.year and not (years[0] <= paper.year <= years[1]):
                    continue
                seen_ids.add(paper.arxiv_id)
                out.append(paper)
                kept_for_query += 1
                if kept_for_query >= cap:
                    break
    return out
