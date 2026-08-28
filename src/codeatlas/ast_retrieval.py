from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .config import CodeAtlasPaths, resolve_repo_root
from .evaluation import evaluate_dataset, write_report
from .lexical_baseline import lexical_terms
from .models import (
    ContextSnippet,
    RetrievalResult,
    RetrievalTimings,
    TokenReport,
    estimate_tokens,
)
from .retrieval import read_line_range, trim_to_tokens
from .storage import GraphStore


_FIELD_WEIGHTS = {
    "name": 9.0,
    "qualified_name": 5.0,
    "signature": 5.0,
    "file_path": 3.5,
    "decorators": 2.5,
    "docstring": 2.5,
    "code": 1.0,
}


@dataclass(frozen=True)
class _IndexedSymbol:
    row: Any
    code: str
    fields: dict[str, Counter[str]]
    all_terms: frozenset[str]


@dataclass(frozen=True)
class _ScoredSymbol:
    symbol: _IndexedSymbol
    score: float
    matched_query_terms: tuple[str, ...]
    matched_name_terms: tuple[str, ...]


class AstSymbolRetriever:
    """Retrieve AST-delimited symbols without graph expansion or file FTS.

    This is the syntax-only Phase 0 condition. It intentionally keeps graph
    neighbors, embeddings, and file-level fallback out of the ranking so that
    later experiments can attribute gains to each component separately.
    """

    def __init__(self, *, max_symbols_per_file: int = 3) -> None:
        if max_symbols_per_file <= 0:
            raise ValueError("max_symbols_per_file must be positive")
        self.max_symbols_per_file = max_symbols_per_file

    def retrieve(
        self,
        repo_path: str | Path,
        query: str,
        *,
        depth: int = 2,
        max_tokens: int = 8000,
    ) -> RetrievalResult:
        del depth
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")

        total_start = time.perf_counter()
        repo_root = resolve_repo_root(repo_path)
        paths = CodeAtlasPaths(repo_root)
        if not paths.database_path.exists():
            raise FileNotFoundError(
                f"No CodeAtlas index found at {paths.database_path}. "
                f"Run `codeatlas index {repo_root}` first."
            )

        store = GraphStore(paths.database_path)
        store.initialize()
        try:
            lookup_start = time.perf_counter()
            rows = store.all_symbols()
            file_rows = store.file_rows()
            symbol_lookup_ms = _elapsed_ms(lookup_start)

            ranking_start = time.perf_counter()
            query_terms = lexical_terms(query)
            indexed_symbols = self._index_symbols(repo_root, rows)
            document_frequency = _document_frequency(indexed_symbols)
            scored = self._score_symbols(
                query,
                query_terms,
                indexed_symbols,
                document_frequency,
            )
            snippets = self._select_snippets(scored, max_tokens=max_tokens)
            ranking_ms = _elapsed_ms(ranking_start)

            baseline_tokens = sum(
                _tokens_for_bytes(int(row["size_bytes"])) for row in file_rows
            )
            optimized_tokens = sum(snippet.estimated_tokens for snippet in snippets)
            if baseline_tokens < optimized_tokens:
                baseline_tokens = optimized_tokens

            return RetrievalResult(
                query=query,
                snippets=snippets,
                token_report=TokenReport(
                    baseline_tokens=baseline_tokens,
                    optimized_tokens=optimized_tokens,
                ),
                timings=RetrievalTimings(
                    symbol_lookup_ms=symbol_lookup_ms,
                    graph_traversal_ms=0.0,
                    ranking_ms=ranking_ms,
                    total_ms=_elapsed_ms(total_start),
                ),
            )
        finally:
            store.close()

    def _index_symbols(
        self,
        repo_root: Path,
        rows: Sequence[Any],
    ) -> tuple[_IndexedSymbol, ...]:
        indexed: list[_IndexedSymbol] = []
        for row in rows:
            file_path = str(row["file_path"])
            code = read_line_range(
                repo_root / file_path,
                int(row["line_start"]),
                int(row["line_end"]),
            )
            decorators = _decorator_text(row["decorators_json"])
            fields = {
                "name": Counter(lexical_terms(str(row["name"]))),
                "qualified_name": Counter(lexical_terms(str(row["qualified_name"]))),
                "signature": Counter(lexical_terms(str(row["signature"] or ""))),
                "file_path": Counter(lexical_terms(file_path)),
                "decorators": Counter(lexical_terms(decorators)),
                "docstring": Counter(lexical_terms(str(row["docstring"] or ""))),
                "code": Counter(lexical_terms(code)),
            }
            all_terms = frozenset(
                term for field in fields.values() for term in field
            )
            indexed.append(
                _IndexedSymbol(
                    row=row,
                    code=code,
                    fields=fields,
                    all_terms=all_terms,
                )
            )
        return tuple(indexed)

    def _score_symbols(
        self,
        query: str,
        query_terms: tuple[str, ...],
        symbols: Sequence[_IndexedSymbol],
        document_frequency: Counter[str],
    ) -> tuple[_ScoredSymbol, ...]:
        if not query_terms or not symbols:
            return ()

        unique_query_terms = tuple(dict.fromkeys(query_terms))
        raw_query = query.strip().lower()
        symbol_count = len(symbols)
        scored: list[_ScoredSymbol] = []

        for symbol in symbols:
            score = 0.0
            matched_query_terms: list[str] = []
            matched_name_terms: list[str] = []

            for query_term in unique_query_terms:
                best_field_score = 0.0
                best_field: str | None = None
                for field_name, terms in symbol.fields.items():
                    similarity, matched_term = _best_term_match(query_term, terms)
                    if similarity <= 0.0 or matched_term is None:
                        continue
                    document_count = document_frequency.get(matched_term, 0)
                    inverse_frequency = math.log(
                        (symbol_count + 1) / (document_count + 1)
                    ) + 1.0
                    term_frequency = 1.0 + math.log1p(min(terms[matched_term], 20))
                    field_score = (
                        _FIELD_WEIGHTS[field_name]
                        * inverse_frequency
                        * term_frequency
                        * similarity
                    )
                    if field_score > best_field_score:
                        best_field_score = field_score
                        best_field = field_name
                if best_field is not None:
                    score += best_field_score
                    matched_query_terms.append(query_term)
                    if best_field == "name":
                        matched_name_terms.append(query_term)

            query_coverage = len(set(matched_query_terms)) / len(unique_query_terms)
            score += query_coverage * 12.0

            name_terms = tuple(symbol.fields["name"])
            if name_terms:
                name_coverage = sum(
                    1
                    for name_term in name_terms
                    if any(_terms_match(name_term, query_term) for query_term in unique_query_terms)
                ) / len(name_terms)
                score += name_coverage * 14.0

            name = str(symbol.row["name"]).lower()
            qualified_name = str(symbol.row["qualified_name"]).lower()
            if raw_query == name:
                score += 45.0
            elif raw_query == qualified_name:
                score += 50.0
            elif name and name in raw_query:
                score += 15.0

            if score > 0.0:
                scored.append(
                    _ScoredSymbol(
                        symbol=symbol,
                        score=score,
                        matched_query_terms=tuple(dict.fromkeys(matched_query_terms)),
                        matched_name_terms=tuple(dict.fromkeys(matched_name_terms)),
                    )
                )

        scored.sort(
            key=lambda candidate: (
                -candidate.score,
                str(candidate.symbol.row["file_path"]),
                int(candidate.symbol.row["line_start"]),
                str(candidate.symbol.row["qualified_name"]),
            )
        )
        return tuple(scored)

    def _select_snippets(
        self,
        scored: Sequence[_ScoredSymbol],
        *,
        max_tokens: int,
    ) -> tuple[ContextSnippet, ...]:
        selected: list[ContextSnippet] = []
        per_file: dict[str, int] = defaultdict(int)
        used_tokens = 0

        for candidate in scored:
            row = candidate.symbol.row
            file_path = str(row["file_path"])
            if per_file[file_path] >= self.max_symbols_per_file:
                continue
            remaining = max_tokens - used_tokens
            if remaining <= 0:
                break

            code = candidate.symbol.code
            if not code.strip():
                continue
            tokens = estimate_tokens(code)
            if tokens > remaining:
                if selected or remaining < 32:
                    continue
                code = trim_to_tokens(code, remaining)
                tokens = estimate_tokens(code)
            if tokens <= 0 or used_tokens + tokens > max_tokens:
                continue

            reasons = ["AST symbol"]
            if candidate.matched_name_terms:
                reasons.append(
                    "name matched " + ", ".join(candidate.matched_name_terms[:6])
                )
            elif candidate.matched_query_terms:
                reasons.append(
                    "content matched " + ", ".join(candidate.matched_query_terms[:6])
                )

            selected.append(
                ContextSnippet(
                    file_path=file_path,
                    symbol_name=str(row["name"]),
                    qualified_name=str(row["qualified_name"]),
                    kind=str(row["kind"]),
                    line_start=int(row["line_start"]),
                    line_end=int(row["line_end"]),
                    score=candidate.score,
                    reason="; ".join(reasons),
                    code=code,
                )
            )
            per_file[file_path] += 1
            used_tokens += tokens

        return tuple(selected)


