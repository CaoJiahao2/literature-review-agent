"""Dedupe + scoring.

Merge logic (transitive cluster by union of identity keys):
  - Same DOI
  - Same arXiv id
  - Same normalized title

If any one of those matches between two papers (even across sources), they
are considered the same paper and merged. This catches the common case
where arxiv-only papers (no DOI) and OpenAlex records (DOI but no arxiv id)
describe the same work.

When merging, we keep the richer record (more filled fields), preferring
non-OpenAlex URLs when both sources expose a URL.

Score formula (in [0, 1], source-agnostic):
    0.5 * norm_citation + 0.3 * norm_recency + 0.2 * norm_abstract_richness
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Iterable

from ..state import Paper


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", title.lower())).strip()


def _identity_keys(p: Paper) -> set[str]:
    """All keys that can identify this paper."""
    keys: set[str] = set()
    if p.doi:
        keys.add(f"doi:{p.doi.lower()}")
    if p.arxiv_id:
        keys.add(f"arxiv:{p.arxiv_id.lower()}")
    t = _norm_title(p.title)
    if t:
        keys.add(f"title:{t}")
    return keys


def _richer(a: Paper, b: Paper) -> tuple[Paper, Paper]:
    """Return (keep, other) where `keep` is the more informative record."""
    def richness(p: Paper) -> int:
        score = len(p.abstract)
        if p.doi:
            score += 200
        if p.arxiv_id:
            score += 100
        if p.citation_count:
            score += 50
        if p.venue:
            score += 20
        if p.authors:
            score += 5 * len(p.authors)
        return score

    if richness(a) >= richness(b):
        return a, b
    return b, a


def _merge_two(a: Paper, b: Paper) -> Paper:
    """Prefer the richer record; fill gaps from the other.

    Source-agnostic. URL preference: keep a non-OpenAlex URL when both are present.
    """
    keep, other = _richer(a, b)

    merged = keep.model_copy()
    if not merged.doi and other.doi:
        merged.doi = other.doi
    if not merged.arxiv_id and other.arxiv_id:
        merged.arxiv_id = other.arxiv_id

    # URL: prefer DOI/arxiv canonical form; otherwise take whichever is non-OpenAlex.
    if not merged.url and other.url:
        merged.url = other.url
    elif merged.url and other.url and merged.url != other.url:
        if "openalex.org" in merged.url and "openalex.org" not in other.url:
            merged.url = other.url
        elif "openalex.org" in other.url and "openalex.org" not in merged.url:
            pass  # keep already-good URL

    if not merged.abstract and other.abstract:
        merged.abstract = other.abstract
    if not merged.venue and other.venue:
        merged.venue = other.venue
    if merged.citation_count is None and other.citation_count is not None:
        merged.citation_count = other.citation_count
    if other.citation_count is not None and keep.citation_count is not None:
        merged.citation_count = max(keep.citation_count, other.citation_count)
    for cat in other.categories:
        if cat not in merged.categories:
            merged.categories.append(cat)
    for auth in other.authors:
        if auth and auth not in merged.authors:
            merged.authors.append(auth)
    return merged


def merge_and_rank(papers: Iterable[Paper], *, top_k: int | None = None) -> list[Paper]:
    """Cluster by union of identity keys, merge each cluster, then rank.

    Two papers are considered the same if ANY of their identity keys overlap.
    This is what lets arxiv-only papers (DOI='') merge with OpenAlex records
    that DO have DOIs — they collide on arxiv_id or normalized title.
    """
    # Build clusters via union-find over identity keys.
    clusters: list[list[Paper]] = []
    key_to_cluster: dict[str, int] = {}

    for p in papers:
        keys = _identity_keys(p)
        if not keys:
            clusters.append([p])
            continue
        # Find any cluster whose keys overlap with this paper's.
        target_idx: int | None = None
        for k in keys:
            if k in key_to_cluster:
                target_idx = key_to_cluster[k]
                break
        if target_idx is None:
            clusters.append([p])
            new_idx = len(clusters) - 1
            for k in keys:
                key_to_cluster[k] = new_idx
        else:
            clusters[target_idx].append(p)
            for k in keys:
                key_to_cluster[k] = target_idx

    # Merge each cluster.
    merged: list[Paper] = []
    for group in clusters:
        head = group[0]
        for other in group[1:]:
            head = _merge_two(head, other)
        head.dedupe_key = next(iter(_identity_keys(head)), "title:")
        if len(group) > 1:
            head.source = "merged"
        merged.append(head)

    return score_and_sort(merged, top_k=top_k)


def score_and_sort(papers: list[Paper], *, top_k: int | None = None) -> list[Paper]:
    if not papers:
        return []
    citations = [p.citation_count or 0 for p in papers]
    years = [p.year or 0 for p in papers]
    max_cit = max(citations)
    min_year = min(y for y in years if y > 0) if any(years) else date.today().year
    max_year = max(years) if any(years) else date.today().year
    span = max(1, max_year - min_year)

    # Source-agnostic scoring. arxiv-only papers used to be penalized by 0.06
    # because their source wasn't "openalex"; now every paper is judged on
    # citations + recency + abstract richness alone.
    for p in papers:
        nc = math.log1p(p.citation_count or 0) / math.log1p(max_cit) if max_cit > 0 else 0.0
        ny = max(0.0, ((p.year or min_year) - min_year) / span) if p.year else 0.0
        nr = min(1.0, len(p.abstract) / 1500.0)
        p.score = 0.5 * nc + 0.3 * ny + 0.2 * nr

    papers.sort(key=lambda p: p.score, reverse=True)
    return papers[:top_k] if top_k else papers
