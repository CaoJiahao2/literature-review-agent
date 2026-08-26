"""Tests for cross-run JSON memory persistence."""

from __future__ import annotations

import json
from pathlib import Path

from lit_review.config import Settings
from lit_review.memory.store import (
    inject_memory_context,
    load_topic_memory,
    save_topic_memory,
    topic_memory_path,
)
from lit_review.state import Paper


def _settings(tmp_path: Path) -> Settings:
    return Settings(llm_api_key="", memory_dir=tmp_path / "memory")


def test_save_and_load_roundtrip(tmp_path: Path):
    settings = _settings(tmp_path)
    snapshot = {
        "topic": "RAG",
        "language": "en",
        "sections": {"background": "prior body"},
        "papers": [Paper(title="A", year=2024, source="arxiv", arxiv_id="2401.00001")],
        "topic_summary": "prior summary",
    }
    path = save_topic_memory(settings, "RAG", snapshot)

    assert path == topic_memory_path(settings, "RAG")
    assert path.exists()

    loaded = load_topic_memory(settings, "RAG")
    assert loaded is not None
    assert loaded["sections"]["background"] == "prior body"
    assert loaded["papers"][0]["title"] == "A"
    assert loaded["updated_at"]


def test_load_missing_memory_returns_none(tmp_path: Path):
    assert load_topic_memory(_settings(tmp_path), "not-there") is None


def test_corrupt_memory_file_is_tolerated(tmp_path: Path):
    settings = _settings(tmp_path)
    path = topic_memory_path(settings, "bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert load_topic_memory(settings, "bad") is None


def test_inject_memory_context_renders_prior_work(tmp_path: Path):
    settings = _settings(tmp_path)
    save_topic_memory(
        settings,
        "RAG",
        {
            "sections": {"background": "old background text"},
            "papers": [Paper(title="Old Paper", year=2023, source="arxiv")],
            "topic_summary": "old summary",
        },
    )
    snapshot, context = inject_memory_context(settings, "RAG")
    assert snapshot is not None
    assert "old background text" in context
    assert "Old Paper" in context


def test_topic_key_is_stable_across_whitespace_and_case(tmp_path: Path):
    settings = _settings(tmp_path)
    p1 = topic_memory_path(settings, "  Retrieval Augmented Generation ")
    p2 = topic_memory_path(settings, "retrieval augmented generation")
    assert p1 == p2
