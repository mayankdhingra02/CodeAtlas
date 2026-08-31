from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import CodeAtlasPaths, resolve_repo_root
from .models import (
    EdgeType,
    IndexedFileResult,
    IndexReport,
    NodeType,
    ParseResult,
    SourceFile,
)
from .parsers import ParserRegistry
from .project_config import load_project_config
from .scanner import iter_source_files
from .storage import (
    SCHEMA_VERSION,
    GraphStore,
    file_node_key,
    module_node_key,
    normalize_import_module,
    symbol_node_key,
    unresolved_symbol_node_key,
    utc_now,
)

SEMANTIC_RESOLUTION_VERSION = 1


class RepositoryIndexer:
    def __init__(self, parser_registry: ParserRegistry | None = None) -> None:
        self.parser_registry = parser_registry or ParserRegistry()

    def index(self, repo_path: str | Path, *, incremental: bool = False) -> IndexReport:
        start = time.perf_counter()
        repo_root = resolve_repo_root(repo_path)
        paths = CodeAtlasPaths(repo_root)
        project_config = load_project_config(repo_root)
        paths.artifact_dir.mkdir(parents=True, exist_ok=True)
        paths.cache_dir.mkdir(parents=True, exist_ok=True)

        store = GraphStore(paths.database_path)
        warnings: list[str] = []
        try:
            store.initialize(validate_schema=False)
            schema_status = store.schema_version_status()
            if not schema_status["ok"]:
                warnings.append(
                    "Index rebuilt because schema changed "
                    f"from {schema_status['actual']} to {SCHEMA_VERSION}."
                )
                incremental = False
                store.recreate()
            previous_hashes = store.previous_file_hashes()
            stored_semantic_resolution_version = store.get_metadata(
                "semantic_resolution_version"
            )
            source_files = tuple(iter_source_files(repo_root))
            source_files_by_path = {source.relative_path: source for source in source_files}
            current_hashes = {source.relative_path: source.sha256 for source in source_files}

            if not incremental:
                store.clear()
                previous_hashes = {}

            to_process = self._files_to_process(source_files, previous_hashes, incremental)
            deleted_paths = sorted(set(previous_hashes) - set(current_hashes))
            parser_errors: list[str] = []
            parsed_results: list[ParseResult] = []
            file_results: list[IndexedFileResult] = []

            for source_file in to_process:
                parser = self.parser_registry.get(source_file.language)
                try:
                    parsed_results.append(parser.parse(repo_root, source_file))
                except Exception as exc:
                    parser_errors.append(f"{source_file.relative_path}: {exc}")
                    file_results.append(
                        IndexedFileResult(
                            relative_path=source_file.relative_path,
                            status="error",
                            symbols=0,
                            imports=0,
                        )
                    )

            parsed_paths = {result.source_file.relative_path for result in parsed_results}
            semantic_mutation_paths = set(deleted_paths) | parsed_paths
            old_symbol_rows = (
                store.semantic_symbols_for_files(semantic_mutation_paths)
                if incremental
                else []
            )
            old_import_rows = (
                store.semantic_imports_for_files(semantic_mutation_paths)
                if incremental
                else []
            )
            definitions_changed = incremental and _definition_universe_changed(
                old_symbol_rows,
                parsed_results,
            )
            imports_changed = incremental and _import_universe_changed(
                old_import_rows,
                parsed_results,
            )

            changed_attempt_paths = {source.relative_path for source in to_process}
            unchanged_current_paths = set(current_hashes) - changed_attempt_paths
            semantic_candidate_paths: set[str] = set()
            conservative_fallback_used = False
            conservative_fallback_reason: str | None = None
            semantic_resolution_upgrade = (
                incremental
                and bool(previous_hashes)
                and stored_semantic_resolution_version != SEMANTIC_RESOLUTION_VERSION
            )
            if semantic_resolution_upgrade:
                semantic_candidate_paths = set(unchanged_current_paths)
                conservative_fallback_used = True
                conservative_fallback_reason = (
                    "Semantic resolution behavior changed; all unchanged source files were "
                    "conservatively re-resolved once to upgrade the existing index."
                )
            elif definitions_changed:
                semantic_candidate_paths = set(unchanged_current_paths)
                conservative_fallback_used = True
                conservative_fallback_reason = (
                    "Definition identities changed; schema v3 does not persist every unresolved "
                    "semantic use, so all unchanged source files were conservatively re-resolved."
                )
            elif imports_changed:
                affected_names, affected_keys, affected_modules = _affected_import_evidence(
                    old_import_rows,
                    parsed_results,
                )
                try:
                    semantic_candidate_paths = store.semantic_dependent_files(
                        symbol_names=affected_names,
                        symbol_keys=affected_keys,
                        module_names=affected_modules,
                    )
                    semantic_candidate_paths &= unchanged_current_paths
                except sqlite3.Error as exc:
                    semantic_candidate_paths = set(unchanged_current_paths)
                    conservative_fallback_used = True
                    conservative_fallback_reason = (
                        "Targeted import-dependent discovery failed; all unchanged source files "
                        f"were conservatively re-resolved ({exc})."
                    )
            if conservative_fallback_reason:
                warnings.append(conservative_fallback_reason)

            prior_relationship_counts = store.resolution_edge_counts_for_files(
                semantic_mutation_paths | semantic_candidate_paths
            )

            files_deleted = 0
            for relative_path in deleted_paths:
                files_deleted += (
                    1 if store.delete_file(relative_path, commit=False) else 0
                )

            for parse_result in parsed_results:
                self._write_parse_result(store, parse_result, commit=False)
                file_results.append(
                    IndexedFileResult(
                        relative_path=parse_result.source_file.relative_path,
                        status="indexed",
                        symbols=len(parse_result.symbols),
                        imports=len(parse_result.imports),
                    )
                )

            for parse_result in parsed_results:
                self._write_resolution_edges(store, parse_result, commit=False)

            semantic_results: list[ParseResult] = []
            for relative_path in sorted(semantic_candidate_paths):
                semantic_source_file = source_files_by_path.get(relative_path)
                if semantic_source_file is None:
                    continue
                parser = self.parser_registry.get(semantic_source_file.language)
                try:
                    semantic_results.append(parser.parse(repo_root, semantic_source_file))
                except Exception as exc:
                    message = (
                        f"{relative_path}: semantic re-resolution failed: {exc}"
                    )
                    # This file was successfully parsed into the prior index.
                    # Publishing the new symbol universe with its old semantic
                    # edges would create a mixed generation, so abort and let
                    # the outer transaction restore the complete prior index.
                    raise RuntimeError(message) from exc

            semantic_result_paths = {
                result.source_file.relative_path for result in semantic_results
            }
            if semantic_result_paths:
                store.delete_resolution_edges_for_files(semantic_result_paths)
                for parse_result in semantic_results:
                    self._write_resolution_edges(store, parse_result, commit=False)
                    file_results.append(
                        IndexedFileResult(
                            relative_path=parse_result.source_file.relative_path,
                            status="semantically-reresolved",
                            symbols=len(parse_result.symbols),
                            imports=len(parse_result.imports),
                        )
                    )
            store.prune_orphan_symbol_references()

            if incremental:
                relationships_removed = sum(
                    prior_relationship_counts.get(path, 0)
                    for path in semantic_mutation_paths | semantic_result_paths
                )
                replacement_counts = store.resolution_edge_counts_for_files(
                    parsed_paths | semantic_result_paths
                )
                relationships_replaced = sum(replacement_counts.values())
            else:
                # A full build creates a baseline; no prior semantic generation
                # exists for the incremental removal/replacement counters.
                relationships_removed = 0
                relationships_replaced = 0

            skipped = 0
            if incremental:
                content_skipped_paths = set(current_hashes) - changed_attempt_paths
                skipped = len(content_skipped_paths)
                status_skipped_paths = content_skipped_paths - semantic_candidate_paths
                for relative_path in sorted(status_skipped_paths):
                    file_results.append(
                        IndexedFileResult(
                            relative_path=relative_path,
                            status="skipped",
                            symbols=0,
                            imports=0,
                        )
                    )

            parse_quality = store.parse_quality_stats()
            store.set_metadata("schema_version", SCHEMA_VERSION)
            store.set_metadata(
                "semantic_resolution_version",
                SEMANTIC_RESOLUTION_VERSION,
            )
            store.set_metadata("repo_root", str(repo_root))
            store.set_metadata("last_indexed_at", utc_now())
            store.set_metadata("supported_languages", list(self.parser_registry.supported_languages))
            store.set_metadata("project_config", project_config.public_payload())
            store.set_metadata("parse_quality", parse_quality)
            store.set_metadata("last_index_report", {
                "files_scanned": len(source_files),
                "files_indexed": len(parsed_results),
                "files_skipped": skipped,
                "files_deleted": files_deleted,
                "files_content_parsed": len(parsed_results),
                "files_semantically_reresolved": len(semantic_results),
                "relationships_removed": relationships_removed,
                "relationships_replaced": relationships_replaced,
                "conservative_fallback_used": conservative_fallback_used,
                "conservative_fallback_reason": conservative_fallback_reason,
                "parser_errors": parser_errors,
                "file_results": [asdict(result) for result in sorted(file_results, key=lambda item: item.relative_path)],
            })
            store.commit()

            stats_payload = store.repository_stats()
            stats_payload["parse_quality"] = parse_quality
            metadata_payload: dict[str, Any] = {
                "repo_root": str(repo_root),
                "database_path": str(paths.database_path),
                "last_indexed_at": stats_payload.get("last_indexed_at"),
                "supported_languages": list(self.parser_registry.supported_languages),
                "incremental": incremental,
            }
            self._write_json(paths.metadata_path, metadata_payload)
            self._write_json(paths.stats_path, stats_payload)

            duration = time.perf_counter() - start
            return IndexReport(
                repo_root=repo_root,
                database_path=paths.database_path,
                full_rebuild=not incremental,
                duration_seconds=duration,
                files_scanned=len(source_files),
                files_indexed=len(parsed_results),
                files_skipped=skipped,
                files_deleted=files_deleted,
                symbols_indexed=sum(len(result.symbols) for result in parsed_results),
                edges_indexed=store.count_edges(),
                files_content_parsed=len(parsed_results),
                files_semantically_reresolved=len(semantic_results),
                relationships_removed=relationships_removed,
                relationships_replaced=relationships_replaced,
                conservative_fallback_used=conservative_fallback_used,
                conservative_fallback_reason=conservative_fallback_reason,
                parser_errors=tuple(parser_errors),
                file_results=tuple(sorted(file_results, key=lambda item: item.relative_path)),
                warnings=tuple(warnings),
            )
        except Exception:
            store.rollback()
            raise
        finally:
            store.close()

    def _files_to_process(
        self,
        source_files: tuple[SourceFile, ...],
        previous_hashes: dict[str, str],
        incremental: bool,
    ) -> tuple[SourceFile, ...]:
        if not incremental:
            return source_files
        return tuple(
            source_file
            for source_file in source_files
            if previous_hashes.get(source_file.relative_path) != source_file.sha256
        )

    def _write_parse_result(
        self,
        store: GraphStore,
        parse_result: ParseResult,
        *,
        commit: bool = True,
    ) -> None:
        relative_path = parse_result.source_file.relative_path
        file_key = file_node_key(relative_path)
        module_key = module_node_key(parse_result.module_name)
        replacement_keys = {
            file_key,
            module_key,
            *(symbol.node_key for symbol in parse_result.symbols),
        }
        store.delete_file(
            relative_path,
            replacement_keys=replacement_keys,
            commit=False,
        )
        file_id = store.upsert_file(parse_result.source_file)
        content = parse_result.source_file.path.read_text(encoding="utf-8", errors="replace")

        store.insert_node(
            file_key,
            NodeType.FILE,
            relative_path,
            file_path=relative_path,
            metadata={"language": parse_result.source_file.language},
        )
        store.upsert_file_search(
            parse_result.source_file,
            content,
        )
        store.upsert_file_snippet(file_id, parse_result.source_file, content)
        store.insert_node(
            module_key,
            NodeType.MODULE,
            parse_result.module_name,
            file_path=relative_path,
            metadata={"path": relative_path},
        )
        store.insert_edge(
            file_key,
            NodeType.FILE,
            module_key,
            NodeType.MODULE,
            EdgeType.CONTAINS,
            metadata={"resolution_tier": "parser", "confidence": 1.0},
        )

        for import_record in parse_result.imports:
            store.insert_import(file_id, import_record)
            import_module_key = module_node_key(import_record.module)
            store.insert_node(
                import_module_key,
                NodeType.MODULE,
                import_record.module,
                metadata={"external": True},
            )
            store.insert_edge(
                file_key,
                NodeType.FILE,
                import_module_key,
                NodeType.MODULE,
                EdgeType.IMPORTS,
                metadata={
                    "name": import_record.name,
                    "alias": import_record.alias,
                    "line": import_record.line_number,
                    "resolution_tier": "parser",
                    "confidence": 0.9,
                },
            )

        for symbol in parse_result.symbols:
            symbol_id = store.insert_symbol(file_id, relative_path, symbol)
            store.upsert_symbol_snippet(file_id, symbol_id, relative_path, symbol, content)
            route = route_info_for_symbol(symbol)
            if route:
                route_key = route_node_key(symbol.qualified_name)
                store.insert_node(
                    route_key,
                    NodeType.ROUTE,
                    route["label"],
                    file_path=relative_path,
                    metadata=route | {"handler": symbol.qualified_name},
                )
                store.insert_edge(
                    route_key,
                    NodeType.ROUTE,
                    symbol.node_key,
                    symbol.node_type,
                    EdgeType.HANDLES,
                    weight=0.95,
                    metadata=route | {"confidence": 0.95, "resolution_tier": "parser"},
                )
            parent_key = (
                symbol_node_key(symbol.parent_qualified_name)
                if symbol.parent_qualified_name
                else module_key
            )
            parent_type = NodeType.SYMBOL if symbol.parent_qualified_name else NodeType.MODULE
            store.insert_edge(
                parent_key,
                parent_type,
                symbol.node_key,
                symbol.node_type,
                EdgeType.CONTAINS,
                metadata={"resolution_tier": "parser", "confidence": 1.0},
            )
            store.insert_edge(
                file_key,
                NodeType.FILE,
                symbol.node_key,
                symbol.node_type,
                EdgeType.DEFINES,
                metadata={"resolution_tier": "parser", "confidence": 1.0},
            )

        if commit:
            store.commit()

    def _write_resolution_edges(
        self,
        store: GraphStore,
        parse_result: ParseResult,
        *,
        commit: bool = True,
    ) -> None:
        for call in parse_result.calls:
            source_key = symbol_node_key(call.source_qualified_name)
            http_call = http_call_info(call)
            if http_call:
                http_target_key = route_external_key(
                    http_call["method"], http_call["target"]
                )
                store.insert_node(
                    http_target_key,
                    NodeType.ROUTE,
                    http_call["label"],
                    metadata=http_call | {"external": True},
                )
                store.insert_edge(
                    source_key,
                    NodeType.SYMBOL,
                    http_target_key,
                    NodeType.ROUTE,
                    EdgeType.HTTP_CALLS,
                    weight=http_call["confidence"],
                    metadata=http_call | {"resolution_tier": "heuristic"},
                )
            resolution = store.resolve_symbol(
                call.target_name,
                parse_result.module_name,
                parse_result.imports,
                source_file_path=parse_result.source_file.relative_path,
            )
            if resolution is None:
                call_target_key = unresolved_symbol_node_key(call.target_name)
                store.insert_node(
                    call_target_key,
                    NodeType.SYMBOL,
                    call.target_name,
                    metadata={"unresolved": True},
                )
            else:
                call_target_key = resolution.node_key
            store.insert_edge(
                source_key,
                NodeType.SYMBOL,
                call_target_key,
                NodeType.SYMBOL,
                EdgeType.CALLS,
                metadata={
                    "display": call.display_name,
                    "line": call.line_number,
                    "arguments": list(call.arguments),
                    "resolution_tier": resolution.tier if resolution else "unresolved",
                    "confidence": resolution_confidence(resolution.tier if resolution else None, 0.68),
                },
            )

        for inheritance in parse_result.inheritance:
            source_key = symbol_node_key(inheritance.source_qualified_name)
            resolution = store.resolve_symbol(
                inheritance.target_name,
                parse_result.module_name,
                parse_result.imports,
                source_file_path=parse_result.source_file.relative_path,
            )
            if resolution is None:
                inheritance_target_key = unresolved_symbol_node_key(
                    inheritance.target_name
                )
                store.insert_node(
                    inheritance_target_key,
                    NodeType.SYMBOL,
                    inheritance.target_name,
                    metadata={"unresolved": True},
                )
            else:
                inheritance_target_key = resolution.node_key
            store.insert_edge(
                source_key,
                NodeType.SYMBOL,
                inheritance_target_key,
                NodeType.SYMBOL,
                EdgeType.INHERITS,
                metadata={
                    "line": inheritance.line_number,
                    "resolution_tier": resolution.tier if resolution else "unresolved",
                    "confidence": resolution_confidence(resolution.tier if resolution else None, 0.74),
                },
            )

        for reference in parse_result.references:
            resolution = store.resolve_symbol(
                reference.target_name,
                parse_result.module_name,
                parse_result.imports,
                source_file_path=parse_result.source_file.relative_path,
            )
            if resolution is None:
                continue
            reference_target_key = resolution.node_key
            store.insert_edge(
                symbol_node_key(reference.source_qualified_name),
                NodeType.SYMBOL,
                reference_target_key,
                NodeType.SYMBOL,
                EdgeType.REFERENCES,
                metadata={
                    "line": reference.line_number,
                    "resolution_tier": resolution.tier,
                    "confidence": resolution_confidence(resolution.tier, 0.66),
                },
            )

        for import_record in parse_result.imports:
            if not import_record.name:
                continue
            resolution = store.resolve_symbol(
                import_record.name,
                parse_result.module_name,
                parse_result.imports,
                source_file_path=parse_result.source_file.relative_path,
            )
            if resolution is None:
                continue
            import_target_key = resolution.node_key
            store.insert_edge(
                file_node_key(parse_result.source_file.relative_path),
                NodeType.FILE,
                import_target_key,
                NodeType.SYMBOL,
                EdgeType.REFERENCES,
                metadata={
                    "import": import_record.display_name,
                    "line": import_record.line_number,
                    "resolution_tier": resolution.tier,
                    "confidence": resolution_confidence(resolution.tier, 0.72),
                },
            )

        if commit:
            store.commit()

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _definition_universe_changed(
    old_rows: list[sqlite3.Row],
    parsed_results: list[ParseResult],
) -> bool:
    previous = {
        (
            str(row["file_path"]),
            str(row["name"]),
            str(row["qualified_name"]),
            str(row["module"]),
            str(row["kind"]),
        )
        for row in old_rows
    }
    current = {
        (
            result.source_file.relative_path,
            symbol.name,
            symbol.qualified_name,
            symbol.module,
            symbol.kind.value,
        )
        for result in parsed_results
        for symbol in result.symbols
    }
    return previous != current


