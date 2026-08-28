from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .indexer import RepositoryIndexer
from .models import RetrievalResult
from .retrieval import RetrievalEngine


class Retriever(Protocol):
    """Small interface implemented by CodeAtlas and future retrieval baselines."""

    def retrieve(
        self,
        repo_path: str | Path,
        query: str,
        *,
        depth: int = 2,
        max_tokens: int = 8000,
    ) -> RetrievalResult: ...


@dataclass(frozen=True)
class RetrievalBenchmarkCase:
    case_id: str
    query: str
    expected_files: tuple[str, ...] = ()
    expected_symbols: tuple[str, ...] = ()
    max_tokens: int = 2000
    depth: int = 2
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, source: str) -> RetrievalBenchmarkCase:
        case_id = str(payload.get("id", "")).strip()
        query = str(payload.get("query", "")).strip()
        expected_files = _normalized_strings(payload.get("expected_files", ()), paths=True)
        expected_symbols = _normalized_strings(payload.get("expected_symbols", ()))
        max_tokens = int(payload.get("max_tokens", 2000))
        depth = int(payload.get("depth", 2))
        metadata = payload.get("metadata", {})

        if not case_id:
            raise ValueError(f"{source}: benchmark case is missing a non-empty 'id'")
        if not query:
            raise ValueError(f"{source}: benchmark case {case_id!r} is missing a query")
        if not expected_files and not expected_symbols:
            message = (
                f"{source}: benchmark case {case_id!r} must declare "
                "expected_files or expected_symbols"
            )
            raise ValueError(message)
        if max_tokens <= 0:
            raise ValueError(f"{source}: benchmark case {case_id!r} has max_tokens <= 0")
        if depth < 0:
            raise ValueError(f"{source}: benchmark case {case_id!r} has depth < 0")
        if not isinstance(metadata, dict):
            raise ValueError(f"{source}: benchmark case {case_id!r} metadata must be an object")

        return cls(
            case_id=case_id,
            query=query,
            expected_files=expected_files,
            expected_symbols=expected_symbols,
            max_tokens=max_tokens,
            depth=depth,
            metadata=dict(metadata),
        )


@dataclass(frozen=True)
class RetrievalCaseEvaluation:
    case_id: str
    query: str
    expected_files: tuple[str, ...]
    expected_symbols: tuple[str, ...]
    retrieved_files: tuple[str, ...]
    retrieved_symbols: tuple[str, ...]
    file_recall: float | None
    symbol_recall: float | None
    file_reciprocal_rank: float | None
    symbol_reciprocal_rank: float | None
    all_targets_found: bool
    baseline_tokens: int
    context_tokens: int
    token_savings_percent: float
    latency_ms: float
    error: str | None = None


@dataclass(frozen=True)
class RetrievalBenchmarkSummary:
    strategy: str
    total_cases: int
    completed_cases: int
    errored_cases: int
    completion_rate: float
    all_targets_rate: float
    mean_file_recall: float | None
    mean_symbol_recall: float | None
    mean_file_reciprocal_rank: float | None
    mean_symbol_reciprocal_rank: float | None
    mean_context_tokens: float
    median_context_tokens: float
    mean_token_savings_percent: float
    mean_latency_ms: float


@dataclass(frozen=True)
class RetrievalBenchmarkReport:
    strategy: str
    dataset: str
    repo_root: str
    summary: RetrievalBenchmarkSummary
    cases: tuple[RetrievalCaseEvaluation, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_benchmark_cases(path: str | Path) -> tuple[RetrievalBenchmarkCase, ...]:
    dataset_path = Path(path).expanduser().resolve()
    cases: list[RetrievalBenchmarkCase] = []
    seen_ids: set[str] = set()

    with dataset_path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            source = f"{dataset_path}:{line_number}"
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}: invalid JSON: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"{source}: each JSONL line must contain an object")
            case = RetrievalBenchmarkCase.from_mapping(payload, source=source)
            if case.case_id in seen_ids:
                raise ValueError(f"{source}: duplicate benchmark id {case.case_id!r}")
            seen_ids.add(case.case_id)
            cases.append(case)

    if not cases:
        raise ValueError(f"{dataset_path}: benchmark dataset is empty")
    return tuple(cases)


