"""Gradio web UI for the literature-review agent.

Run with: `lit-review ui [--host 127.0.0.1] [--port 7860] [--share]`

The UI exposes the same knobs as the CLI (``topic``, language, top-k, year
range, sources) and renders the final Markdown report in-place with a
download button.

v0.2 changes
============
* Delegates the actual run to :func:`lit_review.runner.run`, removing the
  v0.1 import leak from ``cli._do_run``.
* ``--emit-metrics`` is exposed via the runner; the UI surfaces the JSON as
  a separate download link when ``--emit-metrics`` is set.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path


def _strip_socks_proxies() -> None:
    """Gradio's internal httpx doesn't support socks:// proxies; strip them.

    Many dev/CI envs set ``ALL_PROXY=socks://...`` which breaks any default
    ``httpx.Client``. We only strip ``socks*`` schemes; standard ``http://``
    proxies are kept.
    """
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        val = os.environ.get(key, "")
        if val.startswith(("socks://", "socks5://", "socks4://")):
            os.environ.pop(key, None)
            os.environ.pop(key.upper(), None)


_strip_socks_proxies()

import gradio as gr  # noqa: E402  (must come after _strip_socks_proxies)

from .config import load_settings
from .runner import run as runner_run
from .state import GraphState

log = logging.getLogger(__name__)


SOURCE_CHOICES = ["arxiv", "openalex", "huggingface", "semantic_scholar", "crossref"]
SOURCE_LABELS = {
    "arxiv": "arXiv (preprints, AI-heavy)",
    "openalex": "OpenAlex (broad, citation counts)",
    "huggingface": "Hugging Face Daily Papers (curated AI)",
    "semantic_scholar": "Semantic Scholar (rich metadata, gated)",
    "crossref": "Crossref (DOI/citation metadata)",
}


def _looks_cjk(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", s))


def _parse_years(spec: str) -> tuple[int, int]:
    m = re.match(r"^(\d{4})\.\.(\d{4})$", spec)
    if not m:
        raise ValueError("--years must be of the form YYYY..YYYY (e.g. 2020..2025)")
    a, b = int(m.group(1)), int(m.group(2))
    if a > b:
        raise ValueError("--years: left year must be <= right year")
    return (a, b)


def _run_ui(
    topic: str,
    language: str,
    top_k: int,
    year_start: int,
    year_end: int,
    max_iter: int,
    sources: list[str],
    no_llm: bool,
    progress=gr.Progress(track_tqdm=False),
) -> tuple[str, str, str | None, str | None]:
    """Run the agent via the unified :func:`runner.run` and return result artifacts."""
    if not topic.strip():
        return "", "❌ Topic is required.", None, None

    settings = load_settings()
    output_dir = Path("reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_topic = re.sub(r"[^A-Za-z0-9._-]+", "_", topic.strip())[:60]
    output = output_dir / f"{safe_topic}.md"

    progress(0.05, "Planning queries…")
    enabled = [s for s in sources if s in SOURCE_CHOICES]
    if not enabled:
        enabled = settings.enabled_sources()

    lang = (language or ("zh" if _looks_cjk(topic) else "en")).lower()

    try:
        year_range = (int(year_start), int(year_end))
        if year_range[0] > year_range[1]:
            return "", "❌ Invalid year range.", None, None
    except Exception:
        return "", "❌ Invalid year range.", None, None

    progress(0.15, f"Searching {len(enabled)} sources concurrently…")
    state = GraphState(
        topic=topic.strip(),
        language=lang,
        years=year_range,
        top_k=int(top_k),
        max_iter=int(max_iter),
        no_llm=bool(no_llm),
        output_path=str(output),
        verbose=False,
        sources=enabled,
    )

    try:
        result = runner_run(state, settings, emit_metrics=True)
    except Exception as exc:  # pragma: no cover
        log.exception("UI run failed")
        return "", f"❌ Error: {exc}", None, None

    if not result.output_path.exists():
        return "", "❌ Report file was not created.", None, None

    progress(1.0, "Done.")
    md = result.output_path.read_text(encoding="utf-8")
    status = (
        f"✅ Report ready ({result.output_path.stat().st_size:,} bytes, "
        f"{md.count(chr(10))} lines, "
        f"{len(result.papers)} unique papers kept)"
    )
    metrics_path = result.output_path.parent / (result.output_path.name + ".metrics.json")
    metrics_link = str(metrics_path) if metrics_path.exists() else None
    return md, status, str(result.output_path), metrics_link


def build_interface() -> gr.Blocks:
    settings = load_settings()
    default_sources = settings.enabled_sources()

    with gr.Blocks(title="Literature Review Agent") as demo:
        gr.Markdown(
            """
            # 📚 Literature Review Agent
            Enter an AI research topic and get a structured Markdown literature review.
            Sources: **arXiv**, **OpenAlex**, **HuggingFace Daily Papers**, **Semantic Scholar**, **Crossref**.
            """
        )

        with gr.Row():
            with gr.Column(scale=2):
                topic = gr.Textbox(label="Topic", placeholder="e.g. retrieval-augmented generation with knowledge graphs", lines=2)
                with gr.Row():
                    language = gr.Dropdown(["en", "zh"], value="en", label="Language")
                    top_k = gr.Slider(5, 80, value=20, step=1, label="Top-K papers")
                with gr.Row():
                    year_start = gr.Number(value=settings.year_window()[0], precision=0, label="From year")
                    year_end = gr.Number(value=settings.year_window()[1], precision=0, label="To year")
                with gr.Row():
                    max_iter = gr.Slider(0, 4, value=2, step=1, label="Max refine iterations")
                    no_llm = gr.Checkbox(value=False, label="Skeleton-only (no LLM)")
                sources = gr.CheckboxGroup(
                    choices=[(f"{SOURCE_LABELS[s]}", s) for s in SOURCE_CHOICES],
                    value=default_sources,
                    label="Sources",
                )
                run_btn = gr.Button("Generate Report", variant="primary")

            with gr.Column(scale=3):
                status = gr.Markdown("_Ready._")
                report_md = gr.Markdown()
                with gr.Row():
                    download = gr.File(label="Download Markdown", interactive=False)
                    metrics_link = gr.File(label="Download metrics.json", interactive=False)

        run_btn.click(
            _run_ui,
            inputs=[topic, language, top_k, year_start, year_end, max_iter, sources, no_llm],
            outputs=[report_md, status, download, metrics_link],
        )

    return demo


def launch(*, host: str = "127.0.0.1", port: int = 7860, share: bool = False) -> None:
    _strip_socks_proxies()
    demo = build_interface()
    demo.launch(
        server_name=host,
        server_port=port,
        share=share,
        show_error=True,
        theme=gr.themes.Soft(),
    )
