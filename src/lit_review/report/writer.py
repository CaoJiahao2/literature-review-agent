"""Write the final Markdown report to disk."""

from __future__ import annotations

import logging
from pathlib import Path

from ..state import today_iso
from .template import render_report

log = logging.getLogger(__name__)


def write_report(state: dict, *, output_path: Path | None = None) -> Path:
    """Render the Markdown report and write it to `output_path`.

    If `output_path` is None, uses `state["output_path"]`.
    Returns the resolved absolute path.
    """
    out = Path(output_path or state["output_path"]).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    md = render_report(
        topic=state["topic"],
        language=state.get("language", "en"),
        generated_on=today_iso(),
        sections=state.get("sections", {}) or {},
        references=state.get("merged", []) or [],
    )
    out.write_text(md, encoding="utf-8")
    log.info("wrote report: %s (%d bytes)", out, out.stat().st_size)
    return out
