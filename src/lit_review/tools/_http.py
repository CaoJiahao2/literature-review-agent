"""Shared HTTP helpers for source tools (sync + async)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

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


def get_async_client(settings: Settings) -> httpx.AsyncClient:
    """An async counterpart to :func:`get_client` with the same defaults."""
    return httpx.AsyncClient(
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


async def safe_get_async(
    client: httpx.AsyncClient, url: str, *, params: dict[str, Any] | None = None
) -> httpx.Response | None:
    """Async counterpart of :func:`safe_get` with the same retry semantics."""
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r
        except (httpx.HTTPError, httpx.RequestError) as exc:
            last_err = exc
            log.warning("async GET %s failed (attempt %d): %s", url, attempt + 1, exc)
            await asyncio.sleep(0.25 * (attempt + 1))
    log.error("async GET %s gave up: %s", url, last_err)
    return None
