from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import SUPPORTED_EXTENSIONS, CodeAtlasPaths, resolve_repo_root
from .models import EdgeType, NodeType, SourceFile, SymbolKind, SymbolRecord
from .storage import SCHEMA_VERSION, GraphStore, symbol_node_key

SCIP_PROTOBUF_FORMATS = {"scip", "scip-protobuf", "protobuf"}
SCIP_DEFINITION_ROLE = 0x1
SCIP_IMPORT_ROLE = 0x2
SCIP_IDENTIFIER_FUNCTION = 15
SCIP_IDENTIFIER_MACRO = 17
SCIP_KIND_MAP = {
    7: "CLASS",
    9: "METHOD",
    17: "FUNCTION",
    26: "METHOD",
    28: "CLASS",
    49: "CLASS",
    54: "CLASS",
    80: "METHOD",
}


def import_external_index(
    repo_path: str | Path,
    input_path: str | Path,
    *,
    index_format: str = "auto",
) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    source_path = Path(input_path).expanduser().resolve()
    payload = load_external_index_payload(source_path, index_format)
    external_format = detected_format(payload, index_format)
    store = GraphStore(CodeAtlasPaths(repo_root).database_path)
    inserted_files: set[str] = set()
    inserted_symbols: set[str] = set()
    inserted_edges = 0
    try:
        store.initialize()
        for symbol in normalized_symbols(payload):
            relative_path = symbol["file_path"]
            file_id, content = ensure_file(store, repo_root, relative_path, inserted_files)
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
            symbol_id = store.insert_symbol(file_id, relative_path, record)
            if content:
                store.upsert_symbol_snippet(file_id, symbol_id, relative_path, record, content)
            inserted_symbols.add(record.qualified_name)
        for edge in normalized_edges(payload, payload_format=external_format):
            source = edge["source"]
            target = edge["target"]
            source_key = symbol_node_key(source)
            target_key = symbol_node_key(target)
            if source not in inserted_symbols:
                store.insert_node(
                    source_key,
                    NodeType.SYMBOL,
                    display_name(source),
                    metadata={"external": True},
                )
            if target not in inserted_symbols:
                store.insert_node(
                    target_key,
                    NodeType.SYMBOL,
                    display_name(target),
                    metadata={"external": True},
                )
            metadata = edge_metadata(edge, external_format)
            store.insert_edge(
                source_key,
                NodeType.SYMBOL,
                target_key,
                NodeType.SYMBOL,
                edge["type"],
                weight=float(edge.get("weight", metadata["confidence"])),
                metadata=metadata,
            )
            inserted_edges += 1
        store.set_metadata(
            "schema_version",
            SCHEMA_VERSION,
        )
        store.set_metadata(
            "external_index",
            {
                "path": str(source_path),
                "format": external_format,
                "resolution_tier": resolution_tier_for_format(external_format),
                "symbols": len(inserted_symbols),
                "edges": inserted_edges,
            },
        )
        store.commit()
    finally:
        store.close()
    return {
        "source": str(source_path),
        "format": external_format,
        "resolution_tier": resolution_tier_for_format(external_format),
        "files": len(inserted_files),
        "symbols": len(inserted_symbols),
        "edges": inserted_edges,
    }


def load_external_index_payload(source_path: Path, index_format: str) -> dict[str, Any]:
    raw = source_path.read_bytes()
    requested = index_format.lower()
    if requested in SCIP_PROTOBUF_FORMATS:
        return scip_protobuf_payload(raw)
    if requested in {"json", "generic", "generic-json", "scip-json"}:
        return json.loads(raw.decode("utf-8"))
    if source_path.suffix == ".scip":
        return scip_protobuf_payload(raw)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return scip_protobuf_payload(raw)


