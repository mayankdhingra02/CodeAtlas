from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import CodeAtlasPaths, resolve_repo_root
from .models import EdgeType
from .storage import GraphStore, symbol_node_key


ENTRYPOINT_HINTS = (
    "main",
    "handler",
    "route_",
    "describe_",
    "it_",
    "test_",
    "setup",
    "teardown",
)


def dead_code(repo_path: str | Path, *, limit: int = 50) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    store = GraphStore(CodeAtlasPaths(repo_root).database_path)
    try:
        store.initialize()
        rows = store.connection.execute(
            """
            SELECT s.*, f.path AS file_path,
              (
                SELECT COUNT(*)
                FROM edges e
                WHERE e.target_key = 'symbol:' || s.qualified_name
                  AND e.edge_type IN ('CALLS', 'REFERENCES', 'HANDLES')
              ) AS inbound
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            WHERE s.kind IN ('FUNCTION', 'METHOD')
            ORDER BY inbound ASC, f.path, s.line_start
            """
        ).fetchall()
        candidates = []
        for row in rows:
            name = str(row["name"])
            file_path = str(row["file_path"])
            if int(row["inbound"] or 0) > 0:
                continue
            if _is_entrypoint(name, file_path):
                continue
            candidates.append(
                {
                    "qualified_name": str(row["qualified_name"]),
                    "name": name,
                    "kind": str(row["kind"]),
                    "file_path": file_path,
                    "line_start": int(row["line_start"]),
                    "line_end": int(row["line_end"]),
                    "confidence": 0.62,
                    "reason": "No inbound CALLS/REFERENCES/HANDLES edges in the current index.",
                }
            )
            if len(candidates) >= limit:
                break
        return {"count": len(candidates), "items": candidates}
    finally:
        store.close()


def structural_query(repo_path: str | Path, expression: str, *, limit: int = 25) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    command, _, raw_value = expression.partition(":")
    command = command.strip().lower()
    value = raw_value.strip()
    store = GraphStore(CodeAtlasPaths(repo_root).database_path)
    try:
        store.initialize()
        if command in {"callers", "called-by"}:
            return _symbol_edges(store, value, direction="incoming", edge_types=("CALLS",), limit=limit)
        if command in {"calls", "callees"}:
            return _symbol_edges(store, value, direction="outgoing", edge_types=("CALLS",), limit=limit)
        if command == "imports":
            return _imports(store, value, limit=limit)
        if command == "route":
            return _routes(store, value, limit=limit)
        if command == "dead":
            return {"query": expression, "type": "dead", **dead_code(repo_root, limit=limit)}
        if command in {"http", "api"}:
            return _http_edges(store, value, limit=limit)
        return _search(store, expression, limit=limit)
    finally:
        store.close()


def route_summary(repo_path: str | Path, *, limit: int = 100) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    store = GraphStore(CodeAtlasPaths(repo_root).database_path)
    try:
        store.initialize()
        rows = store.connection.execute(
            """
            SELECT n.key, n.label, n.file_path, n.metadata_json
            FROM nodes n
            WHERE n.type = 'ROUTE'
            ORDER BY n.file_path, n.label
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"count": len(rows), "routes": [_node_payload(row) for row in rows]}
    finally:
        store.close()


def http_confidence_summary(repo_path: str | Path, *, limit: int = 100) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    store = GraphStore(CodeAtlasPaths(repo_root).database_path)
    try:
        store.initialize()
        rows = store.connection.execute(
            """
            SELECT e.*, s.label AS source_label, t.label AS target_label, s.file_path AS source_path, t.file_path AS target_path
            FROM edges e
            LEFT JOIN nodes s ON s.key = e.source_key
            LEFT JOIN nodes t ON t.key = e.target_key
            WHERE e.edge_type IN ('HTTP_CALLS', 'HANDLES')
            ORDER BY e.weight DESC, s.label, t.label
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"count": len(rows), "edges": [_edge_payload(row) for row in rows]}
    finally:
        store.close()