def _import_universe_changed(
    old_rows: list[sqlite3.Row],
    parsed_results: list[ParseResult],
) -> bool:
    previous = {
        (
            str(row["file_path"]),
            str(row["source_module"] or ""),
            str(row["module"]),
            str(row["name"] or ""),
            str(row["alias"] or ""),
            bool(row["is_from"]),
        )
        for row in old_rows
    }
    current = {
        (
            result.source_file.relative_path,
            result.module_name,
            record.module,
            record.name or "",
            record.alias or "",
            record.is_from,
        )
        for result in parsed_results
        for record in result.imports
    }
    return previous != current


def _affected_import_evidence(
    old_rows: list[sqlite3.Row],
    parsed_results: list[ParseResult],
) -> tuple[set[str], set[str], set[str]]:
    names: set[str] = set()
    keys: set[str] = set()
    modules: set[str] = set()
    for row in old_rows:
        source_module = str(row["source_module"] or "")
        imported_module = normalize_import_module(
            str(row["module"] or ""),
            source_module,
            source_file_path=str(row["file_path"] or ""),
        )
        imported_name = str(row["name"] or "")
        alias = str(row["alias"] or "")
        if source_module:
            modules.add(source_module)
        if imported_module:
            modules.add(imported_module)
        local_name = alias or imported_name or imported_module.rsplit(".", 1)[-1]
        if local_name:
            names.add(local_name)
        if imported_module and imported_name:
            keys.add(symbol_node_key(f"{imported_module}.{imported_name}"))
    for result in parsed_results:
        modules.add(result.module_name)
        for record in result.imports:
            imported_module = normalize_import_module(
                record.module,
                result.module_name,
                source_file_path=result.source_file.relative_path,
            )
            if imported_module:
                modules.add(imported_module)
            local_name = (
                record.alias
                or record.name
                or imported_module.rsplit(".", 1)[-1]
            )
            if local_name:
                names.add(local_name)
            if imported_module and record.name:
                keys.add(symbol_node_key(f"{imported_module}.{record.name}"))
    return names, keys, modules


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def route_info_for_symbol(symbol: Any) -> dict[str, Any] | None:
    js_route = route_info_from_js_symbol(symbol.name, symbol.signature or "")
    if js_route:
        return js_route
    for decorator in getattr(symbol, "decorators", ()) or ():
        route = route_info_from_decorator(str(decorator))
        if route:
            return route
    return None


