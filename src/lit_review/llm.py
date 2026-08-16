"""LLM factory: builds a ChatOpenAI pointed at any OpenAI-compatible endpoint.

Returns `None` when no API key is configured, so the rest of the pipeline can
degrade to deterministic skeleton output without special-casing.
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from .config import Settings

log = logging.getLogger(__name__)


def build_chat_model(settings: Settings, *, temperature: float = 0.2) -> Optional[BaseChatModel]:
    """Build a ChatOpenAI for any OpenAI-compatible endpoint.

    Returns None if no API key is set; callers must handle that case.
    """
    if not settings.has_llm():
        return None

    # Build a dedicated httpx client that ignores env proxies.
    # Many dev/CI envs set ALL_PROXY=socks:// which httpx can't speak;
    # the openai SDK (used underneath ChatOpenAI) would otherwise fail.
    import httpx

    http_client = httpx.Client(trust_env=False, timeout=settings.request_timeout)
    return ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=temperature,
        timeout=settings.request_timeout,
        http_client=http_client,
    )


def warn_if_no_llm(settings: Settings, *, forced: bool = False) -> None:
    """Emit a single warning when the LLM is unavailable."""
    if forced:
        return  # user explicitly opted out
    if not settings.has_llm():
        log.warning(
            "LLM_API_KEY is not set — falling back to a deterministic skeleton report. "
            "Copy .env.example to .env and set LLM_API_KEY (any OpenAI-compatible provider) "
            "to enable prose synthesis."
        )
