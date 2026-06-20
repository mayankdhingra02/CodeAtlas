from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from .benchmark import Benchmarker
from .graph import GraphService
from .indexer import RepositoryIndexer
from .memory import MemoryQueryEngine
from .mcp_server import run_mcp_server
from .retrieval import RetrievalEngine
from .visualization import VisualizationService
from .watcher import watch_repository

app = typer.Typer(
    name="codeatlas",
    help="Local-first code intelligence and graph retrieval for AI coding assistants.",
    no_args_is_help=True,
)
console = Console()
memory_engine = MemoryQueryEngine()
visualization_service = VisualizationService()


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


@app.command("memory")
def memory_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository path to index memory for.")] = Path("."),
    max_commits: Annotated[
        int,
        typer.Option("--max-commits", min=1, help="Maximum git commits to mine."),
    ] = 500,
    incremental: Annotated[
        bool,
        typer.Option("--incremental", "-i", help="Do not clear existing memory first."),
    ] = False,
) -> None:
    report = memory_engine.index_memory(
        repo_path, max_commits=max_commits, incremental=incremental
    )
    table = Table(title="CodeAtlas Repository Memory")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Repository", str(report.repo_root))
    table.add_row("Database", str(report.database_path))
    table.add_row("Git history", "available" if report.git_available else "not available")
    table.add_row("Duration", f"{report.duration_seconds:.3f}s")
    table.add_row("Commits indexed", str(report.commits_indexed))
    table.add_row("Documents indexed", str(report.documents_indexed))
    table.add_row("Memory entities", str(report.entities_indexed))
    table.add_row("Memory relationships", str(report.relationships_indexed))
    table.add_row("Evidence records", str(report.evidence_indexed))
    console.print(table)
    for warning in report.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


@app.command("history")
def history_cmd(
    topic: Annotated[str, typer.Argument(help="Feature, service, or architecture topic.")],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 10,
) -> None:
    events = memory_engine.history(repo_path, topic, limit=limit)
    table = Table(title=f"Repository Time Machine: {topic}")
    table.add_column("Date")
    table.add_column("Event")
    table.add_column("Confidence", justify="right")
    for event in events:
        table.add_row(event.date or "unknown", event.title, f"{event.confidence:.2f}")
    console.print(table)
    _print_evidence(events)


@app.command("ownership")
def ownership_cmd(
    topic: Annotated[str, typer.Argument(help="Feature, module, service, or path topic.")],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 10,
) -> None:
    entries = memory_engine.ownership(repo_path, topic, limit=limit)
    table = Table(title=f"Ownership Intelligence: {topic}")
    table.add_column("Developer")
    table.add_column("Commits", justify="right")
    table.add_column("Files", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Last active")
    for entry in entries:
        table.add_row(
            entry.developer,
            str(entry.commits),
            str(entry.files_touched),
            f"{entry.expertise_score:.1f}",
            entry.last_active or "unknown",
        )
    console.print(table)
    _print_evidence(entries)


@app.command("decisions")
def decisions_cmd(
    question: Annotated[str, typer.Argument(help="Decision question to answer from evidence.")],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
) -> None:
    answers = memory_engine.decisions(repo_path, question)
    for answer in answers:
        console.rule(f"Decision confidence={answer.confidence:.2f}")
        console.print(answer.answer)
    _print_evidence(answers)


@app.command("architecture")
def architecture_cmd(
    topic: Annotated[str, typer.Argument(help="Architecture topic to explain.")],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
) -> None:
    findings = memory_engine.architecture(repo_path, topic)
    for finding in findings:
        console.rule(f"Architecture confidence={finding.confidence:.2f}")
        console.print(finding.summary)
    _print_evidence(findings)


@app.command("repo-context")
def repo_context_cmd(
    query: Annotated[str, typer.Argument(help="Context question for the repository memory layer.")],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
    max_tokens: Annotated[int, typer.Option("--max-tokens", "-m", min=1)] = 4000,
) -> None:
    context = memory_engine.compressed_context(repo_path, query, max_tokens=max_tokens)
    console.print_json(json.dumps(asdict(context), default=str))


@app.command("impact")
def impact_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
    base_ref: Annotated[
        str,
        typer.Option("--base-ref", "-b", help="Git ref to diff against."),
    ] = "HEAD",
) -> None:
    report = memory_engine.impact(repo_path, base_ref=base_ref)
    console.rule(f"Impact Radius: {report.risk_level}")
    console.print(report.summary)
    for warning in report.warnings:
        console.print(f"[yellow]{warning}[/yellow]")
    table = Table(title="Changed Files")
    table.add_column("File")
    table.add_column("Status")
    table.add_column("Component")
    table.add_column("Risk")
    table.add_column("Owners")
    table.add_column("Co-changes")
    for item in report.impacted_files:
        table.add_row(
            item.file_path,
            item.status,
            item.component,
            item.risk,
            ", ".join(owner.developer for owner in item.owners) or "unknown",
            ", ".join(link.related_file_path for link in item.related_files[:3]) or "none",
        )
    console.print(table)
    console.rule("Token Savings")
    console.print(f"Full changed-file context: {report.token_report.baseline_tokens:,} tokens")
    console.print(f"Impact context used: {report.token_report.optimized_tokens:,} tokens")
    console.print(f"Saved: {report.token_report.savings_percent:.0f}%")


