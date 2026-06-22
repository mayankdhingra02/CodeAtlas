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
            intent = detect_query_intent(query)

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
                intent=intent,
            )
            if not snippets:
                snippets = self._text_search_snippets(repo_root, store, query, max_tokens=max_tokens)
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
        intent: str,
    ) -> list[ContextSnippet]:
        query_lower = query.lower()
        terms = query_terms(query)
        exact_keys = {symbol_node_key(str(row["qualified_name"])) for row in matches}
        edge_bonus = self._edge_bonus(edges)
        edge_types = self._edge_types_by_key(edges)
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
                reasons.append("method definition")
            else:
                reasons.append(str(row["kind"]).lower() + " definition")
            file_path = str(row["file_path"]).lower()
            if terms and any(term in file_path for term in terms):
                score += 12
                reasons.append("file path matches query")
            if intent == "test" and is_test_path(file_path):
                score += 35
                reasons.append("test-focused query")
            if intent == "architecture" and is_doc_path(file_path):
                score += 20
                reasons.append("architecture/documentation path")
            score += edge_bonus.get(key, 0.0)
            if key in edge_bonus:
                related = ", ".join(sorted(edge_types.get(key, ())))
                reasons.append("graph neighbor" + (f" via {related}" if related else ""))
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

    def _text_search_snippets(
        self,
        repo_root: Path,
        store: GraphStore,
        query: str,
        *,
        max_tokens: int,
    ) -> list[ContextSnippet]:
        terms = query_terms(query)
        if not terms:
            return []
        intent = detect_query_intent(query)
        fts_rows = store.search_files(query, limit=30)
        fts_rank_by_path = {str(row["path"]): float(row["rank"]) for row in fts_rows}
        candidate_paths = list(fts_rank_by_path)
        if not candidate_paths:
            candidate_paths = [str(row["path"]) for row in store.file_rows()]
        ranked: list[tuple[float, ContextSnippet]] = []
        for relative_path in candidate_paths:
            path = repo_root / relative_path
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            haystack_path = relative_path.lower()
            path_hits = sum(1 for term in terms if term in haystack_path)
            best_line = 0
            best_hits = 0
            for index, line in enumerate(lines, start=1):
                lower = line.lower()
                hits = sum(1 for term in terms if term in lower)
                if hits > best_hits:
                    best_hits = hits
                    best_line = index
            if not path_hits and not best_hits:
                continue
            start = max(1, best_line - 6) if best_line else 1
            end = min(len(lines), (best_line + 8) if best_line else 28)
            code = "\n".join(lines[start - 1 : end])
            score = path_hits * 18 + best_hits * 12 + (4 if path_hits and best_hits else 0)
            if relative_path in fts_rank_by_path:
                score += 40 + min(20, abs(fts_rank_by_path[relative_path]))
            if intent == "test" and is_test_path(relative_path):
                score += 35
            if intent == "architecture" and is_doc_path(relative_path):
                score += 30
            reason_parts = []
            if relative_path in fts_rank_by_path:
                reason_parts.append("SQLite FTS match")
            if path_hits:
                reason_parts.append("file path contains query terms")
            if best_hits:
                reason_parts.append("file text contains query terms")
            if intent != "general":
                reason_parts.append("intent: " + intent)
            ranked.append(
                (
                    score,
                    ContextSnippet(
                        file_path=relative_path,
                        symbol_name=Path(relative_path).name,
                        qualified_name=relative_path,
                        kind="FILE",
                        line_start=start,
                        line_end=end,
                        score=score,
                        reason=", ".join(reason_parts) or "text search fallback",
                        code=code,
                    ),
                )
            )
        ranked.sort(key=lambda item: (-item[0], item[1].file_path))
        snippets: list[ContextSnippet] = []
        used_tokens = 0
        for _, snippet in ranked:
            tokens = snippet.estimated_tokens
            if snippets and used_tokens + tokens > max_tokens:
                continue
            if not snippets and tokens > max_tokens:
                snippet = ContextSnippet(
                    file_path=snippet.file_path,
                    symbol_name=snippet.symbol_name,
                    qualified_name=snippet.qualified_name,
                    kind=snippet.kind,
                    line_start=snippet.line_start,
                    line_end=snippet.line_end,
                    score=snippet.score,
                    reason=snippet.reason,
                    code=trim_to_tokens(snippet.code, max_tokens),
                )
                tokens = snippet.estimated_tokens
            snippets.append(snippet)
            used_tokens += tokens
            if len(snippets) >= 8:
                break
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

    def _edge_types_by_key(self, edges: list[Any]) -> dict[str, set[str]]:
        edge_types: dict[str, set[str]] = {}
        for edge in edges:
            edge_type = str(edge["edge_type"]).lower()
            edge_types.setdefault(str(edge["source_key"]), set()).add(edge_type)
            edge_types.setdefault(str(edge["target_key"]), set()).add(edge_type)
        return edge_types

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


def query_terms(query: str) -> tuple[str, ...]:
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
    for raw in query.replace("/", " ").replace(".", " ").replace("_", " ").split():
        term = "".join(char for char in raw.lower() if char.isalnum() or char in "-")
        if len(term) < 3 or term in stop_words:
            continue
        terms.append(term)
    return tuple(dict.fromkeys(terms))


def detect_query_intent(query: str) -> str:
    text = query.lower()
    if any(term in text for term in ("test", "spec", "coverage", "validate", "assert")):
        return "test"
    if any(term in text for term in ("bug", "fix", "error", "exception", "fail", "regression")):
        return "bug"
    if any(term in text for term in ("owner", "owns", "authored", "maintainer", "who changed")):
        return "ownership"
    if any(term in text for term in ("architecture", "design", "decision", "adr", "why")):
        return "architecture"
    if any(term in text for term in ("api", "request", "route", "endpoint", "flow")):
        return "api"
    if any(term in text for term in ("data", "database", "db", "sql", "model", "schema")):
        return "data"
    return "general"


def is_test_path(path: str) -> bool:
    lowered = path.lower()
    return any(part in lowered for part in ("test", "tests", "spec", "__tests__", ".spec.", ".test."))


def is_doc_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith("docs/") or any(part in lowered for part in ("readme", "adr", "design", "architecture", "rfcs"))


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
