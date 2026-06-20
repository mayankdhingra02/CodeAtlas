from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .memory import MemoryQueryEngine
from .retrieval import RetrievalEngine
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
