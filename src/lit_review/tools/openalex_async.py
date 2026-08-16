"""Async version of the OpenAlex source client."""

from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

import httpx

from ..config import Settings
from ..state import Paper
from ._http import safe_get_async

log = logging.getLogger(__name__)


def _reconstruct_abstract(inverted_index: object) -> str:
    if not isinstance(inverted_index, dict) or not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        if not isinstance(idxs, list):
            continue
        for i in idxs:
            if isinstance(i, int):
                positions.append((i, word))
    if not positions:
        return ""
    positions.sort()
    return " ".join(w for _, w in positions)


def _parse_work(work: dict, year_filter: Optional[tuple[int, int]]) -> Optional[Paper]:
    title = (work.get("title") or "").strip()
    if not title:
        return None
    year = work.get("publication_year")
    if not isinstance(year, int):
        year = None
    if year_filter and year and not (year_filter[0] <= year <= year_filter[1]):
        return None
    authorships = work.get("authorships") or []
    authors = [(a.get("author") or {}).get("display_name") for a in authorships]
    authors = [n for n in authors if n]
    venue = ""
    primary = work.get("primary_location") or {}
    src = primary.get("source") or {}
    if src.get("display_name"):
        venue = src["display_name"]
    doi = (work.get("doi") or "").lower()
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/") :]
    url = work.get("id") or ""
    if url.startswith("https://openalex.org/"):
        url = f"https://openalex.org/works/{url.rsplit('/', 1)[-1]}"
    arxiv_id = ""
    for loc in work.get("locations") or []:
        ext = (loc.get("external_id") or "").lower()
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]+\.[0-9]+)", ext)
        if m:
            arxiv_id = m.group(1)
            break
    cited = work.get("cited_by_count")
    if not isinstance(cited, int):
        cited = None
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))
    return Paper(
        title=re.sub(r"\s+", " ", title),
        authors=authors,
        year=year,
        abstract=abstract,
        venue=venue,
        url=url,
        doi=doi,
        arxiv_id=arxiv_id,
        categories=[],
        citation_count=cited,
        source="openalex",
    )


async def search_openalex_async(
    client: httpx.AsyncClient,
    settings: Settings,
    queries: Iterable[str],
    *,
    max_per_query: int,
    years: Optional[tuple[int, int]] = None,
) -> list[Paper]:
    out: list[Paper] = []
    seen: set[str] = set()
    mailto = ""
    m = re.search(r"mailto:([^)\s]+)", settings.user_agent)
    if m:
        mailto = m.group(1)

    for q in queries:
        q = (q or "").strip()
        if not q:
            continue
        params = {
            "search": q,
            "per_page": max_per_query * 3,
            "page": 1,
        }
        if mailto:
            params["mailto"] = mailto
        if years:
            params["filter"] = f"publication_year:{years[0]}-{years[1]}"

        resp = await safe_get_async(client, "https://api.openalex.org/works", params=params)
        if resp is None:
            continue
        try:
            data = resp.json()
        except Exception as exc:
            log.warning("OpenAlex JSON parse failed for %r: %s", q, exc)
            continue
        results = data.get("results") or []
        kept = 0
        for work in results:
            paper = _parse_work(work, years)
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
