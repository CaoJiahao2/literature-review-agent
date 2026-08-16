"""Async version of the Semantic Scholar source client."""

from __future__ import annotations

import logging
import os
from typing import Iterable, Optional

import httpx

from ..config import Settings
from ..state import Paper
from ._http import safe_get_async

log = logging.getLogger(__name__)


def _parse(p: dict, year_filter: Optional[tuple[int, int]]) -> Optional[Paper]:
    paper_id = p.get("paperId") or ""
    title = (p.get("title") or "").strip()
    if not title:
        return None
    year = p.get("year")
    if not isinstance(year, int):
        year = None
    if year_filter and year and not (year_filter[0] <= year <= year_filter[1]):
        return None
    abstract = (p.get("abstract") or "").strip()
    authors = [a.get("name", "") for a in (p.get("authors") or []) if a.get("name")]
    venue = (p.get("venue") or "").strip()
    ext = p.get("externalIds") or {}
    doi = (ext.get("DOI") or "").lower()
    arxiv_id = (ext.get("ArXiv") or "").lower()
    url = p.get("url") or (f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else "")
    cited = p.get("citationCount")
    if not isinstance(cited, int):
        cited = None
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
        source="semantic_scholar",
    )


async def search_semantic_scholar_async(
    client: httpx.AsyncClient,
    settings: Settings,
    queries: Iterable[str],
    *,
    max_per_query: int,
    years: Optional[tuple[int, int]] = None,
) -> list[Paper]:
    out: list[Paper] = []
    seen: set[str] = set()
    api_key = os.environ.get("S2_API_KEY", "").strip()

    for q in queries:
        q = (q or "").strip()
        if not q:
            continue
        params = {
            "query": q,
            "limit": max_per_query * 3,
            "fields": "title,abstract,year,authors,venue,externalIds,url,citationCount",
        }
        if years:
            params["year"] = f"{years[0]}-{years[1]}"
        headers = {"x-api-key": api_key} if api_key else {}

        try:
            resp = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=params,
                headers=headers,
            )
            if resp.status_code == 429:
                log.warning("Semantic Scholar rate-limited (no S2_API_KEY set); skipping.")
                return out
            resp.raise_for_status()
        except Exception as exc:
            log.warning("Semantic Scholar request failed: %s", exc)
            continue

        try:
            data = resp.json()
        except Exception as exc:
            log.warning("Semantic Scholar JSON parse failed: %s", exc)
            continue
        results = data.get("data") or []
        kept = 0
        for p in results:
            paper = _parse(p, years)
            if paper is None:
                continue
            key = paper.short_id()
            if key in seen:
                continue
            seen.add(key)
            out.append(paper)
            kept += 1
            if kept >= max_per_query:
                break
    return out