def normalized_symbols(payload: dict[str, Any]) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    symbol_metadata: dict[str, dict[str, Any]] = {}
    for item in payload.get("symbols", []) if isinstance(payload.get("symbols"), list) else []:
        normalized = generic_symbol(item)
        if normalized:
            symbols.append(normalized)
            symbol_metadata[normalized["qualified_name"]] = normalized
    documents = payload.get("documents", []) if isinstance(payload.get("documents"), list) else []
    for document in documents:
        file_path = document_path(document)
        if not file_path:
            continue
        document_symbols = (
            document.get("symbols", []) if isinstance(document.get("symbols"), list) else []
        )
        for item in document_symbols:
            normalized = generic_symbol(item, default_file=file_path)
            if normalized:
                symbols.append(normalized)
                symbol_metadata[normalized["qualified_name"]] = normalized
        document_occurrences = (
            document.get("occurrences", []) if isinstance(document.get("occurrences"), list) else []
        )
        for occurrence in document_occurrences:
            if not is_definition_occurrence(occurrence):
                continue
            normalized = scip_occurrence_symbol(occurrence, file_path)
            if normalized:
                metadata = symbol_metadata.get(normalized["qualified_name"])
                if metadata:
                    normalized = {
                        **metadata,
                        "line_start": normalized["line_start"],
                        "line_end": normalized["line_end"],
                        "col_start": normalized.get("col_start", metadata.get("col_start", 0)),
                        "col_end": normalized.get("col_end", metadata.get("col_end", 0)),
                    }
                symbols.append(normalized)
    unique: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        unique[symbol["qualified_name"]] = symbol
    return list(unique.values())


def normalized_edges(
    payload: dict[str, Any],
    *,
    payload_format: str = "generic",
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    tier = resolution_tier_for_format(payload_format)
    confidence = confidence_for_resolution_tier(tier)
    for item in payload.get("edges", []) if isinstance(payload.get("edges"), list) else []:
        source = item.get("source") or item.get("source_symbol") or item.get("from")
        target = item.get("target") or item.get("target_symbol") or item.get("to")
        if not source or not target:
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        edges.append(
            {
                "source": str(source),
                "target": str(target),
                "type": edge_type_for(
                    str(item.get("type") or item.get("edge_type") or "REFERENCES")
                ),
                "weight": float(item.get("weight", confidence)),
                "metadata": {
                    "resolution_tier": tier,
                    "confidence": float(item.get("confidence", confidence)),
                    **metadata,
                },
            }
        )
    documents = payload.get("documents", []) if isinstance(payload.get("documents"), list) else []
    for document in documents:
        definitions = definition_occurrences(document)
        document_symbols = (
            document.get("symbols", []) if isinstance(document.get("symbols"), list) else []
        )
        for item in document_symbols:
            source = item.get("symbol") or item.get("qualified_name")
            if not source:
                continue
            relationships = (
                item.get("relationships", [])
                if isinstance(item.get("relationships"), list)
                else []
            )
            for relationship in relationships:
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
                            "weight": confidence,
                            "metadata": {
                                "relationship": relationship,
                                "resolution_tier": tier,
                                "confidence": confidence,
                            },
                        }
                    )
        document_occurrences = (
            document.get("occurrences", []) if isinstance(document.get("occurrences"), list) else []
        )
        for occurrence in document_occurrences:
            if occurrence_is_definition_or_import(occurrence) or not occurrence.get("symbol"):
                continue
            source = enclosing_definition_symbol(occurrence, definitions)
            target = str(occurrence["symbol"])
            if not source or source == target:
                continue
            edge_type = EdgeType.CALLS if occurrence_is_call(occurrence) else EdgeType.REFERENCES
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "type": edge_type,
                    "weight": confidence,
                    "metadata": {
                        "line": occurrence_lines(occurrence)[0],
                        "resolution_tier": tier,
                        "confidence": confidence,
                        "source": "scip-occurrence",
                    },
                }
            )
    return edges


def generic_symbol(item: dict[str, Any], *, default_file: str = "") -> dict[str, Any] | None:
    raw_name = item.get("qualified_name") or item.get("symbol") or item.get("name")
    file_path = (
        item.get("file_path")
        or item.get("path")
        or item.get("relative_path")
        or default_file
    )
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
        "docstring": docstring_for_symbol(item),
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


def ensure_file(
    store: GraphStore,
    repo_root: Path,
    relative_path: str,
    seen: set[str],
) -> tuple[int, str]:
    path = repo_root / relative_path
    content = path.read_bytes() if path.exists() and path.is_file() else b""
    text = content.decode("utf-8", errors="replace")
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
        store.upsert_file_search(source_file, text)
        store.upsert_file_snippet(file_id, source_file, text)
        store.insert_node(
            f"file:{relative_path}",
            NodeType.FILE,
            Path(relative_path).name,
            file_path=relative_path,
        )
        seen.add(relative_path)
    return file_id, text


