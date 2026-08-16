"""Tests for :class:`lit_review.llm_client.LLMClient`.

These tests use a synthetic ``BaseChatModel`` so they don't hit the network
or require a real API key. Cache + retry path is exercised.
"""

from __future__ import annotations

import pytest

from lit_review.config import Settings
from lit_review.llm_client import LLMClient, _safe_json


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _StubMessage:
    def __init__(self, content: str, usage: dict | None = None) -> None:
        self.content = content
        self.usage_metadata = usage or {"input_tokens": 0, "output_tokens": 0}


class _StubModelOK:
    """Always returns a deterministic response and counts calls."""

    calls = 0

    @classmethod
    def reset(cls) -> None:
        cls.calls = 0

    def invoke(self, messages):
        type(self).calls += 1
        # Echo a JSON payload: useful for invoke_json tests.
        return _StubMessage('{"queries": ["q1", "q2"]}', {"input_tokens": 5, "output_tokens": 7})


class _FlakyModel(_StubModelOK):
    """Fails the first time, succeeds the second; for retry tests."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def invoke(self, messages):
        self.attempts += 1
        if self.attempts == 1:
            from lit_review.llm_client import _is_retryable  # noqa: F401
            # Simulate retryable error class; easier to import a real one
            raise TimeoutError("simulated timeout")
        return _StubMessage('{"queries": ["ok"]}', {"input_tokens": 1, "output_tokens": 1})


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def _settings_without_llm():
    return Settings(llm_api_key="")


def _settings_with_llm():
    return Settings(llm_api_key="sk-test", llm_model="test-model")


def test_safe_json_strips_fences():
    assert _safe_json("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_safe_json_handles_inline_braces():
    text = 'preamble {"queries": ["x"]} trailing'
    assert _safe_json(text) == {"queries": ["x"]}


def test_safe_json_returns_empty_dict_on_failure():
    assert _safe_json("not json at all") == {}


def test_invoke_text_returns_none_when_no_key():
    client = LLMClient(_settings_without_llm())
    assert client.invoke_text(system="s", user="u", tag="t") is None


def test_invoke_text_uses_cache(monkeypatch):
    _StubModelOK.reset()
    settings = _settings_with_llm()
    client = LLMClient(settings)
    monkeypatch.setattr(client, "_build_model", lambda temperature: _StubModelOK())
    out1 = client.invoke_text(system="s", user="u", tag="cached")
    out2 = client.invoke_text(system="s", user="u", tag="cached")
    assert out1 == out2
    assert _StubModelOK.calls == 1
    snap = client.snapshot()
    assert snap["calls"] == 1
    assert snap["cache_hits"] == 1


def test_invoke_text_handles_retryable_error(monkeypatch):
    settings = _settings_with_llm()
    client = LLMClient(settings)
    flaky = _FlakyModel()
    monkeypatch.setattr(client, "_build_model", lambda temperature: flaky)
    out = client.invoke_text(system="s", user="u", tag="retry")
    assert out == '{"queries": ["ok"]}'
    assert flaky.attempts == 2


def test_invoke_text_returns_none_after_giving_up(monkeypatch):
    class _AlwaysFail:
        def invoke(self, messages):
            raise TimeoutError("nope")

    settings = _settings_with_llm()
    client = LLMClient(settings)
    monkeypatch.setattr(client, "_build_model", lambda temperature: _AlwaysFail())
    out = client.invoke_text(system="s", user="u", tag="doomed", max_attempts=2)
    assert out is None
    assert client.errors  # an entry was appended


def test_invoke_json_parses_response(monkeypatch):
    settings = _settings_with_llm()
    client = LLMClient(settings)
    monkeypatch.setattr(client, "_build_model", lambda temperature: _StubModelOK())
    out = client.invoke_json(system="s", user="u", tag="j", fallback={})
    assert out == {"queries": ["q1", "q2"]}


def test_invoke_json_returns_fallback_on_empty(monkeypatch):
    class _Bad:
        def invoke(self, messages):
            return _StubMessage("not json")

    settings = _settings_with_llm()
    client = LLMClient(settings)
    monkeypatch.setattr(client, "_build_model", lambda temperature: _Bad())
    fallback = {"queries": ["fallback"]}
    out = client.invoke_json(system="s", user="u", tag="j-fb", fallback=fallback)
    assert out == fallback
