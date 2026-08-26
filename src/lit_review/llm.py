"""LLM factory: builds a ChatOpenAI pointed at any OpenAI-compatible endpoint.

``build_chat_model`` returns ``None`` when no API key is configured; the ReAct
agent has no deterministic fallback, so callers should use ``require_llm()``
to fail fast with a clear ``ConfigurationError``.
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


def require_llm(settings: Settings) -> None:
    """Raise when the LLM is unavailable (the ReAct agent has no skeleton fallback)."""
    if not settings.has_llm():
        from .config import ConfigurationError

        raise ConfigurationError(
            "LLM_API_KEY is not set. This agent requires any OpenAI-compatible "
            "endpoint; copy .env.example to .env and configure LLM_API_KEY "
            "(LLM_BASE_URL / LLM_MODEL optional)."
        )
