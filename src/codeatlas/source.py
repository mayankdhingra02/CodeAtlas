from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import CodeAtlasPaths, resolve_repo_root
from .storage import GraphStore


def source_outline(repo_path: str | Path, query: str = "", *, limit: int = 80) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    store = GraphStore(CodeAtlasPaths(repo_root).database_path)
    try:
        store.initialize()
        search = query.strip()
        params: tuple[Any, ...]
        where = ""
        if search:
            where = """
            WHERE f.path LIKE ?
               OR s.name LIKE ?
               OR s.qualified_name LIKE ?
               OR s.kind LIKE ?
            """
            pattern = f"%{search}%"
            params = (pattern, pattern, pattern, pattern, limit * 8)
        else:
            params = (limit * 8,)
        rows = store.connection.execute(
            f"""
            SELECT
              f.path AS file_path,
              f.language,
              s.name,
              s.qualified_name,
              s.kind,
              s.line_start,
              s.line_end,
              s.signature,
              s.parent_qualified_name
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            {where}
            ORDER BY f.path, s.line_start, s.name
            LIMIT ?
            """,
            params,
        ).fetchall()
        files: dict[str, dict[str, Any]] = {}
        for row in rows:
            file_path = str(row["file_path"])
            entry = files.setdefault(
                file_path,
                {
                    "file_path": file_path,
                    "language": str(row["language"]),
                    "symbols": [],
                },
            )
            entry["symbols"].append(
                {
                    "name": str(row["name"]),
                    "qualified_name": str(row["qualified_name"]),
                    "kind": str(row["kind"]),
                    "line_start": int(row["line_start"]),
                    "line_end": int(row["line_end"]),
                    "signature": row["signature"],
                    "parent": row["parent_qualified_name"],
                }
            )
            if len(files) >= limit and not search:
                break
        result_files = list(files.values())[:limit]
        return {
            "query": search,
            "count": len(result_files),
            "files": result_files,
        }
    finally:
        store.close()
