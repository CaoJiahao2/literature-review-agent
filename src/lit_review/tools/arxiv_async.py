"""Async version of the arXiv source client.

Registered in :mod:`.async_runner` via :func:`register_async_source` so the
graph can fan out across (source, query) pairs concurrently.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Iterable, Optional

import httpx

from ..config import Settings
from ..state import Paper
from ._http import safe_get_async

log = logging.getLogger(__name__)

ATOM = "{http://www.w3.org/2005/Atom}"


def _to_query(q: str) -> str:
    q = q.strip()
    if not q:
        return ""
    if any(c in q for c in ['"', "'"]):
        return q
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


async def search_arxiv_async(
    client: httpx.AsyncClient,
    settings: Settings,
    queries: Iterable[str],
    *,
    max_per_query: int,
    years: Optional[tuple[int, int]] = None,
) -> list[Paper]:
    """Async counterpart of :func:`.arxiv.search_arxiv`.

    Uses ``httpx.AsyncClient`` for non-blocking HTTP, deduplicates across
    queries, and honors ``years`` server-side-result filtering.
    """
    out: list[Paper] = []
    seen_ids: set[str] = set()
    today = date.today().year
    if years is None:
        # Be liberal: arXiv has no native date filter; let dedupe + scoring handle it.
        years = (today - 10, today)

    for q in queries:
        q = (q or "").strip()
        if not q:
            continue
        params = {
            "search_query": _to_query(q),
            "start": 0,
            "max_results": max_per_query * 3,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        resp = await safe_get_async(client, "https://export.arxiv.org/api/query", params=params)
        if resp is None:
            continue
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            log.warning("arXiv XML parse failed for %r: %s", q, exc)
            continue
        kept = 0
        for entry in root.findall(f"{ATOM}entry"):
            paper = _parse_entry(entry)
            if paper is None or not paper.arxiv_id:
                continue
            if paper.arxiv_id in seen_ids:
                continue
            if paper.year and not (years[0] <= paper.year <= years[1]):
                continue
            seen_ids.add(paper.arxiv_id)
            out.append(paper)
            kept += 1
            if kept >= max_per_query:
                break
    return out
