"""typer CLI entrypoint."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import Settings, load_settings
from .graph import build_graph
from .llm import warn_if_no_llm
from .report.writer import write_report
from .state import GraphState

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
    max_iter: int,
    sources: Optional[str],
    no_llm: bool,
    verbose: bool,
) -> None:
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    settings = load_settings()
    warn_if_no_llm(settings, forced=no_llm)

    lang = (language or ("zh" if _looks_cjk(topic) else "en")).lower()
    if lang not in ("en", "zh"):
        raise typer.BadParameter(f"--language must be 'en' or 'zh' (got {language!r})")

    year_range = _parse_years(years_spec) if years_spec else settings.year_window()

    enabled_sources = (
        [s.strip() for s in sources.split(",") if s.strip()]
        if sources else settings.enabled_sources()
    )

    state: GraphState = GraphState(
        topic=topic,
        language=lang,
        years=year_range,
        top_k=top_k,
        max_iter=max_iter,
        no_llm=no_llm,
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
            f"[bold]LLM:[/bold] {'off' if (no_llm or not settings.has_llm()) else settings.llm_model}",
            title="Literature Review",
        )
    )

    graph = build_graph(settings)
    final_state: dict = {}
    if verbose:
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as progress:
            task = progress.add_task("Running literature review…", total=None)
            for step in graph.stream(state):
                node_name = next(iter(step.keys()))
                progress.update(task, description=f"node: {node_name}")
                final_state.update(step[node_name])
    else:
        with console.status("[bold green]Running literature review…", spinner="dots"):
            final_state = graph.invoke(state)

    merged = final_state.get("merged", []) or []
    errors = final_state.get("errors", []) or []
    if errors:
        console.print(f"[yellow]Warnings ({len(errors)}):[/yellow]")
        for e in errors[:5]:
            console.print(f"  - {e}")

    console.print(f"[green]Collected {len(merged)} papers.[/green]")

    out_path = write_report(final_state, output_path=output)
    console.print(f"[bold green]✓ Report written to {out_path}[/bold green]")


@app.command()
def config() -> None:
    """Print resolved configuration and exit."""
    s = load_settings()
    console.print(Panel.fit(
        "\n".join([
            f"[bold]LLM_API_KEY set:[/bold] {bool(s.has_llm())}",
            f"[bold]LLM_BASE_URL:[/bold] {s.llm_base_url}",
            f"[bold]LLM_MODEL:[/bold] {s.llm_model}",
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
    max_iter: int = typer.Option(2, "--max-iter", help="Max search-refinement iterations."),
    sources: Optional[str] = typer.Option(
        None, "--sources", "-s",
        help="Comma-separated sources: arxiv,openalex,huggingface,semantic_scholar,crossref. "
             "Default comes from DEFAULT_SOURCES in .env.",
    ),
    no_llm: bool = typer.Option(False, "--no-llm", help="Force skeleton-only output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Stream node-by-node progress."),
) -> None:
    """Generate a literature-review report on TOPIC and write it to OUTPUT."""
    _do_run(topic, output, language, top_k, years, max_iter, sources, no_llm, verbose)


@app.command()
def generate(
    topic: str = typer.Argument(..., help="Research topic (focus: AI)."),
    output: Path = typer.Option(Path("report.md"), "--output", "-o", help="Output Markdown path."),
    language: Optional[str] = typer.Option(None, "--language", "-l", help="en or zh (auto-detected)."),
    top_k: int = typer.Option(30, "--top-k", help="Number of papers to keep after ranking."),
    years: Optional[str] = typer.Option(None, "--years", help="Year range, e.g. 2020..2025."),
    max_iter: int = typer.Option(2, "--max-iter", help="Max search-refinement iterations."),
    sources: Optional[str] = typer.Option(
        None, "--sources", "-s",
        help="Comma-separated sources: arxiv,openalex,huggingface,semantic_scholar,crossref.",
    ),
    no_llm: bool = typer.Option(False, "--no-llm", help="Force skeleton-only output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Stream node-by-node progress."),
) -> None:
    """Alias for `lit-review review`."""
    _do_run(topic, output, language, top_k, years, max_iter, sources, no_llm, verbose)


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