def route_info_from_js_symbol(name: str, signature: str) -> dict[str, Any] | None:
    if not name.startswith("route_"):
        return None
    match = re.match(r"route_(get|post|put|patch|delete|use|head|options)_(.+)", name)
    method = match.group(1).upper() if match else "ROUTE"
    route_match = re.search(r"\.\w+\s*\(\s*['\"]([^'\"]+)['\"]", signature)
    path = route_match.group(1) if route_match else "/" + name.removeprefix("route_").split("_", 1)[-1]
    return {"method": method, "path": path, "label": f"{method} {path}", "source": "js-route"}


def route_info_from_decorator(decorator: str) -> dict[str, Any] | None:
    match = re.search(
        r"(?:route|api_route|get|post|put|patch|delete|head|options)\s*\(\s*['\"]([^'\"]+)['\"]",
        decorator,
        re.IGNORECASE,
    )
    if not match:
        return None
    method_match = re.search(r"\.(get|post|put|patch|delete|head|options|route|api_route)\s*\(", decorator, re.IGNORECASE)
    method = method_match.group(1).upper() if method_match else "ROUTE"
    if method in {"ROUTE", "API_ROUTE"}:
        methods_match = re.search(r"methods\s*=\s*\[([^\]]+)\]", decorator, re.IGNORECASE)
        if methods_match:
            first = re.search(r"['\"]([A-Za-z]+)['\"]", methods_match.group(1))
            method = first.group(1).upper() if first else "ROUTE"
    path = match.group(1)
    return {"method": method, "path": path, "label": f"{method} {path}", "source": "python-decorator"}


