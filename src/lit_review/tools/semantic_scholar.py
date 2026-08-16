"""Semantic Scholar Graph API client.

Endpoint: https://api.semanticscholar.org/graph/v1/paper/search

Free without a key, but heavily rate-limited (HTTP 429). If `S2_API_KEY` is set
in the environment we use it for the higher rate limit; otherwise we still try
and gracefully degrade on 429.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Optional

from ..config import Settings
from ..state import Paper
from ._http import get_client, safe_get

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

    # S2 `url` is /paper/<id>; build a public-facing URL.
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


def search_semantic_scholar(
    settings: Settings,
    queries: Iterable[str],
    *,
    max_per_query: Optional[int] = None,
    years: Optional[tuple[int, int]] = None,
) -> list[Paper]:
    cap = max_per_query if max_per_query is not None else settings.semantic_scholar_max_per_query
    out: list[Paper] = []
    seen: set[str] = set()

    api_key = os.environ.get("S2_API_KEY", "").strip()

    with get_client(settings) as client:
        for q in queries:
            q = q.strip()
            if not q:
                continue
            params = {
                "query": q,
                "limit": cap * 3,
                "fields": "title,abstract,year,authors,venue,externalIds,url,citationCount",
            }
            if years:
                params["year"] = f"{years[0]}-{years[1]}"
            headers = {"x-api-key": api_key} if api_key else {}

            # Override the default client headers for this call.
            try:
                resp = client.get(
                    "https://api.semanticscholar.org/graph/v1/paper/search",
                    params=params,
                    headers=headers,
                )
                if resp.status_code == 429:
                    log.warning("Semantic Scholar rate-limited (no S2_API_KEY set); skipping.")
                    return out
                resp.raise_for_status()
            except Exception as exc:  # pragma: no cover
                log.warning("Semantic Scholar request failed: %s", exc)
                continue

            data = resp.json() if resp.content else {}
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
                if kept >= cap:
                    break
    return out
