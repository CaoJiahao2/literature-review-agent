"""LLM client facade: caching, retry/backoff, token accounting.

The goals of this module:

1. Keep the ReAct agent loop free of retry / cache / network-handling code.
2. Provide a single object that knows about token usage so :mod:`runner` can
   surface cost data without the agent loop having to thread it through state.
3. Offer a *deterministic in-process cache* so re-runs (or reflection steps
   that re-issue similar prompts) cost nothing.
4. Fall back gracefully on 429/timeout — never crash a run; surface the issue
   via the ``fallback`` payload.

The facade wraps any ``BaseChatModel`` (typically ``ChatOpenAI``). New
providers can plug in via the ``model_factory`` parameter.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .config import Settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    text: str
    created_at: float
    tokens_in: int = 0
    tokens_out: int = 0


class _LRUCache:
    """Tiny in-process LRU cache, with optional disk spill."""

    def __init__(self, max_entries: int = 256, disk_dir: Optional[Path] = None) -> None:
        self.max_entries = max_entries
        self.disk_dir = disk_dir
        self._entries: dict[str, _CacheEntry] = {}
        self._order: list[str] = []
        if disk_dir is not None:
            disk_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Optional[_CacheEntry]:
        entry = self._entries.get(key)
        if entry is not None:
            # touch
            try:
                self._order.remove(key)
            except ValueError:
                pass
            self._order.append(key)
            return entry
        if self.disk_dir is not None:
            path = self._path(key)
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    entry = _CacheEntry(
                        text=raw["text"],
                        created_at=raw.get("created_at", 0.0),
                        tokens_in=raw.get("tokens_in", 0),
                        tokens_out=raw.get("tokens_out", 0),
                    )
                    self._set(key, entry)
                    return entry
                except Exception:
                    return None
        return None

    def set(self, key: str, entry: _CacheEntry) -> None:
        self._set(key, entry)
        if self.disk_dir is not None:
            try:
                self._path(key).write_text(
                    json.dumps(
                        {
                            "text": entry.text,
                            "created_at": entry.created_at,
                            "tokens_in": entry.tokens_in,
                            "tokens_out": entry.tokens_out,
                        }
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass

    def _set(self, key: str, entry: _CacheEntry) -> None:
        if key in self._entries:
            try:
                self._order.remove(key)
            except ValueError:
                pass
        self._entries[key] = entry
        self._order.append(key)
        while len(self._order) > self.max_entries:
            evict = self._order.pop(0)
            self._entries.pop(evict, None)

    def _path(self, key: str) -> Path:
        return self.disk_dir / f"{key}.json"  # type: ignore[union-attr]

    def clear(self) -> None:
        self._entries.clear()
        self._order.clear()


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------


_RETRYABLE = (
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",  # handled separately to honor Retry-After
    "InternalServerError",
    "ServiceUnavailableError",
)


def _is_retryable(exc: BaseException) -> bool:
    name = type(exc).__name__
    return name in _RETRYABLE or "timeout" in str(exc).lower()


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Best-effort extraction of Retry-After / backoff hints."""
    headers = getattr(exc, "headers", None)
    if headers:
        try:
            v = headers.get("retry-after") or headers.get("Retry-After")
            if v is not None:
                return float(v)
        except Exception:
            pass
    # OpenAI 429s typically include a message like "Please try again in 20s."
    s = str(exc)
    import re

    m = re.search(r"try again in (\d+(?:\.\d+)?)s", s)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


