from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from .benchmark import Benchmarker
from .graph import GraphService
from .indexer import RepositoryIndexer
from .mcp_server import run_mcp_server
from .retrieval import RetrievalEngine
from .watcher import watch_repository

app = typer.Typer(
    name="codeatlas",
    help="Local-first code intelligence and graph retrieval for AI coding assistants.",
    no_args_is_help=True,
)
console = Console()


@app.command("index")
def index_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(help="Repository path to index."),
    ] = Path("."),
    incremental: Annotated[
        bool,
        typer.Option("--incremental", "-i", help="Only reprocess changed files."),
    ] = False,
) -> None:
    report = RepositoryIndexer().index(repo_path, incremental=incremental)
    table = Table(title="CodeAtlas Index")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Repository", str(report.repo_root))
    table.add_row("Database", str(report.database_path))
    table.add_row("Mode", "incremental" if incremental else "full rebuild")
    table.add_row("Duration", f"{report.duration_seconds:.3f}s")
    table.add_row("Files scanned", str(report.files_scanned))
    table.add_row("Files indexed", str(report.files_indexed))
    table.add_row("Files skipped", str(report.files_skipped))
    table.add_row("Files deleted", str(report.files_deleted))
    table.add_row("Symbols indexed", str(report.symbols_indexed))
    table.add_row("Graph edges", str(report.edges_indexed))
    console.print(table)
    if report.parser_errors:
        console.print("[bold yellow]Parser errors[/bold yellow]")
        for error in report.parser_errors:
            console.print(f"- {error}")


@app.command("context")
def context_cmd(
    query: Annotated[str, typer.Argument(help="Symbol or phrase to retrieve context for.")],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
    depth: Annotated[int, typer.Option("--depth", "-d", min=0, help="Graph traversal depth.")] = 2,
    max_tokens: Annotated[
        int,
        typer.Option("--max-tokens", "-m", min=1, help="Approximate token budget."),
    ] = 8000,
) -> None:
    result = RetrievalEngine().retrieve(repo_path, query, depth=depth, max_tokens=max_tokens)
    for snippet in result.snippets:
        console.rule(
            f"{snippet.file_path}:{snippet.line_start}-{snippet.line_end} "
            f"{snippet.qualified_name} score={snippet.score:.1f}"
        )
        console.print(f"[dim]{snippet.reason}[/dim]")
        console.print(Syntax(snippet.code, "python", line_numbers=True, start_line=snippet.line_start))

    report = result.token_report
    console.rule("Token Report")
    console.print(f"Baseline: {report.baseline_tokens:,} tokens")
    console.print(f"Optimized: {report.optimized_tokens:,} tokens")
    console.print(f"Savings: {report.savings_percent:.0f}%")
    console.print(
        "[dim]"
        f"Lookup {result.timings.symbol_lookup_ms:.1f}ms, "
        f"graph {result.timings.graph_traversal_ms:.1f}ms, "
        f"total {result.timings.total_ms:.1f}ms"
        "[/dim]"
    )


@app.command("graph")
def graph_cmd(
    symbol_name: Annotated[str, typer.Argument(help="Symbol to inspect.")],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
    depth: Annotated[int, typer.Option("--depth", "-d", min=0)] = 1,
) -> None:
    payload = GraphService().neighborhood(repo_path, symbol_name, depth=depth)
    console.print_json(json.dumps(payload, default=str))


@app.command("benchmark")
def benchmark_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository to benchmark.")] = Path("."),
    query: Annotated[
        str | None,
        typer.Option("--query", "-q", help="Query to use for warm retrieval measurement."),
    ] = None,
) -> None:
    report = Benchmarker().run(repo_path, query=query)
    table = Table(title="CodeAtlas Benchmark")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Repository", str(report.repo_root))
    table.add_row("Query", report.query or "(none)")
    table.add_row("Cold start indexing", f"{report.indexing_time_seconds:.3f}s")
    table.add_row("Warm start query", f"{report.retrieval_latency_ms:.1f}ms")
    table.add_row("Graph traversal", f"{report.graph_traversal_ms:.1f}ms")
    table.add_row("Files scanned", str(report.files_scanned))
    table.add_row("Files returned", str(report.files_returned))
    table.add_row("Baseline tokens", f"{report.estimated_tokens_before:,}")
    table.add_row("Optimized tokens", f"{report.estimated_tokens_after:,}")
    table.add_row("Token reduction", f"{report.token_reduction_percent:.0f}%")
    table.add_row("Retrieval accuracy", report.retrieval_accuracy)
    console.print(table)
    if report.extra.get("parser_errors"):
        console.print("[bold yellow]Parser errors were recorded; benchmark excludes failed files.[/bold yellow]")


@app.command("watch")
def watch_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository to watch.")] = Path("."),
) -> None:
    repo_root = repo_path.expanduser().resolve()
    console.print(f"Watching {repo_root} for incremental CodeAtlas updates. Press Ctrl+C to stop.")
    RepositoryIndexer().index(repo_root, incremental=True)
    watch_repository(repo_root)


@app.command("stats")
def stats_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
) -> None:
    stats = RetrievalEngine().repository_stats(repo_path)
    table = Table(title="CodeAtlas Stats")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Repository", str(stats.repo_root))
    table.add_row("Files indexed", str(stats.files_indexed))
    table.add_row("Classes", str(stats.classes))
    table.add_row("Functions", str(stats.functions))
    table.add_row("Methods", str(stats.methods))
    table.add_row("Graph nodes", str(stats.graph_nodes))
    table.add_row("Graph edges", str(stats.graph_edges))
    table.add_row("Imports", str(stats.imports))
    table.add_row("Index size", f"{stats.index_size_bytes:,} bytes")
    table.add_row("Last indexed", stats.last_indexed_at or "unknown")
    table.add_row("Supported languages", ", ".join(stats.supported_languages))
    console.print(table)


@app.command("mcp")
def mcp_cmd(
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
) -> None:
    run_mcp_server(repo_path)
