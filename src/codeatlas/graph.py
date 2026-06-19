from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import CodeAtlasPaths, resolve_repo_root
from .storage import GraphStore, symbol_node_key


class GraphService:
    """Graph helpers with NetworkX support when the dependency is installed."""

    def neighborhood(
        self,
        repo_path: str | Path,
        symbol_name: str,
        *,
        depth: int = 1,
    ) -> dict[str, Any]:
        repo_root = resolve_repo_root(repo_path)
        store = GraphStore(CodeAtlasPaths(repo_root).database_path)
        try:
            store.initialize()
            matches = store.find_symbols(symbol_name)
            start_keys = [symbol_node_key(str(row["qualified_name"])) for row in matches]
            visited, edges = store.traverse(start_keys, depth)
            return {
                "start": start_keys,
                "nodes": sorted(visited),
                "edges": [dict(edge) for edge in edges],
                "networkx_available": networkx_available(),
            }
        finally:
            store.close()


def networkx_available() -> bool:
    try:
        import networkx  # noqa: F401
    except Exception:
        return False
    return True
