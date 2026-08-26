"""Single execution entry point used by both CLI and UI.

``runner.run`` is the single seam. CLI/UI build an ``AgentState`` (no business
logic), call ``run`` with the desired execution knobs, and render the
``RunResult``. ``run`` handles:

* AgentState normalization + defaults
* The ReAct agent loop
* Metrics collection (sources, papers, LLM usage, steps/tool calls/reflections)
* Optional JSON metrics + state snapshots

Importing this module does not require langchain / gradio; all
heavy deps are loaded lazily so ``import lit_review.runner`` is safe in tests.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .config import Settings, load_settings
from .llm import require_llm
from .state import AgentState, Paper

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RunResult:
    """Outcome of a single ``runner.run`` call."""

    state: AgentState
    output_path: Path
    metrics: Optional["Metrics"] = None
    state_snapshot: Optional[dict] = None

    @property
    def papers(self) -> list[Paper]:
        return list(self.state.get("merged") or self.state.get("papers") or [])

    @property
    def sections(self) -> dict[str, str]:
        return dict(self.state.get("sections") or self.state.get("drafts") or {})

    @property
    def errors(self) -> list[str]:
        return list(self.state.get("errors") or [])


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


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
    steps: int = 0
    tool_calls: int = 0
    reflections: int = 0
    max_steps_reached: bool = False

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _normalize_state(state: AgentState, settings: Settings) -> AgentState:
    """Fill in defaults sourced from settings and the live clock."""
    out = AgentState(**dict(state))
    if not out.get("sources"):
        out["sources"] = list(settings.enabled_sources())
    if out.get("years") is None:
        out["years"] = settings.year_window()
    if out.get("language") not in ("en", "zh"):
        out["language"] = "en"
    out.setdefault("top_k", 30)
    out.setdefault("output_path", "report.md")
    out.setdefault("verbose", False)
    out.setdefault("step", 0)
    out.setdefault("tool_calls", 0)
    out.setdefault("done", False)
    out.setdefault("errors", [])
    out.setdefault("drafts", {})
    out.setdefault("sections", {})
    out.setdefault("messages", [])
    out.setdefault("papers", [])
    out.setdefault("reflections", [])
    out.setdefault("merged", [])
    out.setdefault("source_counts", {})
    return out


def run(
    state: AgentState,
    settings: Optional[Settings] = None,
    *,
    emit_metrics: bool = False,
    emit_state: bool = False,
    on_node: Optional[Callable[[str, dict], None]] = None,
) -> RunResult:
    """Execute the ReAct literature-review agent and write the Markdown report.

    Parameters
    ----------
    state:
        Initial ``AgentState``. Populated with whatever the caller already knows;
        :func:`_normalize_state` fills in the rest.
    settings:
        Optional pre-built ``Settings``. Defaults to :func:`load_settings`.
    emit_metrics:
        When True, write ``<output_path>.metrics.json`` alongside the report.
    emit_state:
        When True, write ``<output_path>.state.json`` (debug dump of the final
        state; **may contain LLM output verbatim**).
    on_node:
        Optional progress callback: ``on_node(node_name, node_result)``.

    Returns
    -------
    RunResult
    """
    settings = settings or load_settings()
    require_llm(settings)

    norm = _normalize_state(state, settings)
    output = Path(norm["output_path"]).expanduser().resolve()

    metrics = Metrics(
        started_at=datetime.now().astimezone().isoformat(),
        finished_at="",
        duration_ms=0,
    )

    # Lazy import: keep ``import lit_review.runner`` cheap.
    from .agent import ReviewAgent

    started = time.monotonic()
    try:
        ReviewAgent(settings, norm).run(on_node=on_node)
    finally:
        duration_ms = int((time.monotonic() - started) * 1000)
        metrics.duration_ms = duration_ms
        metrics.finished_at = datetime.now().astimezone().isoformat()

    # Pull counts from final agent state.
    papers_kept = list(norm.get("merged") or [])
    papers_collected = list(norm.get("papers") or [])
    metrics.papers_collected = len(papers_collected)
    metrics.papers_kept = len(papers_kept)
    metrics.sources = dict(norm.get("source_counts") or {})
    metrics.steps = int(norm.get("step", 0))
    metrics.tool_calls = int(norm.get("tool_calls", 0))
    metrics.reflections = len(norm.get("reflections") or [])
    metrics.max_steps_reached = bool(norm.get("max_steps_reached", False))
    metrics.errors = list(norm.get("errors") or [])
    metrics.llm = dict(norm.get("llm_usage") or {})
    metrics.merged = {
        "papers_collected": metrics.papers_collected,
        "papers_after_dedupe": metrics.papers_kept,
        "kept_after_topk": metrics.papers_kept,
    }
    for sec_name, sec_body in (norm.get("sections") or norm.get("drafts") or {}).items():
        metrics.sections[sec_name] = len(sec_body or "")
    metrics.nodes = {
        "agent_steps": metrics.steps,
        "submit_report": 1 if norm.get("done") else 0,
    }

    # The agent writes the report through submit_report; get the final path.
    out_path = Path(norm.get("output_path", str(output))).expanduser().resolve()
    if not out_path.exists():
        raise RuntimeError("Agent finished but the report file was not created: " + str(out_path))

    if emit_metrics:
        metrics_path = out_path.parent / (out_path.name + ".metrics.json")
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_path.write_text(metrics.to_json(), encoding="utf-8")
        log.info("wrote metrics: %s", metrics_path)

    snapshot = dict(norm) if emit_state else None
    if emit_state:
        snapshot_path = out_path.parent / (out_path.name + ".state.json")
        snapshot_path.write_text(
            json.dumps(_safe_jsonify(snapshot or {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("wrote state snapshot: %s", snapshot_path)

    return RunResult(
        state=norm,
        output_path=out_path,
        metrics=metrics if emit_metrics else None,
        state_snapshot=snapshot,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_jsonify(obj: Any) -> Any:
    """Recursively coerce non-JSON values into strings for the state dump."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _safe_jsonify(v) for k, v in obj.items() if not str(k).startswith("__")}
    if isinstance(obj, (list, tuple, set)):
        return [_safe_jsonify(v) for v in obj]
    if dataclasses.is_dataclass(obj):
        return _safe_jsonify(dataclasses.asdict(obj))
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            return repr(obj)
    return repr(obj)


__all__ = ["RunResult", "Metrics", "run"]
