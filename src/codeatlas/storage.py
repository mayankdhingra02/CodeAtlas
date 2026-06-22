from __future__ import annotations

import json
import sqlite3
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import EdgeType, ImportRecord, NodeType, SourceFile, SymbolKind, SymbolRecord


class GraphStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(database_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
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
              UNIQUE(source_key, target_key, edge_type, metadata_json)
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

    def clear(self) -> None:
        self.connection.executescript(
            """
            DELETE FROM edges;
            DELETE FROM nodes;
            DELETE FROM imports;
            DELETE FROM symbols;
            DELETE FROM files;
            DELETE FROM files_fts;
            DELETE FROM metadata WHERE key NOT IN ('schema_version');
            """
        )
        self.connection.commit()

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
        self.connection.execute(
            """
            INSERT OR IGNORE INTO edges(
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
                json.dumps(metadata or {}, sort_keys=True),
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

    def resolve_symbol_node_key(self, name: str, source_module: str | None = None) -> str | None:
        rows = self.symbols_by_name(name)
        if not rows:
            rows = list(
                self.connection.execute(
                    """
                    SELECT s.*, f.path AS file_path
                    FROM symbols s
                    JOIN files f ON f.id = s.file_id
                    WHERE s.qualified_name LIKE ?
                    ORDER BY LENGTH(s.qualified_name)
                    LIMIT 10
                    """,
                    (f"%.{name}",),
                ).fetchall()
            )
        if not rows:
            return None
        if source_module:
            for row in rows:
                if str(row["module"]) == source_module:
                    return symbol_node_key(str(row["qualified_name"]))
        return symbol_node_key(str(rows[0]["qualified_name"]))

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

    def files_in_directories(self, directories: Iterable[str]) -> list[sqlite3.Row]:
        directory_list = sorted(set(directories))
        if not directory_list:
            return self.file_rows()
        rows: list[sqlite3.Row] = []
        for directory in directory_list:
            if directory in {"", "."}:
                rows.extend(
                    self.connection.execute(
                        "SELECT * FROM files WHERE instr(path, '/') = 0"
                    ).fetchall()
                )
            else:
                rows.extend(
                    self.connection.execute(
                        "SELECT * FROM files WHERE path LIKE ?",
                        (f"{directory.rstrip('/')}/%",),
                    ).fetchall()
                )
        unique: dict[str, sqlite3.Row] = {}
        for row in rows:
            unique[str(row["path"])] = row
        return list(unique.values())

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
