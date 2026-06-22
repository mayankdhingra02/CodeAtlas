from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import SUPPORTED_EXTENSIONS, CodeAtlasPaths, resolve_repo_root
from .models import EdgeType, NodeType, SourceFile, SymbolKind, SymbolRecord
from .storage import GraphStore, symbol_node_key


def import_external_index(
    repo_path: str | Path,
    input_path: str | Path,
    *,
    index_format: str = "auto",
) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    source_path = Path(input_path).expanduser().resolve()
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    store = GraphStore(CodeAtlasPaths(repo_root).database_path)
    inserted_files: set[str] = set()
    inserted_symbols: set[str] = set()
    inserted_edges = 0
    try:
        store.initialize()
        for symbol in normalized_symbols(payload):
            relative_path = symbol["file_path"]
            file_id = ensure_file(store, repo_root, relative_path, inserted_files)
            record = SymbolRecord(
                name=symbol["name"],
                qualified_name=symbol["qualified_name"],
                kind=symbol["kind"],
                module=symbol["module"],
                line_start=symbol["line_start"],
                line_end=symbol["line_end"],
                col_start=symbol.get("col_start", 0),
                col_end=symbol.get("col_end", 0),
                docstring=symbol.get("docstring"),
                signature=symbol.get("signature"),
                parent_qualified_name=symbol.get("parent"),
            )
            store.insert_symbol(file_id, relative_path, record)
            inserted_symbols.add(record.qualified_name)
        for edge in normalized_edges(payload):
            source = edge["source"]
            target = edge["target"]
            source_key = symbol_node_key(source)
            target_key = symbol_node_key(target)
            if source not in inserted_symbols:
                store.insert_node(source_key, NodeType.SYMBOL, display_name(source), metadata={"external": True})
            if target not in inserted_symbols:
                store.insert_node(target_key, NodeType.SYMBOL, display_name(target), metadata={"external": True})
            store.insert_edge(
                source_key,
                NodeType.SYMBOL,
                target_key,
                NodeType.SYMBOL,
                edge["type"],
                weight=edge.get("weight", 0.6),
                metadata={"source": "external-index", **edge.get("metadata", {})},
            )
            inserted_edges += 1
        store.set_metadata(
            "external_index",
            {
                "path": str(source_path),
                "format": detected_format(payload, index_format),
                "symbols": len(inserted_symbols),
                "edges": inserted_edges,
            },
        )
        store.commit()
    finally:
        store.close()
    return {
        "source": str(source_path),
        "format": detected_format(payload, index_format),
        "files": len(inserted_files),
        "symbols": len(inserted_symbols),
        "edges": inserted_edges,
    }


def normalized_symbols(payload: dict[str, Any]) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    for item in payload.get("symbols", []) if isinstance(payload.get("symbols"), list) else []:
        normalized = generic_symbol(item)
        if normalized:
            symbols.append(normalized)
    for document in payload.get("documents", []) if isinstance(payload.get("documents"), list) else []:
        file_path = document_path(document)
        if not file_path:
            continue
        for item in document.get("symbols", []) if isinstance(document.get("symbols"), list) else []:
            normalized = generic_symbol(item, default_file=file_path)
            if normalized:
                symbols.append(normalized)
        for occurrence in document.get("occurrences", []) if isinstance(document.get("occurrences"), list) else []:
            if not is_definition_occurrence(occurrence):
                continue
            normalized = scip_occurrence_symbol(occurrence, file_path)
            if normalized:
                symbols.append(normalized)
    unique: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        unique[symbol["qualified_name"]] = symbol
    return list(unique.values())


