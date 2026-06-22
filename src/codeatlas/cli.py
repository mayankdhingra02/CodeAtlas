from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from .agent_install import install_agent
from .analysis import dead_code, http_confidence_summary, route_summary, structural_query
from .artifacts import export_graph_artifact, import_graph_artifact
from .benchmark import Benchmarker
from .external_index import import_external_index
from .graph import GraphService
from .indexer import RepositoryIndexer
from .memory import MemoryQueryEngine
from .mcp_server import run_mcp_server
from .packs import context_pack, render_context_pack
from .retrieval import RetrievalEngine
from .rules import run_rule_checks
from .source import source_outline
from .status import index_status
from .verification import verification_plan
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


@app.command("agent-context")
def agent_context_cmd(
    task: Annotated[str, typer.Argument(help="Coding task to package context for.")],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
    max_tokens: Annotated[int, typer.Option("--max-tokens", "-m", min=1)] = 5000,
) -> None:
    payload = visualization_service.agent_context(repo_path, task, max_tokens=max_tokens)
    console.print(payload["markdown"])


@app.command("context-pack")
def context_pack_cmd(
    task: Annotated[
        str | None,
        typer.Argument(help="Coding task, issue text, or PR summary to package."),
    ] = None,
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
    task_file: Annotated[
        Path | None,
        typer.Option("--task-file", help="Read task text from a local issue/PR brief file."),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: markdown, json, or xml."),
    ] = "markdown",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Optional output file."),
    ] = None,
    max_tokens: Annotated[int, typer.Option("--max-tokens", "-m", min=1)] = 6000,
) -> None:
    task_text = task_file.read_text(encoding="utf-8") if task_file else (task or "")
    pack = context_pack(repo_path, task_text, max_tokens=max_tokens)
    rendered = render_context_pack(pack, output_format=output_format)
    if output:
        output.write_text(rendered, encoding="utf-8")
        console.print(f"Wrote context pack to {output}.")
    else:
        console.print(rendered)


@app.command("export-graph")
def export_graph_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository containing .codeatlas/index.db.")] = Path("."),
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Optional artifact path. Defaults to .codeatlas/graph.db.gz."),
    ] = None,
) -> None:
    report = export_graph_artifact(repo_path, output)
    console.print(
        f"Exported CodeAtlas graph to {report.artifact_path} "
        f"({report.size_bytes:,} bytes)."
    )


@app.command("import-graph")
def import_graph_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository to receive the imported graph.")] = Path("."),
    artifact: Annotated[
        Path | None,
        typer.Option("--artifact", "-a", help="Artifact path. Defaults to .codeatlas/graph.db.gz."),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing .codeatlas/index.db."),
    ] = False,
) -> None:
    report = import_graph_artifact(repo_path, artifact, overwrite=overwrite)
    console.print(f"Imported CodeAtlas graph from {report.artifact_path} into {report.database_path}.")


@app.command("index-status")
def index_status_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository containing .codeatlas/index.db.")] = Path("."),
) -> None:
    status = index_status(repo_path)
    table = Table(title="CodeAtlas Index Status")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for key in (
        "repo_root",
        "indexed",
        "last_indexed_at",
        "files_indexed",
        "symbols",
        "graph_nodes",
        "graph_edges",
        "dirty_files",
        "new_files",
        "deleted_files",
        "stale",
        "parser_errors",
        "artifact_exists",
    ):
        if key in status:
            table.add_row(key.replace("_", " ").title(), str(status[key]))
    console.print(table)


@app.command("query")
def query_cmd(
    expression: Annotated[
        str,
        typer.Argument(help="Mini structural query, e.g. callers:login, calls:main, imports:sqlalchemy, route:/api, dead:functions."),
    ],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository containing .codeatlas/index.db."),
    ] = Path("."),
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 25,
) -> None:
    console.print_json(json.dumps(structural_query(repo_path, expression, limit=limit), default=str))