def evaluate_retriever(
    repo_path: str | Path,
    cases: Sequence[RetrievalBenchmarkCase],
    *,
    retriever: Retriever | None = None,
    strategy: str = "codeatlas-current",
    dataset: str = "in-memory",
) -> RetrievalBenchmarkReport:
    repo_root = Path(repo_path).expanduser().resolve()
    engine = retriever or RetrievalEngine()
    evaluations = tuple(_evaluate_case(repo_root, case, engine) for case in cases)
    summary = summarize_evaluations(evaluations, strategy=strategy)
    return RetrievalBenchmarkReport(
        strategy=strategy,
        dataset=dataset,
        repo_root=str(repo_root),
        summary=summary,
        cases=evaluations,
    )


def evaluate_dataset(
    repo_path: str | Path,
    dataset_path: str | Path,
    *,
    retriever: Retriever | None = None,
    strategy: str = "codeatlas-current",
) -> RetrievalBenchmarkReport:
    resolved_dataset = Path(dataset_path).expanduser().resolve()
    cases = load_benchmark_cases(resolved_dataset)
    return evaluate_retriever(
        repo_path,
        cases,
        retriever=retriever,
        strategy=strategy,
        dataset=str(resolved_dataset),
    )


def summarize_evaluations(
    evaluations: Sequence[RetrievalCaseEvaluation],
    *,
    strategy: str,
) -> RetrievalBenchmarkSummary:
    completed = [evaluation for evaluation in evaluations if evaluation.error is None]
    context_tokens = [evaluation.context_tokens for evaluation in completed]
    total_cases = len(evaluations)
    return RetrievalBenchmarkSummary(
        strategy=strategy,
        total_cases=total_cases,
        completed_cases=len(completed),
        errored_cases=total_cases - len(completed),
        completion_rate=_safe_ratio(len(completed), total_cases),
        all_targets_rate=_safe_ratio(
            sum(1 for evaluation in evaluations if evaluation.all_targets_found),
            total_cases,
        ),
        mean_file_recall=_mean_optional(
            evaluation.file_recall for evaluation in evaluations
        ),
        mean_symbol_recall=_mean_optional(
            evaluation.symbol_recall for evaluation in evaluations
        ),
        mean_file_reciprocal_rank=_mean_optional(
            evaluation.file_reciprocal_rank for evaluation in evaluations
        ),
        mean_symbol_reciprocal_rank=_mean_optional(
            evaluation.symbol_reciprocal_rank for evaluation in evaluations
        ),
        mean_context_tokens=statistics.fmean(context_tokens) if context_tokens else 0.0,
        median_context_tokens=float(statistics.median(context_tokens)) if context_tokens else 0.0,
        mean_token_savings_percent=(
            statistics.fmean(evaluation.token_savings_percent for evaluation in completed)
            if completed
            else 0.0
        ),
        mean_latency_ms=(
            statistics.fmean(evaluation.latency_ms for evaluation in completed)
            if completed
            else 0.0
        ),
    )


