"""Per-topic JSON memory persistence.

Each normalized topic maps to one JSON file under ``Settings.memory_dir``,
named ``sha256(normalized topic).json``. A snapshot stores the last successful
report, its paper list, and lightweight metadata — enough for ``--resume`` to
reuse prior work without replaying the full message transcript.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..state import Paper

log = logging.getLogger(__name__)


def _normalize_topic(topic: str) -> str:
    t = re.sub(r"\s+", " ", (topic or "").strip()).lower()
    return t


def _topic_key(topic: str) -> str:
    return hashlib.sha256(_normalize_topic(topic).encode("utf-8")).hexdigest()


def topic_memory_path(settings: Settings, topic: str) -> Path:
    return settings.resolved_memory_dir / f"{_topic_key(topic)}.json"


def save_topic_memory(settings: Settings, topic: str, snapshot: dict[str, Any]) -> Path:
    """Persist a topic snapshot to disk, creating the memory dir as needed."""
    path = topic_memory_path(settings, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone().isoformat()

    payload: dict[str, Any] = dict(snapshot)
    payload.setdefault("topic", topic)
    payload.setdefault("created_at", now)
    payload["updated_at"] = now

    # Paper lists are pydantic models in-process; coerce them to JSON.
    papers = payload.get("papers", [])
    payload["papers"] = [_paper_to_dict(p) for p in papers]

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("saved topic memory: %s", path)
    return path


def load_topic_memory(settings: Settings, topic: str) -> Optional[dict[str, Any]]:
    """Load a previously saved topic snapshot, or None if absent/corrupt."""
    path = topic_memory_path(settings, topic)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        # Keep papers as plain dicts (the JSON on-disk representation). Callers
        # that need Paper objects can rehydrate them explicitly.
        data["papers"] = [dict(p) for p in data.get("papers", []) if isinstance(p, dict)]
        return data
    except Exception as exc:
        log.warning("failed to load topic memory %s: %s", path, exc)
        return None


def inject_memory_context(settings: Settings, topic: str) -> tuple[Optional[dict[str, Any]], str]:
    """Load prior work and render a compact system-prompt context block.

    Returns ``(snapshot, context_text)``. ``context_text`` is empty when there
    is no usable memory.
    """
    snapshot = load_topic_memory(settings, topic)
    if snapshot is None:
        return None, ""

    lines: list[str] = [
        "Prior work on this topic is available. Reuse it where useful, but verify and extend it with fresh searches.",
        f"Previous report summary: {snapshot.get('topic_summary') or '(none)'}",
    ]
    prev_sections = snapshot.get("sections") or {}
    if prev_sections:
        lines.append("Previous section drafts:")
        for name, body in prev_sections.items():
            excerpt = str(body)[:600].replace("\n", " ")
            lines.append(f"- {name}: {excerpt}")
    prev_papers = snapshot.get("papers") or []
    if prev_papers:
        lines.append("Previously collected papers (title/year/source):")
        for p in prev_papers[:20]:
            title = getattr(p, "title", "") or (p.get("title", "") if isinstance(p, dict) else "")
            year = getattr(p, "year", None) or (p.get("year", None) if isinstance(p, dict) else None)
            source = getattr(p, "source", "") or (p.get("source", "") if isinstance(p, dict) else "")
            if title:
                lines.append(f"- {title} ({year or 'n.d.'}, {source or 'unknown'})")
    return snapshot, "\n".join(lines)


def _paper_to_dict(p: Any) -> dict[str, Any]:
    if isinstance(p, Paper):
        return p.model_dump()
    if isinstance(p, dict):
        return dict(p)
    return {}

