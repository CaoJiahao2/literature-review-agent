"""Shared HTTP helpers for source tools."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import Settings

log = logging.getLogger(__name__)


def get_client(settings: Settings) -> httpx.Client:
    """A short-lived HTTP client honouring the configured UA + timeout."""
    return httpx.Client(
        timeout=settings.request_timeout,
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "application/json, application/atom+xml;q=0.9, */*;q=0.5",
        },
        follow_redirects=True,
        trust_env=False,
    )


def safe_get(client: httpx.Client, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response | None:
    """GET with two retries on connection errors; returns None on failure."""
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            r = client.get(url, params=params)
            r.raise_for_status()
            return r
        except (httpx.HTTPError, httpx.RequestError) as exc:
            last_err = exc
            log.warning("GET %s failed (attempt %d): %s", url, attempt + 1, exc)
    log.error("GET %s gave up: %s", url, last_err)
    return None
