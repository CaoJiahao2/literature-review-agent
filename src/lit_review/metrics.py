"""Lightweight observability primitives.

This module exposes:

* :class:`Metrics` — the dataclass written to ``<report>.metrics.json``.
* :func:`timed_node` — decorator that records per-node wall-clock into a
  caller-provided mutable container (``state['__node_times__']``).

We intentionally avoid a hard dependency on OpenTelemetry for the v0.2 line:
the metrics surface here is enough for ad-hoc analysis, and an OTel tracer
can be plugged into :class:`LLMClient` later (see ``llm_client.py``).
"""

from __future__ import annotations

import dataclasses
import json
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Iterator


@dataclasses.dataclass
class Metrics:
    started_at: str
    finished_at: str
    duration_ms: int
    sources: dict[str, int] = dataclasses.field(default_factory=dict)
    merged: dict[str, int] = dataclasses.field(default_factory=dict)
    llm: dict[str, Any] = dataclasses.field(default_factory=dict)
    nodes: dict[str, int] = dataclasses.field(default_factory=dict)
    sections: dict[str, int] = dataclasses.field(default_factory=dict)
    papers_collected: int = 0
    papers_kept: int = 0
    errors: list[str] = dataclasses.field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False, indent=2)


@contextmanager
def timed_node(state: dict, name: str) -> Iterator[None]:
    """Context manager that times a code block into ``state['__node_times__']``.

    Usage::

        def my_node(state, settings):
            with timed_node(state, "my_node"):
                ...
            return {...}

    The block must write its own partial state; the context manager only
    measures wall-clock and side-effects nothing.
    """
    bucket = state.setdefault("__node_times__", {})
    started = time.monotonic()
    try:
        yield
    finally:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        # Use the maximum so re-entry of the same block doesn't double-count.
        bucket[name] = max(int(bucket.get(name, 0)), elapsed_ms)


def timed_node_decorator(name: str) -> Callable[[Callable[..., dict]], Callable[..., dict]]:
    """Decorator variant for cases where :func:`timed_node` is awkward.

    The wrapped function still returns a partial state dict.
    """
    def deco(fn: Callable[..., dict]) -> Callable[..., dict]:
        def wrapper(state, settings) -> dict:
            with timed_node(state, name):
                return fn(state, settings)
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def wall_ms(state: dict, name: str) -> int:
    """Read the elapsed-ms of a previously :func:`timed_node`-wrapped block."""
    return int((state.get("__node_times__") or {}).get(name, 0))


def new_metrics() -> Metrics:
    now = datetime.now().astimezone().isoformat()
    return Metrics(
        started_at=now,
        finished_at="",
        duration_ms=0,
    )


__all__ = ["Metrics", "timed_node", "timed_node_decorator", "wall_ms", "new_metrics"]