@app.command("hotspots")
def hotspots_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 10,
) -> None:
    hotspots = memory_engine.hotspots(repo_path, limit=limit)
    table = Table(title="Repository Hotspots")
    table.add_column("Component")
    table.add_column("Commits", justify="right")
    table.add_column("Authors", justify="right")
    table.add_column("Files", justify="right")
    table.add_column("Risk score", justify="right")
    table.add_column("Last changed")
    for item in hotspots:
        table.add_row(
            item.component,
            str(item.commits),
            str(item.authors),
            str(item.files),
            f"{item.risk_score:.1f}",
            item.last_changed or "unknown",
        )
    console.print(table)


@app.command("nexus")
def nexus_cmd(
    topic: Annotated[str, typer.Argument(help="Component, feature, path, or service topic.")],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
) -> None:
    summary = memory_engine.component_summary(repo_path, topic)
    console.rule(f"Git Nexus: {topic}")
    console.print(summary.summary)
    console.print(f"Commits: {summary.commits}")
    console.print(f"Authors: {', '.join(summary.authors) or 'unknown'}")
    if summary.files:
        console.print("Files:")
        for file_path in summary.files:
            console.print(f"- {file_path}")
    if summary.related_files:
        console.print("Co-change links:")
        for link in summary.related_files:
            console.print(
                f"- {link.file_path} -> {link.related_file_path} "
                f"({link.commits} commits, confidence {link.confidence:.2f})"
            )
    _print_evidence((summary,))


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


@app.command("serve")
def serve_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(help="Repository to map in a local browser UI."),
    ] = Path("."),
    host: Annotated[
        str,
        typer.Option("--host", help="Host interface for the local visualization server."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Preferred local visualization port."),
    ] = 8765,
    open_browser: Annotated[
        bool,
        typer.Option("--open/--no-open", help="Open the visualization page in the browser."),
    ] = True,
    refresh: Annotated[
        bool,
        typer.Option("--refresh/--no-refresh", help="Refresh code and git memory before serving."),
    ] = True,
    max_commits: Annotated[
        int,
        typer.Option("--max-commits", min=1, help="Maximum commits to mine for the commit map."),
    ] = 1000,
) -> None:
    visualization_service.serve(
        repo_path,
        host=host,
        port=port,
        open_browser=open_browser,
        refresh=refresh,
        max_commits=max_commits,
    )


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


def _print_evidence(items: object) -> None:
    evidence = []
    for item in items:
        evidence.extend(getattr(item, "evidence", ()))
    if not evidence:
        return
    seen: set[tuple[str, str, str | None]] = set()
    console.rule("Evidence")
    for ref in evidence:
        key = (ref.source_type, ref.source_id, ref.path)
        if key in seen:
            continue
        seen.add(key)
        location = ref.path or ref.source_id[:12]
        timestamp = f" {ref.timestamp}" if ref.timestamp else ""
        console.print(f"[bold]{ref.source_type}[/bold] {location}{timestamp}: {ref.title}")
        console.print(f"[dim]{ref.snippet}[/dim]")