@dataclass
class LLMClient:
    """Facade around ``BaseChatModel`` with cache + retry + usage counters."""

    settings: Settings
    cache: _LRUCache = field(init=False)
    calls: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    errors: list[str] = field(default_factory=list)
    by_tag: dict[str, int] = field(default_factory=dict)
    _tracer: Optional[Callable[[str, dict], None]] = None

    def __post_init__(self) -> None:
        disk_dir: Optional[Path] = None
        env_dir = os.environ.get("LIT_REVIEW_CACHE_DIR")
        if env_dir:
            disk_dir = Path(env_dir)
        self.cache = _LRUCache(max_entries=256, disk_dir=disk_dir)

    # ----- public API --------------------------------------------------

    def set_tracer(self, tracer: Optional[Callable[[str, dict], None]]) -> None:
        self._tracer = tracer

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "by_tag": dict(self.by_tag),
            "errors": list(self.errors),
        }

    def invoke_text(
        self,
        *,
        system: str,
        user: str,
        tag: str = "default",
        temperature: float = 0.2,
        max_attempts: int = 3,
    ) -> Optional[str]:
        """Invoke the chat model and return the assistant text.

        Returns None when the call ultimately fails after ``max_attempts``.
        The caller decides whether to treat ``None`` as a fatal error or to
        use the ``fallback`` payload.
        """
        if not self.settings.has_llm():
            return None
        key = self._cache_key(tag, system, user, temperature)
        cached = self.cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            self._emit_trace(tag, {"cache_hit": True})
            return cached.text

        model = self._build_model(temperature)
        if model is None:
            return None
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        last_exc: Optional[BaseException] = None
        for attempt in range(max_attempts):
            try:
                resp = model.invoke(messages)
                text = self._extract_text(resp)
                self._consume_usage(resp)
                self.calls += 1
                self.by_tag[tag] = self.by_tag.get(tag, 0) + 1
                self.cache.set(
                    key,
                    _CacheEntry(
                        text=text,
                        created_at=time.time(),
                        tokens_in=getattr(resp, "usage_metadata", {}).get("input_tokens", 0)
                        if hasattr(resp, "usage_metadata")
                        else 0,
                        tokens_out=getattr(resp, "usage_metadata", {}).get("output_tokens", 0)
                        if hasattr(resp, "usage_metadata")
                        else 0,
                    ),
                )
                self._emit_trace(tag, {"cache_hit": False, "attempt": attempt + 1})
                return text
            except Exception as exc:
                last_exc = exc
                self.errors.append(f"{tag}: {type(exc).__name__}: {exc}")
                if not _is_retryable(exc):
                    log.warning("[%s] non-retryable error: %s", tag, exc)
                    break
                sleep_for = _retry_after_seconds(exc) or (1.5 * (attempt + 1))
                sleep_for = min(sleep_for, 30.0)
                log.info(
                    "[%s] retryable error on attempt %d, sleeping %.1fs: %s",
                    tag,
                    attempt + 1,
                    sleep_for,
                    exc,
                )
                time.sleep(sleep_for)
        log.warning("[%s] giving up after %d attempts: %s", tag, max_attempts, last_exc)
        return None

    def invoke_json(
        self,
        *,
        system: str,
        user: str,
        tag: str = "default",
        temperature: float = 0.2,
        fallback: Optional[dict] = None,
    ) -> dict:
        """Invoke for JSON output. Returns ``fallback`` on any failure."""
        fallback = fallback or {}
        text = self.invoke_text(
            system=system, user=user, tag=tag, temperature=temperature
        )
        if text is None:
            return dict(fallback)
        data = _safe_json(text)
        if not data and fallback:
            return dict(fallback)
        return data or dict(fallback)

    def bind_tools(self, tools, *, temperature: float = 0.2):
        """Build and return a chat model with the given LangChain tools bound.

        Returns None when the model cannot be built (e.g. no API key).
        """
        if not self.settings.has_llm():
            return None
        try:
            model = self._build_model(temperature)
        except Exception as exc:
            log.warning("build_chat_model failed: %s", exc)
            return None
        if model is None:
            return None
        try:
            return model.bind_tools(list(tools))
        except Exception as exc:
            log.warning("bind_tools failed: %s", exc)
            return None

    def invoke_chat(
        self,
        model,
        messages,
        *,
        tag: str = "agent",
        max_attempts: int = 3,
    ) -> Any:
        """Invoke a bound chat model with retry/backoff and usage accounting.

        Tool-calling agent steps are intentionally **not** cached because their
        output depends on the full, evolving message transcript. Returns the raw
        response message (with ``tool_calls`` populated when the model requests a
        tool) or None when the call ultimately fails.
        """
        if model is None:
            return None
        last_exc: Optional[BaseException] = None
        for attempt in range(max_attempts):
            try:
                resp = model.invoke(list(messages))
                self._consume_usage(resp)
                self.calls += 1
                self.by_tag[tag] = self.by_tag.get(tag, 0) + 1
                self._emit_trace(tag, {"cache_hit": False, "attempt": attempt + 1})
                return resp
            except Exception as exc:
                last_exc = exc
                self.errors.append(f"{tag}: {type(exc).__name__}: {exc}")
                if not _is_retryable(exc):
                    log.warning("[%s] non-retryable error: %s", tag, exc)
                    break
                sleep_for = _retry_after_seconds(exc) or (1.5 * (attempt + 1))
                sleep_for = min(sleep_for, 30.0)
                log.info(
                    "[%s] retryable error on attempt %d, sleeping %.1fs: %s",
                    tag,
                    attempt + 1,
                    sleep_for,
                    exc,
                )
                time.sleep(sleep_for)
        log.warning("[%s] giving up after %d attempts: %s", tag, max_attempts, last_exc)
        return None

    # ----- internals ---------------------------------------------------

    def _build_model(self, temperature: float):
        # Defer imports — keep ``import lit_review.llm_client`` cheap.
        if not self.settings.has_llm():
            return None
        try:
            from .llm import build_chat_model
        except Exception:
            return None
        try:
            return build_chat_model(self.settings, temperature=temperature)
        except Exception as exc:
            log.warning("build_chat_model failed: %s", exc)
            return None

    @staticmethod
    def _cache_key(tag: str, system: str, user: str, temperature: float) -> str:
        payload = json.dumps(
            {"tag": tag, "system": system, "user": user, "temperature": temperature},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _extract_text(resp: Any) -> str:
        content = getattr(resp, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict):
                    parts.append(str(p.get("text", "")))
                else:
                    parts.append(str(p))
            return "\n".join(parts).strip()
        return str(content)

    def _consume_usage(self, resp: Any) -> None:
        meta = getattr(resp, "usage_metadata", None)
        if isinstance(meta, dict):
            self.tokens_in += int(meta.get("input_tokens", 0) or 0)
            self.tokens_out += int(meta.get("output_tokens", 0) or 0)

    def _emit_trace(self, tag: str, payload: dict) -> None:
        if self._tracer is None:
            return
        try:
            self._tracer(tag, payload)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def _safe_json(text: str) -> dict:
    """Lenient JSON parser for LLM output."""
    import re

    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return {}
    return {}


__all__ = ["LLMClient", "_safe_json"]
