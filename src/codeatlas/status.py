from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import CodeAtlasPaths, resolve_repo_root
from .project_config import load_project_config
from .scanner import iter_source_files
from .storage import GraphStore


def index_status(repo_path: str | Path) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    paths = CodeAtlasPaths(repo_root)
    project_config = load_project_config(repo_root)
    payload: dict[str, Any] = {
        "repo_root": str(repo_root),
        "database_path": str(paths.database_path),
        "artifact_path": str(paths.graph_artifact_path),
        "indexed": paths.database_path.exists(),
        "artifact_exists": paths.graph_artifact_path.exists(),
        "dirty_files": 0,
        "new_files": 0,
        "deleted_files": 0,
        "stale": True,
        "last_indexed_at": None,
        "parser_errors": 0,
        "files_skipped": 0,
        "supported_languages": [],
        "config": project_config.public_payload(),
    }
    if paths.graph_artifact_path.exists():
        payload["artifact_size_bytes"] = paths.graph_artifact_path.stat().st_size
    if not paths.database_path.exists():
        return payload

    store = GraphStore(paths.database_path)
    try:
        store.initialize()
        stats = store.repository_stats()
        report = store.get_metadata("last_index_report", {})
        previous = store.previous_file_hashes()
        current_files = tuple(iter_source_files(repo_root))
        current = {source.relative_path: source.sha256 for source in current_files}
        dirty = sum(1 for path, sha in current.items() if path in previous and previous[path] != sha)
        new = len(set(current) - set(previous))
        deleted = len(set(previous) - set(current))
        last_indexed = stats.get("last_indexed_at")
        payload.update(
            {
                "indexed": True,
                "last_indexed_at": last_indexed,
                "files_indexed": int(stats.get("files_indexed") or 0),
                "symbols": int(stats.get("classes") or 0)
                + int(stats.get("functions") or 0)
                + int(stats.get("methods") or 0),
                "graph_nodes": int(stats.get("graph_nodes") or 0),
                "graph_edges": int(stats.get("graph_edges") or 0),
                "dirty_files": dirty,
                "new_files": new,
                "deleted_files": deleted,
                "stale": bool(dirty or new or deleted),
                "parser_errors": len(report.get("parser_errors") or [])
                if isinstance(report, dict)
                else 0,
                "files_skipped": int(report.get("files_skipped") or 0)
                if isinstance(report, dict)
                else 0,
                "supported_languages": store.get_metadata("supported_languages", []),
                "checked_at": datetime.now(UTC).isoformat(),
            }
        )
    finally:
        store.close()
    return payload
