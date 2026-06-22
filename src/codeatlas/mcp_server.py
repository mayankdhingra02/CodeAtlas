from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .analysis import dead_code, http_confidence_summary, route_summary, structural_query
from .artifacts import export_graph_artifact, import_graph_artifact
from .external_index import import_external_index
from .memory import MemoryQueryEngine
from .packs import context_pack, render_context_pack
from .retrieval import RetrievalEngine
from .rules import run_rule_checks
from .source import source_outline
from .status import index_status
from .verification import verification_plan
from .visualization import VisualizationService


def create_tool_handlers(repo_path: str | Path = ".") -> dict[str, Callable[..., Any]]:
    engine = RetrievalEngine()
    memory = MemoryQueryEngine()
    visualization = VisualizationService()

    def get_code_context(query: str, max_tokens: int = 8000, depth: int = 2) -> dict[str, Any]:
        result = engine.retrieve(repo_path, query, depth=depth, max_tokens=max_tokens)
        return {
            "query": result.query,
            "snippets": [snippet.__dict__ for snippet in result.snippets],
            "token_report": result.token_report.__dict__
            | {"savings_percent": result.token_report.savings_percent},
            "timings": result.timings.__dict__,
        }

    def find_symbol(symbol_name: str) -> list[dict[str, Any]]:
        return engine.find_symbol(repo_path, symbol_name)

    def explain_dependencies(symbol_name: str) -> dict[str, Any]:
        return engine.explain_dependencies(repo_path, symbol_name).__dict__

    def token_report(query: str) -> dict[str, Any]:
        report = engine.token_report(repo_path, query)
        return report.__dict__ | {"savings_percent": report.savings_percent}

    def repository_stats() -> dict[str, Any]:
        stats = engine.repository_stats(repo_path)
        return stats.__dict__ | {
            "repo_root": str(stats.repo_root),
            "database_path": str(stats.database_path),
        }

    def get_context(query: str, max_tokens: int = 4000) -> dict[str, Any]:
        return asdict(memory.compressed_context(repo_path, query, max_tokens=max_tokens))

    def get_history(topic: str, limit: int = 10) -> list[dict[str, Any]]:
        return [asdict(event) for event in memory.history(repo_path, topic, limit=limit)]

    def get_architecture(topic: str, limit: int = 8) -> list[dict[str, Any]]:
        return [asdict(finding) for finding in memory.architecture(repo_path, topic, limit=limit)]

    def get_ownership(topic: str, limit: int = 10) -> list[dict[str, Any]]:
        return [asdict(entry) for entry in memory.ownership(repo_path, topic, limit=limit)]

    def get_dependencies(symbol_name: str) -> dict[str, Any]:
        try:
            dependencies = engine.explain_dependencies(repo_path, symbol_name).__dict__
        except Exception as exc:
            dependencies = {"error": str(exc)}
        return {
            "symbol_name": symbol_name,
            "dependencies": dependencies,
            "evidence": "Derived from the persisted CodeAtlas code graph.",
        }

    def get_api_flow(query: str) -> dict[str, Any]:
        evidence_query = f"{query} api route endpoint producer consumer event kafka database"
        evidence = memory.search_memory(repo_path, evidence_query, limit=10)
        if not evidence:
            return {
                "query": query,
                "confidence": 0.0,
                "flow": [],
                "answer": "No evidence-backed API flow was found in indexed memory.",
                "evidence": [],
            }
        return {
            "query": query,
            "confidence": 0.45,
            "flow": [],
            "answer": (
                "CodeAtlas found related API or infrastructure evidence, but endpoint-level "
                "flow extraction is still conservative. Use the evidence to guide inspection."
            ),
            "evidence": evidence,
        }

    def get_decisions(question: str, limit: int = 5) -> list[dict[str, Any]]:
        return [asdict(answer) for answer in memory.decisions(repo_path, question, limit=limit)]

    def search_memory(query: str, limit: int = 10) -> list[dict[str, Any]]:
        return memory.search_memory(repo_path, query, limit=limit)

    def get_impact(base_ref: str = "HEAD") -> dict[str, Any]:
        return asdict(memory.impact(repo_path, base_ref=base_ref))

    def get_hotspots(limit: int = 10) -> list[dict[str, Any]]:
        return [asdict(item) for item in memory.hotspots(repo_path, limit=limit)]

    def get_nexus(topic: str) -> dict[str, Any]:
        return asdict(memory.component_summary(repo_path, topic))

    def get_visual_map() -> dict[str, Any]:
        return visualization.build_map(repo_path)

    def get_index_status() -> dict[str, Any]:
        return index_status(repo_path)

    def query_code_graph(expression: str, limit: int = 25) -> dict[str, Any]:
        return structural_query(repo_path, expression, limit=limit)

    def find_dead_code(limit: int = 50) -> dict[str, Any]:
        return dead_code(repo_path, limit=limit)

    def get_routes(limit: int = 100) -> dict[str, Any]:
        return route_summary(repo_path, limit=limit)

    def get_http_confidence(limit: int = 100) -> dict[str, Any]:
        return http_confidence_summary(repo_path, limit=limit)

    def export_graph() -> dict[str, Any]:
        report = export_graph_artifact(repo_path)
        return report.__dict__ | {
            "repo_root": str(report.repo_root),
            "database_path": str(report.database_path),
            "artifact_path": str(report.artifact_path),
        }

    def import_graph(overwrite: bool = False) -> dict[str, Any]:
        report = import_graph_artifact(repo_path, overwrite=overwrite)
        return report.__dict__ | {
            "repo_root": str(report.repo_root),
            "database_path": str(report.database_path),
            "artifact_path": str(report.artifact_path),
        }

    def get_context_pack(
        task: str,
        max_tokens: int = 6000,
        output_format: str = "json",
    ) -> dict[str, Any]:
        pack = context_pack(repo_path, task, max_tokens=max_tokens)
        return {
            "pack": pack,
            "rendered": render_context_pack(pack, output_format=output_format),
            "format": output_format,
        }

    def get_verification_plan(base_ref: str = "HEAD", task: str = "") -> dict[str, Any]:
        return verification_plan(repo_path, base_ref=base_ref, task=task)

    def run_rules(limit: int = 100, severity: str | None = None) -> dict[str, Any]:
        return run_rule_checks(repo_path, limit=limit, severity=severity)

    def get_source_outline(query: str = "", limit: int = 80) -> dict[str, Any]:
        return source_outline(repo_path, query, limit=limit)

    def import_code_index(input_path: str, index_format: str = "auto") -> dict[str, Any]:
        return import_external_index(repo_path, input_path, index_format=index_format)

    return {
        "get_code_context": get_code_context,
        "find_symbol": find_symbol,
        "explain_dependencies": explain_dependencies,
        "token_report": token_report,
        "repository_stats": repository_stats,
        "get_context": get_context,
        "get_history": get_history,
        "get_architecture": get_architecture,
        "get_ownership": get_ownership,
        "get_dependencies": get_dependencies,
        "get_api_flow": get_api_flow,
        "get_decisions": get_decisions,
        "search_memory": search_memory,
        "get_impact": get_impact,
        "get_hotspots": get_hotspots,
        "get_nexus": get_nexus,
        "get_visual_map": get_visual_map,
        "get_index_status": get_index_status,
        "query_code_graph": query_code_graph,
        "find_dead_code": find_dead_code,
        "get_routes": get_routes,
        "get_http_confidence": get_http_confidence,
        "export_graph": export_graph,
        "import_graph": import_graph,
        "get_context_pack": get_context_pack,
        "get_verification_plan": get_verification_plan,
        "run_rules": run_rules,
        "get_source_outline": get_source_outline,
        "import_code_index": import_code_index,
    }


def run_mcp_server(repo_path: str | Path = ".") -> None:
    FastMCP = _load_fastmcp()
    mcp = FastMCP("CodeAtlas")
    handlers = create_tool_handlers(repo_path)

    for name, handler in handlers.items():
        mcp.tool(name=name)(handler)

    mcp.run()


def _load_fastmcp() -> Any:
    try:
        from fastmcp import FastMCP

        return FastMCP
    except Exception:
        pass
    try:
        from mcp.server.fastmcp import FastMCP

        return FastMCP
    except Exception as exc:
        msg = (
            "MCP support requires FastMCP or the MCP Python SDK. "
            "Install with `pip install 'codeatlas[mcp]'`."
        )
        raise RuntimeError(msg) from exc