def _document_frequency(symbols: Sequence[_IndexedSymbol]) -> Counter[str]:
    frequency: Counter[str] = Counter()
    for symbol in symbols:
        frequency.update(symbol.all_terms)
    return frequency


def _best_term_match(
    query_term: str,
    candidate_terms: Counter[str],
) -> tuple[float, str | None]:
    if query_term in candidate_terms:
        return 1.0, query_term

    best_similarity = 0.0
    best_term: str | None = None
    for candidate in candidate_terms:
        if not _terms_match(query_term, candidate):
            continue
        shared = _shared_prefix_length(query_term, candidate)
        similarity = 0.72 + min(shared, 8) * 0.02
        if similarity > best_similarity:
            best_similarity = similarity
            best_term = candidate
    return best_similarity, best_term


def _terms_match(left: str, right: str) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) < 4:
        return False
    shared = _shared_prefix_length(left, right)
    required = max(4, min(len(left), len(right)) - 2)
    return shared >= required


def _shared_prefix_length(left: str, right: str) -> int:
    shared = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        shared += 1
    return shared


def _decorator_text(raw_value: Any) -> str:
    try:
        value = json.loads(str(raw_value or "[]"))
    except json.JSONDecodeError:
        return str(raw_value or "")
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _tokens_for_bytes(size_bytes: int) -> int:
    if size_bytes <= 0:
        return 0
    return max(1, (size_bytes + 3) // 4)


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run syntax-only AST symbol retrieval against a labeled dataset."
    )
    parser.add_argument("repo", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-symbols-per-file", type=int, default=3)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    retriever = AstSymbolRetriever(
        max_symbols_per_file=args.max_symbols_per_file,
    )
    report = evaluate_dataset(
        args.repo,
        args.dataset,
        retriever=retriever,
        strategy="ast-symbol-v1",
    )
    if args.output:
        write_report(report, args.output)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.summary.errored_cases == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