def normalized_edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for item in payload.get("edges", []) if isinstance(payload.get("edges"), list) else []:
        source = item.get("source") or item.get("source_symbol") or item.get("from")
        target = item.get("target") or item.get("target_symbol") or item.get("to")
        if not source or not target:
            continue
        edges.append(
            {
                "source": str(source),
                "target": str(target),
                "type": edge_type_for(str(item.get("type") or item.get("edge_type") or "REFERENCES")),
                "weight": float(item.get("weight", 0.6)),
                "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            }
        )
    for document in payload.get("documents", []) if isinstance(payload.get("documents"), list) else []:
        for item in document.get("symbols", []) if isinstance(document.get("symbols"), list) else []:
            source = item.get("symbol") or item.get("qualified_name")
            if not source:
                continue
            for relationship in item.get("relationships", []) if isinstance(item.get("relationships"), list) else []:
                target = relationship.get("symbol") or relationship.get("target")
                if target:
                    relationship_type = (
                        relationship.get("type")
                        or relationship.get("relationship")
                        or relationship.get("edge_type")
                        or ("CALLS" if relationship.get("is_call") else "REFERENCES")
                    )
                    edges.append(
                        {
                            "source": str(source),
                            "target": str(target),
                            "type": edge_type_for(str(relationship_type)),
                            "weight": 0.6,
                            "metadata": {"relationship": relationship},
                        }
                    )
    return edges


def generic_symbol(item: dict[str, Any], *, default_file: str = "") -> dict[str, Any] | None:
    raw_name = item.get("qualified_name") or item.get("symbol") or item.get("name")
    file_path = item.get("file_path") or item.get("path") or item.get("relative_path") or default_file
    if not raw_name or not file_path:
        return None
    qualified_name = str(raw_name)
    line_start = int(item.get("line_start") or item.get("line") or 1)
    line_end = int(item.get("line_end") or line_start)
    name = str(item.get("name") or display_name(qualified_name))
    return {
        "name": name,
        "qualified_name": qualified_name,
        "kind": symbol_kind_for(str(item.get("kind") or item.get("type") or "FUNCTION")),
        "module": module_for_path(str(file_path)),
        "file_path": str(file_path),
        "line_start": max(1, line_start),
        "line_end": max(1, line_end),
        "signature": item.get("signature"),
        "docstring": item.get("documentation") if isinstance(item.get("documentation"), str) else None,
    }


def scip_occurrence_symbol(occurrence: dict[str, Any], file_path: str) -> dict[str, Any] | None:
    raw_symbol = occurrence.get("symbol")
    if not raw_symbol:
        return None
    line_start, line_end = occurrence_lines(occurrence)
    qualified_name = str(raw_symbol)
    return {
        "name": display_name(qualified_name),
        "qualified_name": qualified_name,
        "kind": SymbolKind.FUNCTION,
        "module": module_for_path(file_path),
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "signature": qualified_name,
        "docstring": None,
    }


def ensure_file(store: GraphStore, repo_root: Path, relative_path: str, seen: set[str]) -> int:
    path = repo_root / relative_path
    content = path.read_bytes() if path.exists() and path.is_file() else b""
    stat = path.stat() if path.exists() and path.is_file() else None
    source_file = SourceFile(
        path=path,
        relative_path=relative_path,
        language=SUPPORTED_EXTENSIONS.get(path.suffix, "external"),
        size_bytes=stat.st_size if stat else len(content),
        mtime_ns=stat.st_mtime_ns if stat else 0,
        sha256=hashlib.sha256(content).hexdigest(),
        line_count=line_count(content),
    )
    file_id = store.upsert_file(source_file)
    if relative_path not in seen:
        store.upsert_file_search(source_file, content.decode("utf-8", errors="replace"))
        store.insert_node(f"file:{relative_path}", NodeType.FILE, Path(relative_path).name, file_path=relative_path)
        seen.add(relative_path)
    return file_id


def document_path(document: dict[str, Any]) -> str:
    return str(document.get("relative_path") or document.get("relativePath") or document.get("path") or "")


def is_definition_occurrence(occurrence: dict[str, Any]) -> bool:
    role = occurrence.get("symbol_roles", occurrence.get("symbolRoles", occurrence.get("role", 0)))
    if isinstance(role, int):
        return bool(role & 1)
    return "definition" in str(role).lower()


def occurrence_lines(occurrence: dict[str, Any]) -> tuple[int, int]:
    raw_range = occurrence.get("range") or occurrence.get("range_")
    if isinstance(raw_range, list) and len(raw_range) >= 3:
        return int(raw_range[0]) + 1, int(raw_range[2]) + 1
    return 1, 1


def symbol_kind_for(value: str) -> SymbolKind:
    normalized = value.upper()
    if "CLASS" in normalized or "TYPE" in normalized:
        return SymbolKind.CLASS
    if "METHOD" in normalized:
        return SymbolKind.METHOD
    return SymbolKind.FUNCTION


def edge_type_for(value: str) -> EdgeType:
    normalized = value.upper()
    if "CALL" in normalized:
        return EdgeType.CALLS
    if "IMPORT" in normalized:
        return EdgeType.IMPORTS
    return EdgeType.REFERENCES


def module_for_path(file_path: str) -> str:
    return ".".join(Path(file_path).with_suffix("").parts) or Path(file_path).stem


def display_name(qualified_name: str) -> str:
    clean = qualified_name.rstrip(".# /")
    for separator in ("#", "/", ".", " "):
        if separator in clean:
            clean = clean.split(separator)[-1]
    return clean or qualified_name


def detected_format(payload: dict[str, Any], requested: str) -> str:
    if requested != "auto":
        return requested
    if isinstance(payload.get("documents"), list):
        return "scip-json"
    return "generic-json"


def line_count(content: bytes) -> int:
    if not content:
        return 0
    return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)