def _symbol_edges(
    store: GraphStore,
    value: str,
    *,
    direction: str,
    edge_types: tuple[str, ...],
    limit: int,
) -> dict[str, Any]:
    matches = store.find_symbols(value)
    keys = [symbol_node_key(str(row["qualified_name"])) for row in matches]
    if not keys:
        return {"query": value, "type": direction, "matches": [], "edges": []}
    placeholders = ",".join("?" for _ in keys)
    edge_placeholders = ",".join("?" for _ in edge_types)
    column = "target_key" if direction == "incoming" else "source_key"
    rows = store.connection.execute(
        f"""
        SELECT e.*, s.label AS source_label, t.label AS target_label, s.file_path AS source_path, t.file_path AS target_path
        FROM edges e
        LEFT JOIN nodes s ON s.key = e.source_key
        LEFT JOIN nodes t ON t.key = e.target_key
        WHERE e.{column} IN ({placeholders}) AND e.edge_type IN ({edge_placeholders})
        ORDER BY e.weight DESC
        LIMIT ?
        """,
        (*keys, *edge_types, limit),
    ).fetchall()
    return {
        "query": value,
        "type": direction,
        "matches": [str(row["qualified_name"]) for row in matches],
        "edges": [_edge_payload(row) for row in rows],
    }


def _imports(store: GraphStore, value: str, *, limit: int) -> dict[str, Any]:
    rows = store.connection.execute(
        """
        SELECT i.*, f.path AS file_path
        FROM imports i
        JOIN files f ON f.id = i.file_id
        WHERE i.module LIKE ? OR COALESCE(i.name, '') LIKE ? OR COALESCE(i.alias, '') LIKE ?
        ORDER BY f.path, i.line_number
        LIMIT ?
        """,
        (f"%{value}%", f"%{value}%", f"%{value}%", limit),
    ).fetchall()
    return {"query": value, "type": "imports", "items": [dict(row) for row in rows]}


def _routes(store: GraphStore, value: str, *, limit: int) -> dict[str, Any]:
    rows = store.connection.execute(
        """
        SELECT n.key, n.label, n.file_path, n.metadata_json
        FROM nodes n
        WHERE n.type = 'ROUTE' AND (n.label LIKE ? OR n.file_path LIKE ?)
        ORDER BY n.file_path, n.label
        LIMIT ?
        """,
        (f"%{value}%", f"%{value}%", limit),
    ).fetchall()
    return {"query": value, "type": "route", "items": [_node_payload(row) for row in rows]}


def _http_edges(store: GraphStore, value: str, *, limit: int) -> dict[str, Any]:
    rows = store.connection.execute(
        """
        SELECT e.*, s.label AS source_label, t.label AS target_label, s.file_path AS source_path, t.file_path AS target_path
        FROM edges e
        LEFT JOIN nodes s ON s.key = e.source_key
        LEFT JOIN nodes t ON t.key = e.target_key
        WHERE e.edge_type IN ('HTTP_CALLS', 'HANDLES')
          AND (s.label LIKE ? OR t.label LIKE ? OR e.metadata_json LIKE ?)
        ORDER BY e.weight DESC
        LIMIT ?
        """,
        (f"%{value}%", f"%{value}%", f"%{value}%", limit),
    ).fetchall()
    return {"query": value, "type": "http", "edges": [_edge_payload(row) for row in rows]}


def _search(store: GraphStore, value: str, *, limit: int) -> dict[str, Any]:
    rows = store.find_symbols(value, limit=limit)
    return {"query": value, "type": "search", "items": [dict(row) for row in rows]}


def _node_payload(row: Any) -> dict[str, Any]:
    metadata = _json(row["metadata_json"])
    if "path" not in metadata and "target" in metadata:
        metadata["path"] = metadata["target"]
    return {
        "key": str(row["key"]),
        "label": str(row["label"]),
        "file_path": str(row["file_path"] or ""),
        "metadata": metadata,
    }


def _edge_payload(row: Any) -> dict[str, Any]:
    return {
        "source": str(row["source_key"]),
        "target": str(row["target_key"]),
        "source_label": str(row["source_label"] or row["source_key"]),
        "target_label": str(row["target_label"] or row["target_key"]),
        "source_path": str(row["source_path"] or ""),
        "target_path": str(row["target_path"] or ""),
        "type": str(row["edge_type"]),
        "weight": float(row["weight"] or 1),
        "metadata": _json(row["metadata_json"]),
    }


def _json(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_entrypoint(name: str, file_path: str) -> bool:
    lower_name = name.lower()
    lower_path = file_path.lower()
    if lower_name.startswith("_"):
        return True
    if any(hint in lower_name for hint in ENTRYPOINT_HINTS):
        return True
    return bool(re.search(r"(^|/)(tests?|spec|migrations?)/", lower_path))
