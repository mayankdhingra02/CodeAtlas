from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import resolve_repo_root
from .evaluation import evaluate_dataset, write_report
from .models import (
    ContextSnippet,
    RetrievalResult,
    RetrievalTimings,
    TokenReport,
    estimate_tokens,
)
from .scanner import iter_source_files


_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "code",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "what",
        "where",
        "which",
        "with",
    }
)


@dataclass(frozen=True)
class _LexicalChunk:
    file_path: str
    line_start: int
    line_end: int
    code: str
    score: float
    matched_terms: tuple[str, ...]


class LexicalChunkRetriever:
    """Deterministic, index-free lexical baseline over overlapping file chunks."""

    def __init__(
        self,
        *,
        chunk_lines: int = 80,
        stride_lines: int = 40,
        max_chunks_per_file: int = 2,
    ) -> None:
        if chunk_lines <= 0:
            raise ValueError("chunk_lines must be positive")
        if stride_lines <= 0:
            raise ValueError("stride_lines must be positive")
        if max_chunks_per_file <= 0:
            raise ValueError("max_chunks_per_file must be positive")
        self.chunk_lines = chunk_lines
        self.stride_lines = stride_lines
        self.max_chunks_per_file = max_chunks_per_file

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

        started = time.perf_counter()
        repo_root = resolve_repo_root(repo_path)
        query_terms = lexical_terms(query)
        source_files = tuple(iter_source_files(repo_root))
        baseline_tokens = 0
        candidates: list[_LexicalChunk] = []

        for source_file in source_files:
            text = source_file.path.read_text(encoding="utf-8", errors="replace")
            baseline_tokens += estimate_tokens(text)
            candidates.extend(
                self._file_candidates(
                    source_file.relative_path,
                    text,
                    query,
                    query_terms,
                )
            )

        ranking_finished = time.perf_counter()
        ranked = sorted(
            candidates,
            key=lambda chunk: (
                -chunk.score,
                chunk.file_path,
                chunk.line_start,
                chunk.line_end,
            ),
        )
        snippets = self._select_with_budget(ranked, max_tokens=max_tokens)
        completed = time.perf_counter()
        optimized_tokens = sum(snippet.estimated_tokens for snippet in snippets)
        ranking_ms = (ranking_finished - started) * 1000
        total_ms = (completed - started) * 1000

        return RetrievalResult(
            query=query,
            snippets=snippets,
            token_report=TokenReport(
                baseline_tokens=baseline_tokens,
                optimized_tokens=optimized_tokens,
            ),
            timings=RetrievalTimings(
                symbol_lookup_ms=0.0,
                graph_traversal_ms=0.0,
                ranking_ms=ranking_ms,
                total_ms=total_ms,
            ),
        )

    def _file_candidates(
        self,
        relative_path: str,
        text: str,
        query: str,
        query_terms: tuple[str, ...],
    ) -> tuple[_LexicalChunk, ...]:
        if not query_terms:
            return ()
        lines = text.splitlines()
        if not lines:
            return ()

        path_terms = Counter(lexical_terms(relative_path))
        path_overlap = sum(path_terms[term] for term in query_terms)
        query_phrase = " ".join(_raw_terms(query))
        chunks: list[_LexicalChunk] = []

        for start_index in _window_starts(
            len(lines),
            chunk_lines=self.chunk_lines,
            stride_lines=self.stride_lines,
        ):
            end_index = min(start_index + self.chunk_lines, len(lines))
            chunk_text = "\n".join(lines[start_index:end_index])
            counts = Counter(lexical_terms(chunk_text))
            matched = tuple(term for term in query_terms if counts[term] > 0)
            if not matched and path_overlap == 0:
                continue

            coverage = len(set(matched)) / len(set(query_terms))
            frequency = sum(math.log1p(min(counts[term], 20)) for term in matched)
            definition_hits = sum(
                1
                for line in lines[start_index:end_index]
                if line.lstrip().startswith(("class ", "def ", "async def "))
                and any(term in lexical_terms(line) for term in query_terms)
            )
            normalized_chunk = " ".join(_raw_terms(chunk_text))
            phrase_bonus = 2.0 if query_phrase and query_phrase in normalized_chunk else 0.0
            score = (
                coverage * 8.0
                + frequency
                + path_overlap * 2.5
                + definition_hits * 1.5
                + phrase_bonus
            )
            chunks.append(
                _LexicalChunk(
                    file_path=relative_path,
                    line_start=start_index + 1,
                    line_end=end_index,
                    code=chunk_text,
                    score=score,
                    matched_terms=matched,
                )
            )

        return tuple(chunks)

    def _select_with_budget(
        self,
        ranked: Sequence[_LexicalChunk],
        *,
        max_tokens: int,
    ) -> tuple[ContextSnippet, ...]:
        snippets: list[ContextSnippet] = []
        chunks_per_file: dict[str, int] = defaultdict(int)
        used_tokens = 0

        for chunk in ranked:
            if chunks_per_file[chunk.file_path] >= self.max_chunks_per_file:
                continue
            remaining = max_tokens - used_tokens
            if remaining <= 0:
                break

            code = chunk.code
            line_end = chunk.line_end
            tokens = estimate_tokens(code)
            if tokens > remaining:
                if remaining < 32:
                    continue
                code = code[: remaining * 4]
                line_end = chunk.line_start + code.count("\n")
                tokens = estimate_tokens(code)
            if not code.strip() or tokens <= 0:
                continue

            chunks_per_file[chunk.file_path] += 1
            used_tokens += tokens
            snippets.append(
                ContextSnippet(
                    file_path=chunk.file_path,
                    symbol_name=f"{Path(chunk.file_path).name}:{chunk.line_start}-{line_end}",
                    qualified_name=chunk.file_path,
                    kind="FILE_CHUNK",
                    line_start=chunk.line_start,
                    line_end=line_end,
                    score=chunk.score,
                    reason=(
                        "lexical chunk matched: "
                        + ", ".join(chunk.matched_terms[:8])
                    ),
                    code=code,
                )
            )

        return tuple(snippets)


