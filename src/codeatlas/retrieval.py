from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import CodeAtlasPaths, resolve_repo_root
from .models import (
    ContextSnippet,
    DependencyExplanation,
    EdgeType,
    RepositoryStats,
    RetrievalResult,
    RetrievalTimings,
    TokenReport,
    estimate_tokens,
)
from .storage import GraphStore, symbol_node_key


class RetrievalEngine:
    def retrieve(
        self,
        repo_path: str | Path,
        query: str,
        *,
        depth: int = 2,
        max_tokens: int = 8000,
    ) -> RetrievalResult:
        total_start = time.perf_counter()
        repo_root = resolve_repo_root(repo_path)
        store = self._open_store(repo_root)
        try:
            lookup_start = time.perf_counter()
            matches = store.find_symbols(query)
            symbol_lookup_ms = elapsed_ms(lookup_start)

            graph_start = time.perf_counter()
            start_keys = [symbol_node_key(str(row["qualified_name"])) for row in matches]
            visited_keys, edges = store.traverse(start_keys, max(depth, 0)) if start_keys else (set(), [])
            graph_traversal_ms = elapsed_ms(graph_start)

            ranking_start = time.perf_counter()
            symbol_rows = store.symbols_for_node_keys(visited_keys)
            if not symbol_rows:
                symbol_rows = matches
            snippets = self._rank_and_snippet(
                repo_root,
                query,
                symbol_rows,
                matches,
                edges,
                max_tokens=max_tokens,
            )
            token_report = self._token_report(store, snippets)
            ranking_ms = elapsed_ms(ranking_start)

            timings = RetrievalTimings(
                symbol_lookup_ms=symbol_lookup_ms,
                graph_traversal_ms=graph_traversal_ms,
                ranking_ms=ranking_ms,
                total_ms=elapsed_ms(total_start),
            )
            return RetrievalResult(
                query=query,
                snippets=tuple(snippets),
                token_report=token_report,
                timings=timings,
            )
        finally:
            store.close()

    def find_symbol(self, repo_path: str | Path, symbol_name: str) -> list[dict[str, Any]]:
        repo_root = resolve_repo_root(repo_path)
        store = self._open_store(repo_root)
        try:
            return [row_to_dict(row) for row in store.find_symbols(symbol_name)]
        finally:
            store.close()

    def explain_dependencies(self, repo_path: str | Path, symbol_name: str) -> DependencyExplanation:
        repo_root = resolve_repo_root(repo_path)
        store = self._open_store(repo_root)
        try:
            incoming, outgoing = store.dependency_edges_for_symbol(symbol_name)
            matched_symbols = store.find_symbols(symbol_name)
            file_paths = {str(row["file_path"]) for row in matched_symbols}
            imports = tuple(record_import(row) for row in store.imports_for_files(file_paths))
            callers = tuple(
                edge_label(edge["source_key"])
                for edge in incoming
                if edge["edge_type"] == EdgeType.CALLS.value
            )
            references = tuple(
                edge_label(edge["source_key"])
                for edge in incoming
                if edge["edge_type"] == EdgeType.REFERENCES.value
            )
            callees = tuple(
                edge_label(edge["target_key"])
                for edge in outgoing
                if edge["edge_type"] == EdgeType.CALLS.value
            )
            inheritance = tuple(
                edge_label(edge["target_key"])
                for edge in outgoing
                if edge["edge_type"] == EdgeType.INHERITS.value
            )
            return DependencyExplanation(
                symbol_name=symbol_name,
                callers=dedupe(callers),
                callees=dedupe(callees),
                imports=dedupe(imports),
                inheritance=dedupe(inheritance),
                references=dedupe(references),
            )
        finally:
            store.close()

    def repository_stats(self, repo_path: str | Path) -> RepositoryStats:
        repo_root = resolve_repo_root(repo_path)
        paths = CodeAtlasPaths(repo_root)
        store = self._open_store(repo_root)
        try:
            stats = store.repository_stats()
            supported = store.get_metadata("supported_languages", ["python"])
            index_size = paths.database_path.stat().st_size if paths.database_path.exists() else 0
            return RepositoryStats(
                repo_root=repo_root,
                database_path=paths.database_path,
                files_indexed=int(stats["files_indexed"]),
                classes=int(stats["classes"]),
                functions=int(stats["functions"]),
                methods=int(stats["methods"]),
                graph_nodes=int(stats["graph_nodes"]),
                graph_edges=int(stats["graph_edges"]),
                imports=int(stats["imports"]),
                index_size_bytes=index_size,
                last_indexed_at=stats["last_indexed_at"],
                supported_languages=tuple(str(item) for item in supported),
            )
        finally:
            store.close()

    def token_report(self, repo_path: str | Path, query: str, *, depth: int = 2) -> TokenReport:
        return self.retrieve(repo_path, query, depth=depth).token_report

    def _rank_and_snippet(
        self,
        repo_root: Path,
        query: str,
        symbol_rows: list[Any],
        matches: list[Any],
        edges: list[Any],
        *,
        max_tokens: int,
    ) -> list[ContextSnippet]:
        query_lower = query.lower()
        exact_keys = {symbol_node_key(str(row["qualified_name"])) for row in matches}
        edge_bonus = self._edge_bonus(edges)
        ranked: list[tuple[float, str, Any]] = []
        seen: set[str] = set()
        for row in symbol_rows:
            qualified_name = str(row["qualified_name"])
            if qualified_name in seen:
                continue
            seen.add(qualified_name)
            key = symbol_node_key(qualified_name)
            name = str(row["name"])
            score = 0.0
            reasons: list[str] = []
            if key in exact_keys:
                score += 80
                reasons.append("symbol match")
            if name.lower() == query_lower:
                score += 100
                reasons.append("exact name")
            elif qualified_name.lower() == query_lower:
                score += 110
                reasons.append("exact qualified name")
            elif query_lower in name.lower():
                score += 55
                reasons.append("name contains query")
            elif query_lower in qualified_name.lower():
                score += 40
                reasons.append("qualified name contains query")
            if str(row["kind"]) == "METHOD":
                score += 6
            score += edge_bonus.get(key, 0.0)
            if key in edge_bonus:
                reasons.append("graph neighbor")
            ranked.append((score, ", ".join(reasons) or "graph context", row))

        ranked.sort(
            key=lambda item: (
                -item[0],
                str(item[2]["file_path"]),
                int(item[2]["line_start"]),
            )
        )

        snippets: list[ContextSnippet] = []
        used_tokens = 0
        for score, reason, row in ranked:
            code = read_line_range(
                repo_root / str(row["file_path"]),
                int(row["line_start"]),
                int(row["line_end"]),
            )
            if not code:
                continue
            snippet_tokens = estimate_tokens(code)
            if snippets and used_tokens + snippet_tokens > max_tokens:
                continue
            if not snippets and snippet_tokens > max_tokens:
                code = trim_to_tokens(code, max_tokens)
                snippet_tokens = estimate_tokens(code)
            snippets.append(
                ContextSnippet(
                    file_path=str(row["file_path"]),
                    symbol_name=str(row["name"]),
                    qualified_name=str(row["qualified_name"]),
                    kind=str(row["kind"]),
                    line_start=int(row["line_start"]),
                    line_end=int(row["line_end"]),
                    score=score,
                    reason=reason,
                    code=code,
                )
            )
            used_tokens += snippet_tokens
        return snippets

    def _edge_bonus(self, edges: list[Any]) -> dict[str, float]:
        bonuses: dict[str, float] = {}
        for edge in edges:
            edge_type = str(edge["edge_type"])
            bonus = {
                EdgeType.CALLS.value: 28.0,
                EdgeType.REFERENCES.value: 20.0,
                EdgeType.INHERITS.value: 24.0,
                EdgeType.CONTAINS.value: 10.0,
                EdgeType.IMPORTS.value: 8.0,
                EdgeType.DEFINES.value: 6.0,
            }.get(edge_type, 4.0)
            bonuses[str(edge["source_key"])] = bonuses.get(str(edge["source_key"]), 0.0) + bonus
            bonuses[str(edge["target_key"])] = bonuses.get(str(edge["target_key"]), 0.0) + bonus
        return bonuses

    def _token_report(self, store: GraphStore, snippets: list[ContextSnippet]) -> TokenReport:
        optimized = sum(snippet.estimated_tokens for snippet in snippets)
        directories = {str(Path(snippet.file_path).parent) for snippet in snippets}
        file_rows = store.files_in_directories(directories)
        baseline = sum(estimate_tokens("x" * int(row["size_bytes"])) for row in file_rows)
        if baseline < optimized:
            baseline = optimized
        return TokenReport(baseline_tokens=baseline, optimized_tokens=optimized)

    def _open_store(self, repo_root: Path) -> GraphStore:
        paths = CodeAtlasPaths(repo_root)
        if not paths.database_path.exists():
            msg = f"No CodeAtlas index found at {paths.database_path}. Run `codeatlas index {repo_root}` first."
            raise FileNotFoundError(msg)
        store = GraphStore(paths.database_path)
        store.initialize()
        return store


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def read_line_range(path: Path, line_start: int, line_end: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return ""
    start = max(line_start - 1, 0)
    end = min(line_end, len(lines))
    return "\n".join(lines[start:end])


def trim_to_tokens(code: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * 4
    if len(code) <= max_chars:
        return code
    return code[:max_chars].rstrip() + "\n# ... truncated by CodeAtlas token limit"


def row_to_dict(row: Any) -> dict[str, Any]:
    payload = dict(row)
    decorators = payload.get("decorators_json")
    if isinstance(decorators, str):
        try:
            payload["decorators"] = json.loads(decorators)
        except json.JSONDecodeError:
            payload["decorators"] = []
        payload.pop("decorators_json", None)
    return payload


def edge_label(key: Any) -> str:
    text = str(key)
    for prefix in ("symbol:", "module:", "file:", "symbol_ref:"):
        if text.startswith(prefix):
            return text.removeprefix(prefix)
    return text


def record_import(row: Any) -> str:
    module = str(row["module"])
    name = row["name"]
    alias = row["alias"]
    value = f"{module}.{name}" if name else module
    if alias:
        return f"{value} as {alias}"
    return value


def dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)
