"""Tests for the lightweight metrics primitives."""

from __future__ import annotations

import time

from lit_review.metrics import Metrics, new_metrics, timed_node, timed_node_decorator, wall_ms


def test_new_metrics_initializes_clock():
    m = new_metrics()
    assert m.started_at
    assert m.duration_ms == 0
    assert isinstance(m.sources, dict)


def test_timed_node_records_wall_clock():
    state: dict = {}
    with timed_node(state, "alpha"):
        time.sleep(0.005)
    assert wall_ms(state, "alpha") >= 4


def test_timed_node_uses_max_when_reentered():
    """Same name ran twice should keep the largest measurement."""
    state: dict = {}
    with timed_node(state, "beta"):
        time.sleep(0.02)
    with timed_node(state, "beta"):
        time.sleep(0.001)
    # If we kept both we'd see larger total — but we keep max, which should
    # at minimum be the first block (~20ms).
    assert wall_ms(state, "beta") >= 15


def test_timed_node_decorator():
    state: dict = {}

    @timed_node_decorator("decorated")
    def fake_node(state, settings):
        time.sleep(0.005)
        return {"foo": 1}

    out = fake_node(state, object())
    assert out == {"foo": 1}
    assert wall_ms(state, "decorated") >= 4


def test_metrics_to_json_round_trip():
    m = new_metrics()
    m.sources["arxiv"] = 12
    m.errors.append("oops")
    j = m.to_json()
    assert "arxiv" in j
    assert "oops" in j