def lexical_terms(text: str) -> tuple[str, ...]:
    return tuple(
        term
        for raw in _raw_terms(text)
        if (term := _normalize_term(raw)) and term not in _STOP_WORDS
    )


def _raw_terms(text: str) -> tuple[str, ...]:
    expanded = _CAMEL_BOUNDARY.sub(" ", text).replace("_", " ").replace("-", " ")
    return tuple(match.group(0).lower() for match in _TOKEN_PATTERN.finditer(expanded))


def _normalize_term(term: str) -> str:
    value = term.lower()
    if len(value) > 5 and value.endswith("ing"):
        value = value[:-3]
    elif len(value) > 4 and value.endswith("ed"):
        value = value[:-2]
    elif len(value) > 4 and value.endswith("ies"):
        value = value[:-3] + "y"
    elif len(value) > 3 and value.endswith("s") and not value.endswith("ss"):
        value = value[:-1]
    return value


def _window_starts(
    line_count: int,
    *,
    chunk_lines: int,
    stride_lines: int,
) -> tuple[int, ...]:
    if line_count <= chunk_lines:
        return (0,)
    starts = list(range(0, line_count - chunk_lines + 1, stride_lines))
    final_start = max(0, line_count - chunk_lines)
    if not starts or starts[-1] != final_start:
        starts.append(final_start)
    return tuple(starts)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic lexical chunk retrieval baseline."
    )
    parser.add_argument("repo", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--chunk-lines", type=int, default=80)
    parser.add_argument("--stride-lines", type=int, default=40)
    parser.add_argument("--max-chunks-per-file", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    retriever = LexicalChunkRetriever(
        chunk_lines=args.chunk_lines,
        stride_lines=args.stride_lines,
        max_chunks_per_file=args.max_chunks_per_file,
    )
    report = evaluate_dataset(
        args.repo,
        args.dataset,
        retriever=retriever,
        strategy="lexical-chunk-v1",
    )
    if args.output:
        write_report(report, args.output)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.summary.errored_cases == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