@app.command("dead-code")
def dead_code_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository containing .codeatlas/index.db.")] = Path("."),
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 50,
) -> None:
    payload = dead_code(repo_path, limit=limit)
    table = Table(title="Potential Dead Code")
    table.add_column("Symbol")
    table.add_column("File")
    table.add_column("Confidence", justify="right")
    for item in payload["items"]:
        table.add_row(
            item["qualified_name"],
            f"{item['file_path']}:{item['line_start']}",
            f"{item['confidence']:.2f}",
        )
    console.print(table)


@app.command("routes")
def routes_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository containing .codeatlas/index.db.")] = Path("."),
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 100,
) -> None:
    console.print_json(json.dumps(route_summary(repo_path, limit=limit), default=str))


@app.command("http-confidence")
def http_confidence_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository containing .codeatlas/index.db.")] = Path("."),
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 100,
) -> None:
    console.print_json(json.dumps(http_confidence_summary(repo_path, limit=limit), default=str))


@app.command("install-agent")
def install_agent_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository to configure for an AI coding agent.")] = Path("."),
    agent: Annotated[str, typer.Option("--agent", help="Agent to configure. Currently: codex.")] = "codex",
) -> None:
    payload = install_agent(repo_path, agent)
    console.print_json(json.dumps(payload, default=str))


@app.command("rules")
def rules_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository to scan with built-in rule checks.")] = Path("."),
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 100,
    severity: Annotated[
        str | None,
        typer.Option("--severity", help="Optional severity filter: high, medium, or low."),
    ] = None,
) -> None:
    console.print_json(json.dumps(run_rule_checks(repo_path, limit=limit, severity=severity), default=str))


@app.command("verify-plan")
def verify_plan_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository to inspect for local changes.")] = Path("."),
    base_ref: Annotated[str, typer.Option("--base-ref", "-b", help="Git ref to diff against.")] = "HEAD",
    task: Annotated[str, typer.Option("--task", help="Optional task summary for the plan.")] = "",
) -> None:
    console.print_json(json.dumps(verification_plan(repo_path, base_ref=base_ref, task=task), default=str))


@app.command("outline")
def outline_cmd(
    repo_path: Annotated[Path, typer.Argument(help="Repository containing .codeatlas/index.db.")] = Path("."),
    query: Annotated[str, typer.Option("--query", "-q", help="Optional file/symbol filter.")] = "",
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 80,
) -> None:
    console.print_json(json.dumps(source_outline(repo_path, query, limit=limit), default=str))


@app.command("import-index")
def import_index_cmd(
    input_path: Annotated[Path, typer.Argument(help="External code-intelligence JSON, such as SCIP JSON.")],
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", "-r", help="Repository to augment with the external index."),
    ] = Path("."),
    index_format: Annotated[
        str,
        typer.Option("--format", "-f", help="Index format: auto, scip-json, or generic-json."),
    ] = "auto",
) -> None:
    console.print_json(
        json.dumps(import_external_index(repo_path, input_path, index_format=index_format), default=str)
    )


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
    try:
        visualization_service.serve(
            repo_path,
            host=host,
            port=port,
            open_browser=open_browser,
            refresh=refresh,
            max_commits=max_commits,
        )
    except RuntimeError as exc:
        console.print(f"[bold red]Serve failed:[/bold red] {exc}")
        raise typer.Exit(1) from None


@app.command("ui-smoke")
def ui_smoke_cmd(
    url: Annotated[
        str,
        typer.Argument(help="Running CodeAtlas UI URL, for example http://127.0.0.1:8852/."),
    ] = "http://127.0.0.1:8765/",
    screenshot_dir: Annotated[
        Path | None,
        typer.Option("--screenshot-dir", help="Directory for optional Playwright screenshots."),
    ] = None,
) -> None:
    env = os.environ.copy()
    env["CODEATLAS_UI_URL"] = url
    if screenshot_dir:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        env["CODEATLAS_UI_SCREENSHOT_DIR"] = str(screenshot_dir)
    cmd = [sys.executable, "-m", "unittest", "tests.test_ui_smoke", "-v"]
    console.print(f"Running UI smoke against {url}")
    result = subprocess.run(cmd, env=env, check=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


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