def document_path(document: dict[str, Any]) -> str:
    return str(
        document.get("relative_path")
        or document.get("relativePath")
        or document.get("path")
        or ""
    )


def is_definition_occurrence(occurrence: dict[str, Any]) -> bool:
    role = occurrence.get("symbol_roles", occurrence.get("symbolRoles", occurrence.get("role", 0)))
    if isinstance(role, int):
        return bool(role & 1)
    return "definition" in str(role).lower()


def occurrence_lines(occurrence: dict[str, Any]) -> tuple[int, int]:
    raw_range = occurrence.get("range") or occurrence.get("range_")
    if isinstance(raw_range, list) and len(raw_range) >= 4:
        return int(raw_range[0]) + 1, int(raw_range[2]) + 1
    if isinstance(raw_range, list) and len(raw_range) == 3:
        return int(raw_range[0]) + 1, int(raw_range[0]) + 1
    return 1, 1


def occurrence_range(occurrence: dict[str, Any]) -> tuple[int, int, int, int] | None:
    raw_range = occurrence.get("range") or occurrence.get("range_")
    if not isinstance(raw_range, list):
        return None
    if len(raw_range) == 3:
        line = int(raw_range[0])
        return line, int(raw_range[1]), line, int(raw_range[2])
    if len(raw_range) >= 4:
        return int(raw_range[0]), int(raw_range[1]), int(raw_range[2]), int(raw_range[3])
    return None


def range_contains(
    outer: tuple[int, int, int, int],
    inner: tuple[int, int, int, int],
) -> bool:
    return (outer[0], outer[1]) <= (inner[0], inner[1]) and (outer[2], outer[3]) >= (
        inner[2],
        inner[3],
    )


def range_size(found_range: tuple[int, int, int, int] | None) -> tuple[int, int]:
    if found_range is None:
        return 1_000_000, 1_000_000
    return found_range[2] - found_range[0], found_range[3] - found_range[1]


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
    if clean.endswith("()"):
        clean = clean[:-2]
    return clean or qualified_name


def detected_format(payload: dict[str, Any], requested: str) -> str:
    if payload.get("_format"):
        return str(payload["_format"])
    if requested != "auto":
        return requested
    if isinstance(payload.get("documents"), list):
        return "scip-json"
    return "generic-json"


def line_count(content: bytes) -> int:
    if not content:
        return 0
    return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)


def edge_metadata(edge: dict[str, Any], payload_format: str) -> dict[str, Any]:
    tier = resolution_tier_for_format(payload_format)
    confidence = confidence_for_resolution_tier(tier)
    metadata = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
    return {
        "source": payload_format,
        "resolution_tier": tier,
        "confidence": float(metadata.get("confidence", edge.get("confidence", confidence))),
        **metadata,
    }


def resolution_tier_for_format(payload_format: str) -> str:
    normalized = payload_format.lower()
    if normalized.startswith("scip"):
        return "scip"
    if normalized in {"generic-json", "json", "generic"}:
        return "external"
    return "external"


def confidence_for_resolution_tier(tier: str) -> float:
    if tier == "scip":
        return 0.98
    if tier == "external":
        return 0.9
    return 0.6


def docstring_for_symbol(item: dict[str, Any]) -> str | None:
    if isinstance(item.get("docstring"), str):
        return str(item["docstring"])
    if isinstance(item.get("documentation"), str):
        return str(item["documentation"])
    if isinstance(item.get("documentation"), list):
        return "\n\n".join(str(part) for part in item["documentation"])
    return None


def definition_occurrences(document: dict[str, Any]) -> list[dict[str, Any]]:
    occurrences = (
        document.get("occurrences") if isinstance(document.get("occurrences"), list) else []
    )
    definitions = [
        occurrence
        for occurrence in occurrences
        if is_definition_occurrence(occurrence) and occurrence_range(occurrence) is not None
    ]
    return sorted(definitions, key=lambda occurrence: range_size(occurrence_range(occurrence)))


