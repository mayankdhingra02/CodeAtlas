from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import EdgeType, ImportRecord, NodeType, SourceFile, SymbolKind, SymbolRecord

SCHEMA_VERSION = 3
MAX_EDGE_EXAMPLES = 20
MAX_EDGE_LINES = 1000
RESOLUTION_TIER_PRIORITY = {
    "unresolved": 0,
    "heuristic": 1,
    "name": 2,
    "unique_name": 2,
    "same_module": 3,
    "import_scoped": 4,
    "exact_qualified": 5,
    "parser": 6,
    "external": 7,
    "scip": 8,
}


class GraphStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(database_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._symbol_resolution_index: SymbolResolutionIndex | None = None

    def close(self) -> None:
        self.connection.close()

    def initialize(self, *, validate_schema: bool = True) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT NOT NULL UNIQUE,
              language TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL,
              sha256 TEXT NOT NULL,
              line_count INTEGER NOT NULL,
              indexed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS symbols (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              qualified_name TEXT NOT NULL UNIQUE,
              kind TEXT NOT NULL,
              module TEXT NOT NULL,
              line_start INTEGER NOT NULL,
              line_end INTEGER NOT NULL,
              col_start INTEGER NOT NULL,
              col_end INTEGER NOT NULL,
              docstring TEXT,
              decorators_json TEXT NOT NULL,
              signature TEXT,
              parent_qualified_name TEXT
            );

            CREATE TABLE IF NOT EXISTS imports (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              module TEXT NOT NULL,
              name TEXT,
              alias TEXT,
              line_number INTEGER NOT NULL,
              is_from INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snippets (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              symbol_id INTEGER REFERENCES symbols(id) ON DELETE CASCADE,
              cache_key TEXT NOT NULL UNIQUE,
              file_path TEXT NOT NULL,
              qualified_name TEXT,
              kind TEXT NOT NULL,
              line_start INTEGER NOT NULL,
              line_end INTEGER NOT NULL,
              code TEXT NOT NULL,
              sha256 TEXT NOT NULL,
              indexed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS nodes (
              key TEXT PRIMARY KEY,
              type TEXT NOT NULL,
              label TEXT NOT NULL,
              file_path TEXT,
              symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS edges (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_key TEXT NOT NULL,
              source_type TEXT NOT NULL,
              target_key TEXT NOT NULL,
              target_type TEXT NOT NULL,
              edge_type TEXT NOT NULL,
              weight REAL NOT NULL DEFAULT 1.0,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              UNIQUE(source_key, target_key, edge_type)
            );

            CREATE TABLE IF NOT EXISTS metadata (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
              path,
              language,
              content
            );

            CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
            CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);
            CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
            CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_id);
            CREATE INDEX IF NOT EXISTS idx_snippets_file ON snippets(file_path);
            CREATE INDEX IF NOT EXISTS idx_snippets_symbol ON snippets(qualified_name);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_key);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_key);
            CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);
            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);

            CREATE VIEW IF NOT EXISTS classes AS
              SELECT * FROM symbols WHERE kind = 'CLASS';
            CREATE VIEW IF NOT EXISTS functions AS
              SELECT * FROM symbols WHERE kind = 'FUNCTION';
            CREATE VIEW IF NOT EXISTS methods AS
              SELECT * FROM symbols WHERE kind = 'METHOD';
            """
        )
        self.connection.commit()
        if validate_schema:
            self.validate_schema()

    def validate_schema(self) -> None:
        status = self.schema_version_status()
        if status["ok"]:
            return
        msg = (
            f"CodeAtlas index schema is {status['actual']}; expected {SCHEMA_VERSION}. "
            "Run `codeatlas index <repo>` to rebuild the local index."
        )
        raise RuntimeError(msg)

    def schema_version_status(self) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        row_count = self.connection.execute("SELECT COUNT(*) AS count FROM files").fetchone()
        file_count = int(row_count["count"])
        if row is None:
            return {"ok": file_count == 0, "actual": "missing", "expected": SCHEMA_VERSION}
        try:
            actual = json.loads(str(row["value"]))
        except json.JSONDecodeError:
            actual = str(row["value"])
        return {"ok": actual == SCHEMA_VERSION, "actual": actual, "expected": SCHEMA_VERSION}

    def clear(self) -> None:
        self.connection.executescript(
            """
            DELETE FROM edges;
            DELETE FROM nodes;
            DELETE FROM imports;
            DELETE FROM snippets;
            DELETE FROM symbols;
            DELETE FROM files;
            DELETE FROM files_fts;
            DELETE FROM metadata WHERE key NOT IN ('schema_version');
            """
        )
        self.connection.commit()
        self._symbol_resolution_index = None

    def recreate(self) -> None:
        self.connection.executescript(
            """
            DROP VIEW IF EXISTS classes;
            DROP VIEW IF EXISTS functions;
            DROP VIEW IF EXISTS methods;
            DROP TABLE IF EXISTS files_fts;
            DROP TABLE IF EXISTS edges;
            DROP TABLE IF EXISTS nodes;
            DROP TABLE IF EXISTS imports;
            DROP TABLE IF EXISTS snippets;
            DROP TABLE IF EXISTS symbols;
            DROP TABLE IF EXISTS files;
            DROP TABLE IF EXISTS metadata;
            """
        )
        self.connection.commit()
        self._symbol_resolution_index = None
        self.initialize(validate_schema=False)

    def upsert_file(self, source_file: SourceFile) -> int:
        indexed_at = utc_now()
        self.connection.execute(
            """
            INSERT INTO files(path, language, size_bytes, mtime_ns, sha256, line_count, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              language = excluded.language,
              size_bytes = excluded.size_bytes,
              mtime_ns = excluded.mtime_ns,
              sha256 = excluded.sha256,
              line_count = excluded.line_count,
              indexed_at = excluded.indexed_at
            """,
            (
                source_file.relative_path,
                source_file.language,
                source_file.size_bytes,
                source_file.mtime_ns,
                source_file.sha256,
                source_file.line_count,
                indexed_at,
            ),
        )
        row = self.connection.execute(
            "SELECT id FROM files WHERE path = ?", (source_file.relative_path,)
        ).fetchone()
        return int(row["id"])

    def delete_file(self, relative_path: str, replacement_keys: set[str] | None = None) -> bool:
        row = self.connection.execute(
            "SELECT id FROM files WHERE path = ?", (relative_path,)
        ).fetchone()
        if row is None:
            return False
        node_rows = self.connection.execute(
            "SELECT key FROM nodes WHERE file_path = ? OR key = ?",
            (relative_path, file_node_key(relative_path)),
        ).fetchall()
        keys = [str(node["key"]) for node in node_rows]
        if keys:
            placeholders = ",".join("?" for _ in keys)
            target_delete_keys = (
                keys
                if replacement_keys is None
                else [key for key in keys if key not in replacement_keys]
            )
            target_clause = ""
            params: tuple[str, ...] = tuple(keys)
            if target_delete_keys:
                target_placeholders = ",".join("?" for _ in target_delete_keys)
                target_clause = f" OR target_key IN ({target_placeholders})"
                params = (*params, *target_delete_keys)
            self.connection.execute(
                f"DELETE FROM edges WHERE source_key IN ({placeholders}){target_clause}",
                params,
            )
            self.connection.execute(
                f"DELETE FROM nodes WHERE key IN ({placeholders})",
                tuple(keys),
            )
        self.connection.execute("DELETE FROM files WHERE id = ?", (int(row["id"]),))
        self.connection.execute("DELETE FROM files_fts WHERE path = ?", (relative_path,))
        self.connection.commit()
        return True

    def previous_file_hashes(self) -> dict[str, str]:
        rows = self.connection.execute("SELECT path, sha256 FROM files").fetchall()
        return {str(row["path"]): str(row["sha256"]) for row in rows}

    def insert_node(
        self,
        key: str,
        node_type: NodeType,
        label: str,
        *,
        file_path: str | None = None,
        symbol_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO nodes(key, type, label, file_path, symbol_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              type = excluded.type,
              label = excluded.label,
              file_path = COALESCE(excluded.file_path, nodes.file_path),
              symbol_id = COALESCE(excluded.symbol_id, nodes.symbol_id),
              metadata_json = excluded.metadata_json
            """,
            (
                key,
                node_type.value,
                label,
                file_path,
                symbol_id,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )

    def insert_symbol(
        self,
        file_id: int,
        relative_path: str,
        symbol: SymbolRecord,
    ) -> int:
        self.connection.execute(
            """
            INSERT INTO symbols(
              file_id, name, qualified_name, kind, module, line_start, line_end,
              col_start, col_end, docstring, decorators_json, signature,
              parent_qualified_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(qualified_name) DO UPDATE SET
              file_id = excluded.file_id,
              name = excluded.name,
              kind = excluded.kind,
              module = excluded.module,
              line_start = excluded.line_start,
              line_end = excluded.line_end,
              col_start = excluded.col_start,
              col_end = excluded.col_end,
              docstring = excluded.docstring,
              decorators_json = excluded.decorators_json,
              signature = excluded.signature,
              parent_qualified_name = excluded.parent_qualified_name
            """,
            (
                file_id,
                symbol.name,
                symbol.qualified_name,
                symbol.kind.value,
                symbol.module,
                symbol.line_start,
                symbol.line_end,
                symbol.col_start,
                symbol.col_end,
                symbol.docstring,
                json.dumps(list(symbol.decorators)),
                symbol.signature,
                symbol.parent_qualified_name,
            ),
        )
        row = self.connection.execute(
            "SELECT id FROM symbols WHERE qualified_name = ?", (symbol.qualified_name,)
        ).fetchone()
        symbol_id = int(row["id"])
        self._symbol_resolution_index = None
        self.insert_node(
            symbol.node_key,
            symbol.node_type,
            symbol.name,
            file_path=relative_path,
            symbol_id=symbol_id,
            metadata={
                "qualified_name": symbol.qualified_name,
                "line_start": symbol.line_start,
                "line_end": symbol.line_end,
            },
        )
        return symbol_id

    def insert_import(self, file_id: int, record: ImportRecord) -> None:
        self.connection.execute(
            """
            INSERT INTO imports(file_id, module, name, alias, line_number, is_from)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                record.module,
                record.name,
                record.alias,
                record.line_number,
                1 if record.is_from else 0,
            ),
        )

    def upsert_file_search(self, source_file: SourceFile, content: str) -> None:
        self.connection.execute("DELETE FROM files_fts WHERE path = ?", (source_file.relative_path,))
        self.connection.execute(
            """
            INSERT INTO files_fts(path, language, content)
            VALUES (?, ?, ?)
            """,
            (source_file.relative_path, source_file.language, content),
        )

    def upsert_file_snippet(self, file_id: int, source_file: SourceFile, content: str) -> None:
        self._upsert_snippet(
            file_id=file_id,
            symbol_id=None,
            cache_key=file_node_key(source_file.relative_path),
            file_path=source_file.relative_path,
            qualified_name=source_file.relative_path,
            kind=NodeType.FILE.value,
            line_start=1 if source_file.line_count else 0,
            line_end=source_file.line_count,
            code=content,
        )

    def upsert_symbol_snippet(
        self,
        file_id: int,
        symbol_id: int,
        relative_path: str,
        symbol: SymbolRecord,
        content: str,
    ) -> None:
        self._upsert_snippet(
            file_id=file_id,
            symbol_id=symbol_id,
            cache_key=symbol.node_key,
            file_path=relative_path,
            qualified_name=symbol.qualified_name,
            kind=symbol.kind.value,
            line_start=symbol.line_start,
            line_end=symbol.line_end,
            code=line_range_from_text(content, symbol.line_start, symbol.line_end),
        )

    def cached_symbol_snippet(self, qualified_name: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT code
            FROM snippets
            WHERE cache_key = ?
               OR qualified_name = ?
            ORDER BY symbol_id IS NULL
            LIMIT 1
            """,
            (symbol_node_key(qualified_name), qualified_name),
        ).fetchone()
        return str(row["code"]) if row is not None else None

    def cached_file_content(self, relative_path: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT code
            FROM snippets
            WHERE cache_key = ?
            LIMIT 1
            """,
            (file_node_key(relative_path),),
        ).fetchone()
        return str(row["code"]) if row is not None else None

    def cached_line_range(self, relative_path: str, line_start: int, line_end: int) -> str | None:
        content = self.cached_file_content(relative_path)
        if content is None:
            return None
        return line_range_from_text(content, line_start, line_end)

    def _upsert_snippet(
        self,
        *,
        file_id: int,
        symbol_id: int | None,
        cache_key: str,
        file_path: str,
        qualified_name: str | None,
        kind: str,
        line_start: int,
        line_end: int,
        code: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO snippets(
              file_id, symbol_id, cache_key, file_path, qualified_name, kind,
              line_start, line_end, code, sha256, indexed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
              file_id = excluded.file_id,
              symbol_id = excluded.symbol_id,
              file_path = excluded.file_path,
              qualified_name = excluded.qualified_name,
              kind = excluded.kind,
              line_start = excluded.line_start,
              line_end = excluded.line_end,
              code = excluded.code,
              sha256 = excluded.sha256,
              indexed_at = excluded.indexed_at
            """,
            (
                file_id,
                symbol_id,
                cache_key,
                file_path,
                qualified_name,
                kind,
                line_start,
                line_end,
                code,
                snippet_sha256(code),
                utc_now(),
            ),
        )

    def insert_edge(
        self,
        source_key: str,
        source_type: NodeType,
        target_key: str,
        target_type: NodeType,
        edge_type: EdgeType,
        *,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata_payload = metadata or {}
        existing = self.connection.execute(
            """
            SELECT id, weight, metadata_json
            FROM edges
            WHERE source_key = ? AND target_key = ? AND edge_type = ?
            """,
            (source_key, target_key, edge_type.value),
        ).fetchone()
        if existing is not None:
            merged = merge_edge_metadata(
                json.loads(str(existing["metadata_json"] or "{}")),
                metadata_payload,
            )
            self.connection.execute(
                """
                UPDATE edges
                SET
                  source_type = ?,
                  target_type = ?,
                  weight = ?,
                  metadata_json = ?
                WHERE id = ?
                """,
                (
                    source_type.value,
                    target_type.value,
                    max(float(existing["weight"] or 1.0), weight),
                    json.dumps(merged, sort_keys=True),
                    int(existing["id"]),
                ),
            )
            return
        self.connection.execute(
            """
            INSERT INTO edges(
              source_key, source_type, target_key, target_type, edge_type, weight, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_key,
                source_type.value,
                target_key,
                target_type.value,
                edge_type.value,
                weight,
                json.dumps(metadata_payload, sort_keys=True),
            ),
        )

    def set_metadata(self, key: str, value: Any) -> None:
        self.connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, json.dumps(value, sort_keys=True)),
        )

    def get_metadata(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return json.loads(str(row["value"]))

    def commit(self) -> None:
        self.connection.commit()

    def count_edges(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM edges").fetchone()
        return int(row["count"])

    def count_symbols(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM symbols").fetchone()
        return int(row["count"])

    def find_symbols(self, query: str, limit: int = 50) -> list[sqlite3.Row]:
        normalized = query.strip()
        if not normalized:
            return []
        rows = self.connection.execute(
            """
            SELECT s.*, f.path AS file_path
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            WHERE s.name = ?
               OR s.qualified_name = ?
               OR s.name LIKE ?
               OR s.qualified_name LIKE ?
               OR COALESCE(s.docstring, '') LIKE ?
            ORDER BY
              CASE
                WHEN s.name = ? THEN 0
                WHEN s.qualified_name = ? THEN 1
                WHEN s.name LIKE ? THEN 2
                ELSE 3
              END,
              LENGTH(s.qualified_name)
            LIMIT ?
            """,
            (
                normalized,
                normalized,
                f"%{normalized}%",
                f"%{normalized}%",
                f"%{normalized}%",
                normalized,
                normalized,
                f"{normalized}%",
                limit,
            ),
        ).fetchall()
        return list(rows)

    def all_symbols(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT s.*, f.path AS file_path
                FROM symbols s
                JOIN files f ON f.id = s.file_id
                ORDER BY s.qualified_name
                """
            ).fetchall()
        )

    def symbols_by_name(self, name: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT s.*, f.path AS file_path
                FROM symbols s
                JOIN files f ON f.id = s.file_id
                WHERE s.name = ? OR s.qualified_name = ?
                ORDER BY LENGTH(s.qualified_name)
                """,
                (name, name),
            ).fetchall()
        )

    def resolve_symbol_node_key(
        self,
        name: str,
        source_module: str | None = None,
        imports: Iterable[ImportRecord] = (),
    ) -> str | None:
        resolved = self.resolve_symbol(name, source_module, imports)
        return resolved.node_key if resolved else None

    def resolve_symbol(
        self,
        name: str,
        source_module: str | None = None,
        imports: Iterable[ImportRecord] = (),
    ) -> SymbolResolution | None:
        return self.symbol_resolution_index().resolve(name, source_module, imports)

    def symbol_resolution_index(self) -> SymbolResolutionIndex:
        if self._symbol_resolution_index is None:
            self._symbol_resolution_index = SymbolResolutionIndex(self.all_symbols())
        return self._symbol_resolution_index

    def traverse(self, start_keys: Iterable[str], depth: int) -> tuple[set[str], list[sqlite3.Row]]:
        start = set(start_keys)
        visited = set(start)
        edges_seen: dict[int, sqlite3.Row] = {}
        queue: deque[tuple[str, int]] = deque((key, 0) for key in start)
        while queue:
            key, distance = queue.popleft()
            if distance >= depth:
                continue
            edge_rows = self.edges_for_key(key)
            for edge in edge_rows:
                edges_seen[int(edge["id"])] = edge
                neighbor = (
                    str(edge["target_key"])
                    if str(edge["source_key"]) == key
                    else str(edge["source_key"])
                )
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, distance + 1))
        return visited, list(edges_seen.values())

    def edges_for_key(self, key: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT * FROM edges
                WHERE source_key = ? OR target_key = ?
                """,
                (key, key),
            ).fetchall()
        )

    def outgoing_edges(
        self,
        key: str,
        *,
        edge_types: tuple[str, ...] = (),
    ) -> list[sqlite3.Row]:
        """Return only edges whose persisted direction starts at ``key``."""
        normalized_types = tuple(str(edge_type) for edge_type in edge_types)
        if not normalized_types:
            return list(
                self.connection.execute(
                    """
                    SELECT * FROM edges
                    WHERE source_key = ?
                    ORDER BY edge_type, target_key, id
                    """,
                    (key,),
                ).fetchall()
            )
        placeholders = ",".join("?" for _ in normalized_types)
        return list(
            self.connection.execute(
                f"""
                SELECT * FROM edges
                WHERE source_key = ?
                  AND edge_type IN ({placeholders})
                ORDER BY edge_type, target_key, id
                """,
                (key, *normalized_types),
            ).fetchall()
        )

    def incoming_edges(
        self,
        key: str,
        *,
        edge_types: tuple[str, ...] = (),
    ) -> list[sqlite3.Row]:
        """Return only edges whose persisted direction ends at ``key``."""
        normalized_types = tuple(str(edge_type) for edge_type in edge_types)
        if not normalized_types:
            return list(
                self.connection.execute(
                    """
                    SELECT * FROM edges
                    WHERE target_key = ?
                    ORDER BY edge_type, source_key, id
                    """,
                    (key,),
                ).fetchall()
            )
        placeholders = ",".join("?" for _ in normalized_types)
        return list(
            self.connection.execute(
                f"""
                SELECT * FROM edges
                WHERE target_key = ?
                  AND edge_type IN ({placeholders})
                ORDER BY edge_type, source_key, id
                """,
                (key, *normalized_types),
            ).fetchall()
        )

    def nodes_by_keys(self, keys: Iterable[str]) -> list[sqlite3.Row]:
        key_list = list(keys)
        if not key_list:
            return []
        placeholders = ",".join("?" for _ in key_list)
        return list(
            self.connection.execute(
                f"SELECT * FROM nodes WHERE key IN ({placeholders})",
                tuple(key_list),
            ).fetchall()
        )

    def symbols_for_node_keys(self, keys: Iterable[str]) -> list[sqlite3.Row]:
        key_list = [key for key in keys if key.startswith("symbol:")]
        if not key_list:
            return []
        qualified_names = [key.removeprefix("symbol:") for key in key_list]
        placeholders = ",".join("?" for _ in qualified_names)
        return list(
            self.connection.execute(
                f"""
                SELECT s.*, f.path AS file_path
                FROM symbols s
                JOIN files f ON f.id = s.file_id
                WHERE s.qualified_name IN ({placeholders})
                """,
                tuple(qualified_names),
            ).fetchall()
        )

    def imports_for_files(self, file_paths: Iterable[str]) -> list[sqlite3.Row]:
        paths = list(file_paths)
        if not paths:
            return []
        placeholders = ",".join("?" for _ in paths)
        return list(
            self.connection.execute(
                f"""
                SELECT i.*, f.path AS file_path
                FROM imports i
                JOIN files f ON f.id = i.file_id
                WHERE f.path IN ({placeholders})
                ORDER BY f.path, i.line_number
                """,
                tuple(paths),
            ).fetchall()
        )

    def file_rows(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM files ORDER BY path").fetchall())

    def search_files(self, query: str, limit: int = 20) -> list[sqlite3.Row]:
        fts_query = fts_query_for(query)
        if not fts_query:
            return []
        try:
            rows = self.connection.execute(
                """
                SELECT
                  path,
                  language,
                  snippet(files_fts, 2, '', '', ' ... ', 18) AS snippet,
                  bm25(files_fts) AS rank
                FROM files_fts
                WHERE files_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return list(rows)

    def files_by_paths(self, paths: Iterable[str]) -> list[sqlite3.Row]:
        path_list = sorted(set(paths))
        if not path_list:
            return []
        placeholders = ",".join("?" for _ in path_list)
        return list(
            self.connection.execute(
                f"SELECT * FROM files WHERE path IN ({placeholders})",
                tuple(path_list),
            ).fetchall()
        )

    def parse_quality_stats(self) -> dict[str, Any]:
        symbol_rows = self.connection.execute(
            """
            SELECT f.path, COUNT(s.id) AS symbols
            FROM files f
            LEFT JOIN symbols s ON s.file_id = f.id
            GROUP BY f.path
            """
        ).fetchall()
        symbols_by_path = {str(row["path"]): int(row["symbols"] or 0) for row in symbol_rows}
        call_counts: dict[str, dict[str, int]] = {}
        for row in self.connection.execute(
            """
            SELECT n.file_path, e.target_key, e.metadata_json
            FROM edges e
            LEFT JOIN nodes n ON n.key = e.source_key
            WHERE e.edge_type = 'CALLS'
            """
        ).fetchall():
            file_path = str(row["file_path"] or "")
            if not file_path:
                continue
            metadata = json.loads(str(row["metadata_json"] or "{}"))
            count = _edge_count(metadata)
            bucket = call_counts.setdefault(file_path, {"total": 0, "unresolved": 0})
            bucket["total"] += count
            if str(row["target_key"]).startswith("symbol_ref:") or metadata.get(
                "resolution_tier"
            ) == "unresolved":
                bucket["unresolved"] += count

        files: list[dict[str, Any]] = []
        total_symbols = 0
        total_lines = 0
        total_calls = 0
        total_unresolved = 0
        for row in self.file_rows():
            path = str(row["path"])
            line_count = int(row["line_count"] or 0)
            symbols = symbols_by_path.get(path, 0)
            calls = call_counts.get(path, {"total": 0, "unresolved": 0})
            total_symbols += symbols
            total_lines += line_count
            total_calls += calls["total"]
            total_unresolved += calls["unresolved"]
            files.append(
                {
                    "path": path,
                    "language": str(row["language"]),
                    "line_count": line_count,
                    "symbols": symbols,
                    "symbols_per_kloc": symbols_per_kloc(symbols, line_count),
                    "calls": calls["total"],
                    "unresolved_calls": calls["unresolved"],
                    "unresolved_call_ratio": ratio(calls["unresolved"], calls["total"]),
                }
            )
        return {
            "summary": {
                "files": len(files),
                "symbols": total_symbols,
                "symbols_per_kloc": symbols_per_kloc(total_symbols, total_lines),
                "calls": total_calls,
                "unresolved_calls": total_unresolved,
                "unresolved_call_ratio": ratio(total_unresolved, total_calls),
            },
            "files": files,
        }

    def repository_stats(self) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM files) AS files_indexed,
              (SELECT COUNT(*) FROM symbols WHERE kind = 'CLASS') AS classes,
              (SELECT COUNT(*) FROM symbols WHERE kind = 'FUNCTION') AS functions,
              (SELECT COUNT(*) FROM symbols WHERE kind = 'METHOD') AS methods,
              (SELECT COUNT(*) FROM nodes) AS graph_nodes,
              (SELECT COUNT(*) FROM edges) AS graph_edges,
              (SELECT COUNT(*) FROM imports) AS imports,
              (SELECT MAX(indexed_at) FROM files) AS last_indexed_at
            """
        ).fetchone()
        return dict(row)

    def dependency_edges_for_symbol(self, symbol_name: str) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
        rows = self.symbols_by_name(symbol_name)
        if not rows:
            return [], []
        keys = [symbol_node_key(str(row["qualified_name"])) for row in rows]
        incoming: list[sqlite3.Row] = []
        outgoing: list[sqlite3.Row] = []
        for key in keys:
            incoming.extend(
                self.connection.execute(
                    "SELECT * FROM edges WHERE target_key = ? ORDER BY edge_type, source_key",
                    (key,),
                ).fetchall()
            )
            outgoing.extend(
                self.connection.execute(
                    "SELECT * FROM edges WHERE source_key = ? ORDER BY edge_type, target_key",
                    (key,),
                ).fetchall()
            )
        return incoming, outgoing


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def line_range_from_text(content: str, line_start: int, line_end: int) -> str:
    lines = content.splitlines()
    start = max(line_start - 1, 0)
    end = min(max(line_end, line_start), len(lines))
    return "\n".join(lines[start:end])


def snippet_sha256(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8", errors="replace")).hexdigest()


def merge_edge_metadata(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    existing_count = _edge_count(existing)
    incoming_count = _edge_count(incoming)
    for key, value in incoming.items():
        if key not in merged and key not in {"count", "lines", "examples"}:
            merged[key] = value
    merged.update(preferred_resolution_metadata(existing, incoming))
    lines = merge_edge_lines(existing, incoming)
    if lines:
        merged["line"] = lines[0]
        merged["lines"] = lines[:MAX_EDGE_LINES]
    examples = merge_edge_examples(existing, incoming)
    if examples:
        merged["examples"] = examples[:MAX_EDGE_EXAMPLES]
    merged["count"] = existing_count + incoming_count
    return merged


def preferred_resolution_metadata(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    existing_confidence = _edge_confidence(existing)
    incoming_confidence = _edge_confidence(incoming)
    existing_tier = str(existing.get("resolution_tier") or "")
    incoming_tier = str(incoming.get("resolution_tier") or "")
    if incoming_confidence < existing_confidence and (
        RESOLUTION_TIER_PRIORITY.get(incoming_tier, -1)
        <= RESOLUTION_TIER_PRIORITY.get(existing_tier, -1)
    ):
        return {}
    selected: dict[str, Any] = {}
    if "confidence" in incoming:
        selected["confidence"] = incoming["confidence"]
    if incoming_tier:
        selected["resolution_tier"] = incoming_tier
    if "source" in incoming:
        selected["source"] = incoming["source"]
    return selected


def _edge_confidence(metadata: dict[str, Any]) -> float:
    try:
        return float(metadata.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _edge_count(metadata: dict[str, Any]) -> int:
    try:
        return max(1, int(metadata.get("count") or 1))
    except (TypeError, ValueError):
        return 1


def symbols_per_kloc(symbols: int, line_count: int) -> float:
    if line_count <= 0:
        return 0.0
    return round(symbols / (line_count / 1000), 2)


def ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def merge_edge_lines(*metadata_items: dict[str, Any]) -> list[int]:
    lines: list[int] = []
    seen: set[int] = set()
    for metadata in metadata_items:
        for raw in _metadata_lines(metadata):
            try:
                line = int(raw)
            except (TypeError, ValueError):
                continue
            if line <= 0 or line in seen:
                continue
            seen.add(line)
            lines.append(line)
    return lines


def _metadata_lines(metadata: dict[str, Any]) -> list[Any]:
    lines: list[Any] = []
    if "line" in metadata:
        lines.append(metadata["line"])
    raw_lines = metadata.get("lines")
    if isinstance(raw_lines, list):
        lines.extend(raw_lines)
    return lines


def merge_edge_examples(existing: dict[str, Any], incoming: dict[str, Any]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for metadata in (existing, incoming):
        raw_examples = metadata.get("examples")
        if isinstance(raw_examples, list):
            for example in raw_examples:
                if isinstance(example, dict):
                    _append_edge_example(examples, seen, example)
        else:
            occurrence = edge_occurrence_metadata(metadata)
            if occurrence:
                _append_edge_example(examples, seen, occurrence)
    return examples


def _append_edge_example(
    examples: list[dict[str, Any]],
    seen: set[str],
    example: dict[str, Any],
) -> None:
    key = json.dumps(example, sort_keys=True, default=str)
    if key in seen:
        return
    seen.add(key)
    examples.append(example)


def edge_occurrence_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key not in {"count", "examples", "lines"} and value not in (None, "", [])
    }


@dataclass(frozen=True)
class SymbolResolution:
    node_key: str
    tier: str


class SymbolResolutionIndex:
    def __init__(self, rows: Iterable[sqlite3.Row]) -> None:
        self.by_name: dict[str, list[sqlite3.Row]] = {}
        self.by_qualified_name: dict[str, sqlite3.Row] = {}
        for row in rows:
            name = str(row["name"])
            qualified_name = str(row["qualified_name"])
            self.by_name.setdefault(name, []).append(row)
            self.by_qualified_name[qualified_name] = row
        for candidates in self.by_name.values():
            candidates.sort(key=_symbol_candidate_sort_key)

    def resolve(
        self,
        name: str,
        source_module: str | None,
        imports: Iterable[ImportRecord],
    ) -> SymbolResolution | None:
        cleaned = name.strip()
        if not cleaned:
            return None
        exact = self.by_qualified_name.get(cleaned)
        if exact is not None:
            return SymbolResolution(
                symbol_node_key(str(exact["qualified_name"])),
                "exact_qualified",
            )
        imported = self._resolve_import(cleaned, source_module, imports)
        if imported is not None:
            return imported
        if source_module:
            same_module = self.by_qualified_name.get(f"{source_module}.{cleaned}")
            if same_module is not None:
                return SymbolResolution(
                    symbol_node_key(str(same_module["qualified_name"])),
                    "same_module",
                )
        same_module_candidate = self._same_module_candidate(cleaned, source_module)
        if same_module_candidate is not None:
            return same_module_candidate
        unique_name = self._unique_name_candidate(cleaned)
        if unique_name is not None:
            return unique_name
        return None

    def _resolve_import(
        self,
        name: str,
        source_module: str | None,
        imports: Iterable[ImportRecord],
    ) -> SymbolResolution | None:
        for record in imports:
            imported_module = normalize_import_module(record.module, source_module)
            if record.is_from and record.name:
                local_name = record.alias or record.name
                if name != local_name:
                    continue
                qualified_name = (
                    f"{imported_module}.{record.name}" if imported_module else record.name
                )
                row = self.by_qualified_name.get(qualified_name)
                if row is not None:
                    return SymbolResolution(
                        symbol_node_key(str(row["qualified_name"])),
                        "import_scoped",
                    )
                candidates = [
                    candidate
                    for candidate in self.by_name.get(record.name, [])
                    if str(candidate["module"]) == imported_module
                ]
                if len(candidates) == 1:
                    return SymbolResolution(
                        symbol_node_key(str(candidates[0]["qualified_name"])),
                        "import_scoped",
                    )
            elif not record.is_from:
                local_module_name = record.alias or record.module.rsplit(".", 1)[-1]
                if name == local_module_name and imported_module in self.by_qualified_name:
                    qualified_name = str(self.by_qualified_name[imported_module]["qualified_name"])
                    return SymbolResolution(symbol_node_key(qualified_name), "import_scoped")
                candidates = [
                    candidate
                    for candidate in self.by_name.get(name, [])
                    if str(candidate["module"]) == imported_module
                ]
                if len(candidates) == 1:
                    return SymbolResolution(
                        symbol_node_key(str(candidates[0]["qualified_name"])),
                        "import_scoped",
                    )
        return None

    def _same_module_candidate(
        self,
        name: str,
        source_module: str | None,
    ) -> SymbolResolution | None:
        if not source_module:
            return None
        candidates = [
            candidate
            for candidate in self.by_name.get(name, [])
            if str(candidate["module"]) == source_module
        ]
        if len(candidates) == 1:
            return SymbolResolution(
                symbol_node_key(str(candidates[0]["qualified_name"])),
                "same_module",
            )
        return None

    def _unique_name_candidate(self, name: str) -> SymbolResolution | None:
        candidates = self.by_name.get(name, [])
        if len(candidates) == 1:
            return SymbolResolution(
                symbol_node_key(str(candidates[0]["qualified_name"])),
                "unique_name",
            )
        return None


def _symbol_candidate_sort_key(row: sqlite3.Row) -> tuple[int, str]:
    qualified_name = str(row["qualified_name"])
    return (qualified_name.count("."), qualified_name)


def normalize_import_module(module: str, source_module: str | None) -> str:
    if not module.startswith("."):
        return module
    if not source_module:
        return module.lstrip(".")
    leading_dots = len(module) - len(module.lstrip("."))
    remainder = module[leading_dots:].replace("/", ".").strip(".")
    base_parts = source_module.split(".")[:-1]
    if leading_dots > 1:
        base_parts = base_parts[: max(0, len(base_parts) - (leading_dots - 1))]
    parts = [*base_parts]
    if remainder:
        parts.extend(part for part in remainder.split(".") if part)
    return ".".join(parts)


def file_node_key(relative_path: str) -> str:
    return f"file:{relative_path}"


def module_node_key(module_name: str) -> str:
    return f"module:{module_name}"


def symbol_node_key(qualified_name: str) -> str:
    return f"symbol:{qualified_name}"


def unresolved_symbol_node_key(name: str) -> str:
    return f"symbol_ref:{name}"


def fts_query_for(query: str) -> str:
    stop_words = {
        "a",
        "an",
        "and",
        "are",
        "for",
        "from",
        "how",
        "in",
        "is",
        "of",
        "or",
        "the",
        "this",
        "to",
        "what",
        "where",
        "why",
        "with",
    }
    terms = []
    for raw in query.replace("/", " ").replace(".", " ").replace("-", " ").split():
        term = "".join(char for char in raw.lower() if char.isalnum() or char == "_")
        if len(term) < 3 or term in stop_words:
            continue
        terms.append(term)
    return " OR ".join(f"{term}*" for term in dict.fromkeys(terms[:8]))


def node_type_for_symbol_kind(kind: str) -> NodeType:
    if kind == SymbolKind.CLASS.value:
        return NodeType.CLASS
    if kind == SymbolKind.METHOD.value:
        return NodeType.METHOD
    return NodeType.FUNCTION
