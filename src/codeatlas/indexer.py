from __future__ import annotations

import json
import time
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
from .scanner import iter_source_files
from .storage import (
    GraphStore,
    file_node_key,
    module_node_key,
    symbol_node_key,
    unresolved_symbol_node_key,
    utc_now,
)


class RepositoryIndexer:
    def __init__(self, parser_registry: ParserRegistry | None = None) -> None:
        self.parser_registry = parser_registry or ParserRegistry()

    def index(self, repo_path: str | Path, *, incremental: bool = False) -> IndexReport:
        start = time.perf_counter()
        repo_root = resolve_repo_root(repo_path)
        paths = CodeAtlasPaths(repo_root)
        paths.artifact_dir.mkdir(parents=True, exist_ok=True)
        paths.cache_dir.mkdir(parents=True, exist_ok=True)

        store = GraphStore(paths.database_path)
        try:
            store.initialize()
            previous_hashes = store.previous_file_hashes()
            source_files = tuple(iter_source_files(repo_root))
            current_hashes = {source.relative_path: source.sha256 for source in source_files}

            if not incremental:
                store.clear()
                previous_hashes = {}

            deleted_paths = sorted(set(previous_hashes) - set(current_hashes))
            files_deleted = 0
            for relative_path in deleted_paths:
                files_deleted += 1 if store.delete_file(relative_path) else 0

            to_process = self._files_to_process(source_files, previous_hashes, incremental)
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

            for parse_result in parsed_results:
                self._write_parse_result(store, parse_result)
                file_results.append(
                    IndexedFileResult(
                        relative_path=parse_result.source_file.relative_path,
                        status="indexed",
                        symbols=len(parse_result.symbols),
                        imports=len(parse_result.imports),
                    )
                )

            for parse_result in parsed_results:
                self._write_resolution_edges(store, parse_result)

            skipped = 0
            if incremental:
                processed_paths = {result.source_file.relative_path for result in parsed_results}
                errored_paths = {
                    result.relative_path for result in file_results if result.status == "error"
                }
                skipped_paths = set(current_hashes) - processed_paths - errored_paths
                skipped = len(skipped_paths)
                for relative_path in sorted(skipped_paths):
                    file_results.append(
                        IndexedFileResult(
                            relative_path=relative_path,
                            status="skipped",
                            symbols=0,
                            imports=0,
                        )
                    )

            store.set_metadata("schema_version", 1)
            store.set_metadata("repo_root", str(repo_root))
            store.set_metadata("last_indexed_at", utc_now())
            store.set_metadata("supported_languages", list(self.parser_registry.supported_languages))
            store.commit()

            stats_payload = store.repository_stats()
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
                parser_errors=tuple(parser_errors),
                file_results=tuple(sorted(file_results, key=lambda item: item.relative_path)),
            )
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

    def _write_parse_result(self, store: GraphStore, parse_result: ParseResult) -> None:
        relative_path = parse_result.source_file.relative_path
        file_key = file_node_key(relative_path)
        module_key = module_node_key(parse_result.module_name)
        replacement_keys = {
            file_key,
            module_key,
            *(symbol.node_key for symbol in parse_result.symbols),
        }
        store.delete_file(relative_path, replacement_keys=replacement_keys)
        file_id = store.upsert_file(parse_result.source_file)

        store.insert_node(
            file_key,
            NodeType.FILE,
            relative_path,
            file_path=relative_path,
            metadata={"language": parse_result.source_file.language},
        )
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
                },
            )

        for symbol in parse_result.symbols:
            store.insert_symbol(file_id, relative_path, symbol)
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
            )
            store.insert_edge(
                file_key,
                NodeType.FILE,
                symbol.node_key,
                symbol.node_type,
                EdgeType.DEFINES,
            )

        store.commit()

    def _write_resolution_edges(self, store: GraphStore, parse_result: ParseResult) -> None:
        for call in parse_result.calls:
            source_key = symbol_node_key(call.source_qualified_name)
            target_key = store.resolve_symbol_node_key(call.target_name, parse_result.module_name)
            target_type = NodeType.SYMBOL
            if target_key is None:
                target_key = unresolved_symbol_node_key(call.target_name)
                store.insert_node(
                    target_key,
                    NodeType.SYMBOL,
                    call.target_name,
                    metadata={"unresolved": True},
                )
            store.insert_edge(
                source_key,
                NodeType.SYMBOL,
                target_key,
                target_type,
                EdgeType.CALLS,
                metadata={
                    "display": call.display_name,
                    "line": call.line_number,
                    "arguments": list(call.arguments),
                },
            )

        for inheritance in parse_result.inheritance:
            source_key = symbol_node_key(inheritance.source_qualified_name)
            target_key = store.resolve_symbol_node_key(
                inheritance.target_name, parse_result.module_name
            )
            if target_key is None:
                target_key = unresolved_symbol_node_key(inheritance.target_name)
                store.insert_node(
                    target_key,
                    NodeType.SYMBOL,
                    inheritance.target_name,
                    metadata={"unresolved": True},
                )
            store.insert_edge(
                source_key,
                NodeType.SYMBOL,
                target_key,
                NodeType.SYMBOL,
                EdgeType.INHERITS,
                metadata={"line": inheritance.line_number},
            )

        for reference in parse_result.references:
            target_key = store.resolve_symbol_node_key(reference.target_name, parse_result.module_name)
            if target_key is None:
                continue
            store.insert_edge(
                symbol_node_key(reference.source_qualified_name),
                NodeType.SYMBOL,
                target_key,
                NodeType.SYMBOL,
                EdgeType.REFERENCES,
                metadata={"line": reference.line_number},
            )

        for import_record in parse_result.imports:
            if not import_record.name:
                continue
            target_key = store.resolve_symbol_node_key(import_record.name, parse_result.module_name)
            if target_key is None:
                continue
            store.insert_edge(
                file_node_key(parse_result.source_file.relative_path),
                NodeType.FILE,
                target_key,
                NodeType.SYMBOL,
                EdgeType.REFERENCES,
                metadata={"import": import_record.display_name, "line": import_record.line_number},
            )

        store.commit()

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