def occurrence_is_definition_or_import(occurrence: dict[str, Any]) -> bool:
    role = occurrence_role(occurrence)
    return bool(role & (SCIP_DEFINITION_ROLE | SCIP_IMPORT_ROLE))


def occurrence_is_call(occurrence: dict[str, Any]) -> bool:
    syntax_kind = occurrence.get("syntax_kind", occurrence.get("syntaxKind", 0))
    try:
        value = int(syntax_kind)
    except (TypeError, ValueError):
        value = 0
    return value in {SCIP_IDENTIFIER_FUNCTION, SCIP_IDENTIFIER_MACRO}


def enclosing_definition_symbol(
    occurrence: dict[str, Any],
    definitions: list[dict[str, Any]],
) -> str | None:
    found_range = occurrence_range(occurrence)
    if found_range is None:
        return None
    candidates: list[dict[str, Any]] = []
    for definition in definitions:
        symbol = definition.get("symbol")
        if not symbol:
            continue
        definition_range = occurrence_range(definition)
        if definition_range is not None and range_contains(definition_range, found_range):
            candidates.append(definition)
    if not candidates:
        occurrence_start, _ = occurrence_lines(occurrence)
        previous = [
            definition
            for definition in definitions
            if definition.get("symbol") and occurrence_lines(definition)[0] <= occurrence_start
        ]
        if not previous:
            return None
        previous.sort(key=lambda item: occurrence_lines(item)[0], reverse=True)
        return str(previous[0]["symbol"])
    candidates.sort(key=lambda item: range_size(occurrence_range(item)))
    return str(candidates[0]["symbol"])


def occurrence_role(occurrence: dict[str, Any]) -> int:
    raw = occurrence.get("symbol_roles", occurrence.get("symbolRoles", occurrence.get("role", 0)))
    try:
        return int(raw)
    except (TypeError, ValueError):
        text = str(raw).lower()
    role = 0
    if "definition" in text:
        role |= SCIP_DEFINITION_ROLE
    if "import" in text:
        role |= SCIP_IMPORT_ROLE
    return role


def scip_protobuf_payload(raw: bytes) -> dict[str, Any]:
    index = decode_protobuf_message(raw)
    documents = [
        scip_document_payload(value)
        for value in index.get(2, [])
        if isinstance(value, bytes)
    ]
    external_symbols = [
        scip_symbol_information(value)
        for value in index.get(3, [])
        if isinstance(value, bytes)
    ]
    return {
        "_format": "scip-protobuf",
        "documents": [document for document in documents if document.get("relative_path")],
        "external_symbols": external_symbols,
    }


def scip_document_payload(raw: bytes) -> dict[str, Any]:
    message = decode_protobuf_message(raw)
    relative_path = first_string(message, 1)
    occurrences = [
        scip_occurrence(value)
        for value in message.get(2, [])
        if isinstance(value, bytes)
    ]
    symbols = [
        scip_symbol_information(value)
        for value in message.get(3, [])
        if isinstance(value, bytes)
    ]
    language = first_string(message, 4)
    return {
        "relative_path": relative_path,
        "language": language,
        "occurrences": occurrences,
        "symbols": symbols,
    }


def scip_occurrence(raw: bytes) -> dict[str, Any]:
    message = decode_protobuf_message(raw)
    ranges: list[int] = []
    for value in message.get(1, []):
        if isinstance(value, bytes):
            ranges.extend(decode_packed_varints(value))
        else:
            ranges.append(int(value))
    if not ranges:
        single_line = first_bytes(message, 8)
        multi_line = first_bytes(message, 9)
        if single_line:
            ranges = scip_single_line_range(single_line)
        elif multi_line:
            ranges = scip_multi_line_range(multi_line)
    occurrence: dict[str, Any] = {
        "range": ranges,
        "symbol": first_string(message, 2),
        "symbol_roles": first_int(message, 3),
        "syntax_kind": first_int(message, 5),
    }
    enclosing_single = first_bytes(message, 10)
    enclosing_multi = first_bytes(message, 11)
    if enclosing_single:
        occurrence["enclosing_range"] = scip_single_line_range(enclosing_single)
    elif enclosing_multi:
        occurrence["enclosing_range"] = scip_multi_line_range(enclosing_multi)
    override_documentation = [decode_utf8(value) for value in message.get(4, [])]
    if override_documentation:
        occurrence["override_documentation"] = override_documentation
    return occurrence