def write_report(report: RetrievalBenchmarkReport, output_path: str | Path) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _evaluate_case(
    repo_root: Path,
    case: RetrievalBenchmarkCase,
    retriever: Retriever,
) -> RetrievalCaseEvaluation:
    try:
        result = retriever.retrieve(
            repo_root,
            case.query,
            depth=case.depth,
            max_tokens=case.max_tokens,
        )
    except Exception as exc:
        return RetrievalCaseEvaluation(
            case_id=case.case_id,
            query=case.query,
            expected_files=case.expected_files,
            expected_symbols=case.expected_symbols,
            retrieved_files=(),
            retrieved_symbols=(),
            file_recall=0.0 if case.expected_files else None,
            symbol_recall=0.0 if case.expected_symbols else None,
            file_reciprocal_rank=0.0 if case.expected_files else None,
            symbol_reciprocal_rank=0.0 if case.expected_symbols else None,
            all_targets_found=False,
            baseline_tokens=0,
            context_tokens=0,
            token_savings_percent=0.0,
            latency_ms=0.0,
            error=f"{type(exc).__name__}: {exc}",
        )

    retrieved_files = _dedupe(
        _normalize_path(snippet.file_path) for snippet in result.snippets if snippet.file_path
    )
    retrieved_symbols = _dedupe(
        snippet.qualified_name for snippet in result.snippets if snippet.qualified_name
    )
    file_recall = _recall(case.expected_files, retrieved_files)
    symbol_recall = _recall(case.expected_symbols, retrieved_symbols)
    file_rr = _reciprocal_rank(case.expected_files, retrieved_files)
    symbol_rr = _reciprocal_rank(case.expected_symbols, retrieved_symbols)
    files_found = _all_expected_found(case.expected_files, retrieved_files)
    symbols_found = _all_expected_found(case.expected_symbols, retrieved_symbols)
    all_targets_found = files_found and symbols_found

    return RetrievalCaseEvaluation(
        case_id=case.case_id,
        query=case.query,
        expected_files=case.expected_files,
        expected_symbols=case.expected_symbols,
        retrieved_files=retrieved_files,
        retrieved_symbols=retrieved_symbols,
        file_recall=file_recall,
        symbol_recall=symbol_recall,
        file_reciprocal_rank=file_rr,
        symbol_reciprocal_rank=symbol_rr,
        all_targets_found=all_targets_found,
        baseline_tokens=result.token_report.baseline_tokens,
        context_tokens=result.token_report.optimized_tokens,
        token_savings_percent=result.token_report.savings_percent,
        latency_ms=result.timings.total_ms,
    )


def _normalized_strings(value: Any, *, paths: bool = False) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, Iterable) and not isinstance(value, Mapping):
        items = tuple(value)
    else:
        raise ValueError("expected a string or list of strings")
    normalized = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError("expected every benchmark target to be a string")
        text = item.strip()
        if not text:
            continue
        normalized.append(_normalize_path(text) if paths else text)
    return _dedupe(normalized)


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _recall(expected: Sequence[str], retrieved: Sequence[str]) -> float | None:
    if not expected:
        return None
    expected_set = set(expected)
    return len(expected_set.intersection(retrieved)) / len(expected_set)


def _reciprocal_rank(expected: Sequence[str], retrieved: Sequence[str]) -> float | None:
    if not expected:
        return None
    expected_set = set(expected)
    for rank, item in enumerate(retrieved, start=1):
        if item in expected_set:
            return 1.0 / rank
    return 0.0


def _all_expected_found(expected: Sequence[str], retrieved: Sequence[str]) -> bool:
    if not expected:
        return True
    return set(expected).issubset(retrieved)


def _mean_optional(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return statistics.fmean(present) if present else None


def _safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate CodeAtlas retrieval against a labeled JSONL dataset."
    )
    parser.add_argument("repo", type=Path, help="Repository to index and evaluate")
    parser.add_argument("dataset", type=Path, help="JSONL benchmark dataset")
    parser.add_argument("--strategy", default="codeatlas-current")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Rebuild the CodeAtlas index before running the benchmark",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.reindex:
        RepositoryIndexer().index(args.repo, incremental=False)
    report = evaluate_dataset(args.repo, args.dataset, strategy=args.strategy)
    rendered = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if args.output:
        write_report(report, args.output)
    print(rendered)
    return 0 if report.summary.errored_cases == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
