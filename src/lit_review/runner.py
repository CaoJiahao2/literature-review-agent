"""Single execution entry point used by both CLI and UI.

Why a runner?
============

In v0.1, the Gradio UI imported the private helper ``cli._do_run`` directly, which
created a hard coupling: any CLI refactor broke the UI. Both CLI and UI were
also responsible for *the same* orchestration concerns (state construction,
graph invocation, error reporting, report writing) and ended up duplicating logic.

``runner.run`` is the single seam. CLI/UI build a ``GraphState`` (no business
logic), call ``run`` with the desired execution knobs, and render the
``RunResult``. ``run`` handles:

* GraphState normalization + defaults
* Graph compilation
* Streaming vs. one-shot invocation
* Metrics collection (per-node wall-clock, per-source counts, LLM token usage)
* Optional JSON metrics + state snapshots
* Error summarization

Importing this module does not require langchain / langgraph / gradio; all
heavy deps are loaded lazily so ``import lit_review.runner`` is safe in tests.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .config import Settings, load_settings
from .report.writer import write_report
from .state import GraphState, Paper

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RunResult:
    """Outcome of a single ``runner.run`` call.

    The same shape is returned whether the run streamed events or invoked the
    graph in one shot. ``metrics`` and ``state_snapshot`` are populated only
    when the corresponding flags are set on the call.
    """

    state: dict
    output_path: Path
    metrics: Optional["Metrics"] = None
    state_snapshot: Optional[dict] = None

    @property
    def papers(self) -> list[Paper]:
        return list(self.state.get("merged") or [])

    @property
    def sections(self) -> dict[str, str]:
        return dict(self.state.get("sections") or {})

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

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _normalize_state(state: GraphState, settings: Settings) -> dict:
    """Fill in defaults sourced from settings and the live clock."""
    out = dict(state)
    if not out.get("sources"):
        out["sources"] = list(settings.enabled_sources())
    if out.get("years") is None:
        out["years"] = settings.year_window()
    if out.get("language") not in ("en", "zh"):
        out["language"] = "en"
    out.setdefault("top_k", 30)
    out.setdefault("max_iter", 2)
    out.setdefault("no_llm", False)
    out.setdefault("verbose", False)
    out.setdefault("output_path", "report.md")
    out.setdefault("errors", [])
    return out


def run(
    state: GraphState,
    settings: Optional[Settings] = None,
    *,
    emit_metrics: bool = False,
    emit_state: bool = False,
    on_node: Optional[Callable[[str, dict], None]] = None,
) -> RunResult:
    """Execute the literature-review graph and write the Markdown report.

    Parameters
    ----------
    state:
        Initial ``GraphState``. Populated with whatever the caller already knows;
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
    norm = _normalize_state(state, settings)
    output = Path(norm["output_path"]).expanduser().resolve()

    metrics = Metrics(
        started_at=datetime.now().astimezone().isoformat(),
        finished_at="",
        duration_ms=0,
    )

    # Lazy import: keep ``import lit_review.runner`` cheap.
    from .graph import build_graph
    from .llm_client import LLMClient

    client = LLMClient(settings)
    # Stash client in state so nodes can pick it up via a uniform accessor.
    norm["__llm_client__"] = client

    graph = build_graph(settings)
    started = time.monotonic()

    # Stream node-by-node. This is what lets ``--verbose`` show real progress
    # via ``on_node``; the per-node partial dicts are merged cumulatively so
    # ``final`` is exactly the state ``graph.invoke()`` would have produced.
    final = dict(norm)
    for step in graph.stream(norm):
        for node_name, updates in step.items():
            final.update(updates)
            if on_node is not None:
                try:
                    on_node(node_name, updates)
                except Exception:  # pragma: no cover
                    log.warning("on_node callback failed for %s", node_name, exc_info=True)

    duration_ms = int((time.monotonic() - started) * 1000)
    metrics.duration_ms = duration_ms
    metrics.finished_at = datetime.now().astimezone().isoformat()

    # Pull counts from final state.
    papers_kept = list(final.get("merged") or [])
    metrics.papers_kept = len(papers_kept)
    for src, papers in (final.get("source_results") or {}).items():
        metrics.sources[src] = len(papers)
    metrics.papers_collected = sum(metrics.sources.values())

    for node_name, node_ms in (final.get("__node_times__") or {}).items():
        metrics.nodes[node_name] = int(node_ms)
    for sec_name, sec_body in (final.get("sections") or {}).items():
        metrics.sections[sec_name] = len(sec_body or "")
    metrics.errors = list(final.get("errors") or [])

    # Dedupe statistics come from the nodes that ran merge_and_rank; the last
    # write wins (on a refine loop that's the cumulative corpus).
    dedupe_stats = final.get("__dedupe_stats__") or {}
    metrics.merged = {
        "clusters_before": int(dedupe_stats.get("clusters_before", metrics.papers_collected)),
        "papers_after_dedupe": int(dedupe_stats.get("papers_after_dedupe", metrics.papers_kept)),
        "kept_after_topk": metrics.papers_kept,
    }

    # LLMClient aggregates are written into metrics on close.
    metrics.llm = client.snapshot()

    snapshot = dict(final) if emit_state else None

    out_path = write_report(final, output_path=output)

    if emit_metrics:
        metrics_path = out_path.with_suffix(out_path.suffix + ".metrics.json")
        if not str(metrics_path).endswith(".metrics.json"):
            metrics_path = out_path.parent / (out_path.name + ".metrics.json")
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_path.write_text(metrics.to_json(), encoding="utf-8")
        log.info("wrote metrics: %s", metrics_path)

    if emit_state:
        snapshot_path = out_path.parent / (out_path.name + ".state.json")
        snapshot_path.write_text(
            json.dumps(_safe_jsonify(snapshot or {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("wrote state snapshot: %s", snapshot_path)

    return RunResult(
        state=final,
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
    # pydantic models
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            return repr(obj)
    return repr(obj)


__all__ = ["RunResult", "Metrics", "run"]
