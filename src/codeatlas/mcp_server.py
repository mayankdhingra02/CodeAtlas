from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .retrieval import RetrievalEngine


def create_tool_handlers(repo_path: str | Path = ".") -> dict[str, Callable[..., Any]]:
    engine = RetrievalEngine()

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

    return {
        "get_code_context": get_code_context,
        "find_symbol": find_symbol,
        "explain_dependencies": explain_dependencies,
        "token_report": token_report,
        "repository_stats": repository_stats,
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