def http_call_info(call: Any) -> dict[str, Any] | None:
    display = str(call.display_name or "")
    name = str(call.target_name or "").lower()
    owner_method = display.rsplit(".", 1)[-1].lower()
    if name not in HTTP_METHODS and owner_method not in HTTP_METHODS and name not in {"fetch", "request"}:
        return None
    target = first_literal_argument(tuple(call.arguments or ()))
    if not target:
        return None
    method = owner_method if owner_method in HTTP_METHODS else name
    if method == "fetch":
        method = "get"
    confidence = 0.78 if target.startswith(("http://", "https://", "/")) else 0.52
    return {
        "method": method.upper(),
        "target": target,
        "label": f"{method.upper()} {target}",
        "display": display,
        "line": int(call.line_number or 0),
        "confidence": confidence,
    }


def first_literal_argument(arguments: tuple[str, ...]) -> str | None:
    if not arguments:
        return None
    value = arguments[0].strip().strip("rubfRUBF")
    if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
        return value[1:-1]
    if value.startswith(("http://", "https://", "/")):
        return value
    return None


def resolution_confidence(tier: str | None, default: float) -> float:
    return {
        "exact_qualified": 0.95,
        "import_scoped": 0.86,
        "same_module": 0.8,
        "unique_name": 0.7,
        "name": default,
        "unresolved": 0.2,
    }.get(str(tier or ""), default)


def route_node_key(qualified_name: str) -> str:
    return f"route:{qualified_name}"


def route_external_key(method: str, target: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:/-]+", "_", target.strip())
    return f"route:external:{method.upper()}:{cleaned}"