def scip_single_line_range(raw: bytes) -> list[int]:
    message = decode_protobuf_message(raw)
    return [first_int(message, 1), first_int(message, 2), first_int(message, 3)]


def scip_multi_line_range(raw: bytes) -> list[int]:
    message = decode_protobuf_message(raw)
    return [
        first_int(message, 1),
        first_int(message, 2),
        first_int(message, 3),
        first_int(message, 4),
    ]


def scip_symbol_information(raw: bytes) -> dict[str, Any]:
    message = decode_protobuf_message(raw)
    symbol = first_string(message, 1)
    documentation = [decode_utf8(value) for value in message.get(3, [])]
    relationships = [
        scip_relationship(value)
        for value in message.get(4, [])
        if isinstance(value, bytes)
    ]
    kind_value = first_int(message, 5)
    payload: dict[str, Any] = {
        "symbol": symbol,
        "qualified_name": symbol,
        "name": first_string(message, 6) or display_name(symbol),
        "kind": SCIP_KIND_MAP.get(kind_value, "FUNCTION"),
        "documentation": "\n".join(part for part in documentation if part),
        "relationships": relationships,
    }
    signature = first_string(message, 7)
    enclosing_symbol = first_string(message, 8)
    if signature:
        payload["signature"] = signature
    if enclosing_symbol:
        payload["parent"] = enclosing_symbol
    return payload


def scip_relationship(raw: bytes) -> dict[str, Any]:
    message = decode_protobuf_message(raw)
    relationship = {
        "symbol": first_string(message, 1),
        "is_reference": bool(first_int(message, 2)),
        "is_implementation": bool(first_int(message, 3)),
        "is_type_definition": bool(first_int(message, 4)),
        "is_definition": bool(first_int(message, 5)),
    }
    if relationship["is_reference"]:
        relationship["type"] = "REFERENCES"
    elif relationship["is_implementation"]:
        relationship["type"] = "REFERENCES"
    elif relationship["is_type_definition"]:
        relationship["type"] = "REFERENCES"
    elif relationship["is_definition"]:
        relationship["type"] = "REFERENCES"
    return relationship


def decode_protobuf_message(raw: bytes) -> dict[int, list[int | bytes]]:
    fields: dict[int, list[int | bytes]] = {}
    index = 0
    while index < len(raw):
        tag, index = read_varint(raw, index)
        field_number = tag >> 3
        wire_type = tag & 0x7
        if field_number == 0:
            break
        if wire_type == 0:
            value, index = read_varint(raw, index)
        elif wire_type == 1:
            value = raw[index : index + 8]
            index += 8
        elif wire_type == 2:
            length, index = read_varint(raw, index)
            value = raw[index : index + length]
            index += length
        elif wire_type == 5:
            value = raw[index : index + 4]
            index += 4
        else:
            msg = f"Unsupported SCIP protobuf wire type {wire_type}."
            raise ValueError(msg)
        fields.setdefault(field_number, []).append(value)
    return fields


def decode_packed_varints(raw: bytes) -> list[int]:
    values: list[int] = []
    index = 0
    while index < len(raw):
        value, index = read_varint(raw, index)
        values.append(value)
    return values


def read_varint(raw: bytes, index: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while index < len(raw):
        byte = raw[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, index
        shift += 7
    raise ValueError("Truncated SCIP protobuf varint.")


def first_string(message: dict[int, list[int | bytes]], field_number: int) -> str:
    values = message.get(field_number) or []
    if not values:
        return ""
    return decode_utf8(values[0])


def first_int(message: dict[int, list[int | bytes]], field_number: int) -> int:
    values = message.get(field_number) or []
    if not values:
        return 0
    value = values[0]
    if isinstance(value, bytes):
        packed = decode_packed_varints(value)
        return packed[0] if packed else 0
    return int(value)


def first_bytes(message: dict[int, list[int | bytes]], field_number: int) -> bytes:
    values = message.get(field_number) or []
    if not values:
        return b""
    value = values[0]
    return value if isinstance(value, bytes) else b""


def decode_utf8(value: int | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
