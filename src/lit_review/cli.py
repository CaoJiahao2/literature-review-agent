"""typer CLI entrypoint.

The CLI is a thin layer on top of :func:`lit_review.runner.run`. It only:

* parses command-line arguments
* prints status panels + progress
* surfaces warnings (per-source failures, LLM configuration errors)

All real logic — ReAct loop, tool execution, memory, reflection, metrics
emission, report writing — lives in :mod:`runner` / :mod:`agent`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from .config import ConfigurationError, load_settings
from .agent.reviewer import AgentRunError
from .llm import require_llm
from .runner import run as runner_run
from .state import AgentState

console = Console()
app = typer.Typer(
    name="lit-review",
    help="AI-focused literature review agent. Output: a Markdown report.",
    no_args_is_help=True,
    add_completion=False,
    invoke_without_command=True,
)


def _looks_cjk(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", s))


def _parse_years(spec: str) -> tuple[int, int]:
    m = re.match(r"^(\d{4})\.\.(\d{4})$", spec)
    if not m:
        raise typer.BadParameter("--years must be of the form YYYY..YYYY (e.g. 2020..2025)")
    a, b = int(m.group(1)), int(m.group(2))
    if a > b:
        raise typer.BadParameter("--years: left year must be <= right year")
    return (a, b)


def _do_run(
    topic: str,
    output: Path,
    language: Optional[str],
    top_k: int,
    years_spec: Optional[str],
    sources: Optional[str],
    resume: bool,
    verbose: bool,
    emit_metrics: bool = False,
    emit_state: bool = False,
) -> None:
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    settings = load_settings()
    settings.resume_memory = bool(resume)
    try:
        require_llm(settings)
    except ConfigurationError as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        raise typer.Exit(code=2)

    lang = (language or ("zh" if _looks_cjk(topic) else "en")).lower()
    if lang not in ("en", "zh"):
        raise typer.BadParameter(f"--language must be 'en' or 'zh' (got {language!r})")

    year_range = _parse_years(years_spec) if years_spec else settings.year_window()

    enabled_sources = (
        [s.strip() for s in sources.split(",") if s.strip()]
        if sources else settings.enabled_sources()
    )

    state = AgentState(
        topic=topic,
        language=lang,
        years=year_range,
        top_k=top_k,
        output_path=str(output),
        verbose=verbose,
        sources=enabled_sources,
    )

    console.print(
        Panel.fit(
            f"[bold]Topic:[/bold] {topic}\n"
            f"[bold]Language:[/bold] {lang}\n"
            f"[bold]Years:[/bold] {year_range[0]}..{year_range[1]}\n"
            f"[bold]Top-K:[/bold] {top_k}\n"
            f"[bold]Output:[/bold] {output}\n"
            f"[bold]Sources:[/bold] {', '.join(enabled_sources)}\n"
            f"[bold]Model:[/bold] {settings.llm_model}\n"
            f"[bold]Resume memory:[/bold] {'on' if resume else 'off'}\n"
            f"[bold]Max agent steps:[/bold] {settings.max_agent_steps}",
            title="Literature Review Agent",
        )
    )

    console.print("[dim]ReAct loop: plan → search tools → self-review → draft → revise → submit[/dim]")

    def _on_node(name: str, _result: dict) -> None:
        if not verbose:
            return
        if name == "agent_step":
            step = _result.get("step")
            tools = _result.get("tool_calls") or []
            console.print(f"[cyan]▶[/cyan] step {step}: {', '.join(map(str, tools)) or '(no tool call)'}")
        elif name == "submit_report":
            console.print("[green]✓[/green] submit_report")

    try:
        if verbose:
            result = runner_run(
                state,
                settings,
                emit_metrics=emit_metrics,
                emit_state=emit_state,
                on_node=_on_node,
            )
        else:
            with console.status("[bold green]Running literature review…", spinner="dots"):
                result = runner_run(
                    state,
                    settings,
                    emit_metrics=emit_metrics,
                    emit_state=emit_state,
                )
    except ConfigurationError as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        raise typer.Exit(code=2)
    except AgentRunError as exc:
        console.print(f"[bold red]Agent failed:[/bold red] {exc}")
        raise typer.Exit(code=1)

    merged = result.papers
    errors = result.errors
    if errors:
        console.print(f"[yellow]Warnings ({len(errors)}):[/yellow]")
        for e in errors[:5]:
            console.print(f"  - {e}")

    console.print(
        f"[green]Collected {result.metrics.papers_collected if result.metrics else len(merged)} papers[/green]"
        f" → kept {len(merged)} after dedupe+top-k."
    )

    out_path = result.output_path
    console.print(f"[bold green]✓ Report written to {out_path}[/bold green]")
    if emit_metrics and result.metrics is not None:
        console.print(f"[bold cyan]📊 metrics:[/bold cyan] {out_path.with_name(out_path.name + '.metrics.json')}")
    if emit_state:
        console.print(f"[bold cyan]📦 state snapshot:[/bold cyan] {out_path.with_name(out_path.name + '.state.json')}")


@app.command()
def config() -> None:
    """Print resolved configuration and exit."""
    s = load_settings()
    console.print(Panel.fit(
        "\n".join([
            f"[bold]LLM_API_KEY set:[/bold] {bool(s.has_llm())}",
            f"[bold]LLM_BASE_URL:[/bold] {s.llm_base_url}",
            f"[bold]LLM_MODEL:[/bold] {s.llm_model}",
            f"[bold]MAX_AGENT_STEPS:[/bold] {s.max_agent_steps}",
            f"[bold]MAX_REFLECTIONS:[/bold] {s.max_reflections}",
            f"[bold]RESUME_MEMORY:[/bold] {s.resume_memory}",
            f"[bold]MEMORY_DIR:[/bold] {s.resolved_memory_dir}",
            f"[bold]ARXIV_MAX_PER_QUERY:[/bold] {s.arxiv_max_per_query}",
            f"[bold]OPENALEX_MAX_PER_QUERY:[/bold] {s.openalex_max_per_query}",
            f"[bold]HUGGINGFACE_MAX_PER_QUERY:[/bold] {s.huggingface_max_per_query}",
            f"[bold]SEMANTIC_SCHOLAR_MAX_PER_QUERY:[/bold] {s.semantic_scholar_max_per_query}",
            f"[bold]CROSSREF_MAX_PER_QUERY:[/bold] {s.crossref_max_per_query}",
            f"[bold]DEFAULT_SOURCES:[/bold] {s.default_sources}",
            f"[bold]REQUEST_TIMEOUT:[/bold] {s.request_timeout}s",
            f"[bold]User-Agent:[/bold] {s.user_agent}",
            f"[bold]Default year window:[/bold] {s.year_window()[0]}..{s.year_window()[1]}",
        ]),
        title="lit-review config",
    ))


@app.command()
def review(
    topic: str = typer.Argument(..., help="Research topic (focus: AI)."),
    output: Path = typer.Option(Path("report.md"), "--output", "-o", help="Output Markdown path."),
    language: Optional[str] = typer.Option(None, "--language", "-l", help="en or zh (auto-detected)."),
    top_k: int = typer.Option(30, "--top-k", help="Number of papers to keep after ranking."),
    years: Optional[str] = typer.Option(None, "--years", help="Year range, e.g. 2020..2025."),
    sources: Optional[str] = typer.Option(
        None, "--sources", "-s",
        help="Comma-separated sources: arxiv,openalex,huggingface,semantic_scholar,crossref. "
             "Default comes from DEFAULT_SOURCES in .env.",
    ),
    resume: bool = typer.Option(False, "--resume", help="Load and reuse prior memory for this topic."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Stream agent steps."),
    emit_metrics: bool = typer.Option(
        False, "--emit-metrics",
        help="Write <report>.metrics.json with source / LLM / step stats.",
    ),
    emit_state: bool = typer.Option(
        False, "--emit-state",
        help="Write <report>.state.json with the final state snapshot. May contain LLM output verbatim.",
    ),
) -> None:
    """Generate a literature review."""
    _do_run(
        topic=topic,
        output=output,
        language=language,
        top_k=top_k,
        years_spec=years,
        sources=sources,
        resume=resume,
        verbose=verbose,
        emit_metrics=emit_metrics,
        emit_state=emit_state,
    )


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host for the Gradio server."),
    port: int = typer.Option(7860, "--port", help="Port for the Gradio server."),
    share: bool = typer.Option(False, "--share", help="Create a public Gradio link."),
) -> None:
    """Launch the Gradio web UI."""
    from .ui import launch
    launch(host=host, port=port, share=share)


@app.callback()
def _main(ctx: typer.Context) -> None:
    """When invoked with no subcommand, print help."""
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        console.print()
        console.print("[dim]Quick start:[/dim] lit-review review \"<your topic>\" --output report.md")
        console.print("[dim]Diagnostics:[/dim] lit-review review \"<topic>\" --emit-metrics")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
