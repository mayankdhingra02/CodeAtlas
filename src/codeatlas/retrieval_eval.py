from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .indexer import RepositoryIndexer
from .retrieval import RetrievalEngine


@dataclass(frozen=True)
class RetrievalEvalRepository:
    id: str
    path: str
    git_url: str | None = None
    ref: str | None = None
    required: bool = False


@dataclass(frozen=True)
class RetrievalEvalCase:
    query: str
    expected_files: tuple[str, ...]
    expected_symbols: tuple[str, ...]
    k: int
    depth: int
    max_tokens: int
    description: str | None = None


@dataclass(frozen=True)
class RetrievalEvalSuite:
    id: str
    repository_id: str
    cases: tuple[RetrievalEvalCase, ...]
    k: int
    min_file_recall: float
    min_symbol_recall: float
    depth: int
    max_tokens: int


@dataclass(frozen=True)
class RetrievalEvalManifest:
    path: Path
    repositories: tuple[RetrievalEvalRepository, ...]
    suites: tuple[RetrievalEvalSuite, ...]

    def repository(self, repository_id: str) -> RetrievalEvalRepository:
        for repository in self.repositories:
            if repository.id == repository_id:
                return repository
        msg = f"Unknown retrieval eval repository: {repository_id}"
        raise ValueError(msg)


@dataclass(frozen=True)
class RetrievalEvalCaseResult:
    case: RetrievalEvalCase
    returned_files: tuple[str, ...]
    returned_symbols: tuple[str, ...]
    file_hits: tuple[str, ...]
    symbol_hits: tuple[str, ...]

    @property
    def missing_files(self) -> tuple[str, ...]:
        return tuple(file for file in self.case.expected_files if file not in self.file_hits)

    @property
    def missing_symbols(self) -> tuple[str, ...]:
        return tuple(
            symbol for symbol in self.case.expected_symbols if symbol not in self.symbol_hits
        )

    @property
    def passed(self) -> bool:
        return not self.missing_files and not self.missing_symbols


@dataclass(frozen=True)
class RetrievalEvalSuiteResult:
    suite: RetrievalEvalSuite
    repo_root: Path
    case_results: tuple[RetrievalEvalCaseResult, ...]

    @property
    def total_expected_files(self) -> int:
        return sum(len(result.case.expected_files) for result in self.case_results)

    @property
    def total_file_hits(self) -> int:
        return sum(len(result.file_hits) for result in self.case_results)

    @property
    def total_expected_symbols(self) -> int:
        return sum(len(result.case.expected_symbols) for result in self.case_results)

    @property
    def total_symbol_hits(self) -> int:
        return sum(len(result.symbol_hits) for result in self.case_results)

    @property
    def file_recall(self) -> float:
        if self.total_expected_files == 0:
            return 1.0
        return self.total_file_hits / self.total_expected_files

    @property
    def symbol_recall(self) -> float:
        if self.total_expected_symbols == 0:
            return 1.0
        return self.total_symbol_hits / self.total_expected_symbols

    @property
    def failed_cases(self) -> tuple[RetrievalEvalCaseResult, ...]:
        return tuple(result for result in self.case_results if not result.passed)

    @property
    def passed(self) -> bool:
        return (
            self.file_recall >= self.suite.min_file_recall
            and self.symbol_recall >= self.suite.min_symbol_recall
        )

    def failure_report(self) -> str:
        lines = [
            (
                f"{self.suite.id}: file recall@{self.suite.k} "
                f"{self.file_recall:.1%} "
                f"(min {self.suite.min_file_recall:.1%}), symbol recall@{self.suite.k} "
                f"{self.symbol_recall:.1%} (min {self.suite.min_symbol_recall:.1%})"
            )
        ]
        for result in self.failed_cases[:10]:
            lines.append(f"- {result.case.query!r}")
            if result.missing_files:
                lines.append(f"  missing files: {', '.join(result.missing_files)}")
            if result.missing_symbols:
                lines.append(f"  missing symbols: {', '.join(result.missing_symbols)}")
            lines.append(f"  returned files: {', '.join(result.returned_files[:8]) or '(none)'}")
            returned_symbols = ", ".join(result.returned_symbols[:8]) or "(none)"
            lines.append(f"  returned symbols: {returned_symbols}")
        return "\n".join(lines)


@dataclass(frozen=True)
class RetrievalEvalSkippedSuite:
    suite_id: str
    repository_id: str
    reason: str


@dataclass(frozen=True)
class RetrievalEvalRun:
    results: tuple[RetrievalEvalSuiteResult, ...]
    skipped: tuple[RetrievalEvalSkippedSuite, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    def failure_report(self) -> str:
        return "\n\n".join(result.failure_report() for result in self.results if not result.passed)


def load_retrieval_eval_manifest(path: str | Path) -> RetrievalEvalManifest:
    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(payload.get("version", 1)) != 1:
        msg = f"Unsupported retrieval eval manifest version: {payload.get('version')}"
        raise ValueError(msg)

    repositories = tuple(
        RetrievalEvalRepository(
            id=str(item["id"]),
            path=str(item["path"]),
            git_url=item.get("git_url"),
            ref=item.get("ref"),
            required=bool(item.get("required", False)),
        )
        for item in payload.get("repositories", ())
    )
    suites = tuple(_parse_suite(item) for item in payload.get("suites", ()))
    return RetrievalEvalManifest(path=manifest_path, repositories=repositories, suites=suites)


def evaluate_retrieval_manifest(
    manifest: RetrievalEvalManifest,
    *,
    repository_ids: set[str] | None = None,
    repo_overrides: dict[str, Path] | None = None,
    index: bool = True,
    skip_missing: bool = True,
    retrieval: RetrievalEngine | None = None,
    indexer: RepositoryIndexer | None = None,
) -> RetrievalEvalRun:
    selected_ids = set(repository_ids or ())
    overrides = repo_overrides or {}
    results: list[RetrievalEvalSuiteResult] = []
    skipped: list[RetrievalEvalSkippedSuite] = []
    for suite in manifest.suites:
        if selected_ids and suite.repository_id not in selected_ids:
            continue
        repository = manifest.repository(suite.repository_id)
        repo_root = overrides.get(repository.id) or resolve_manifest_repo_path(
            repository.path, manifest.path.parent
        )
        if not repo_root.exists():
            if skip_missing and not repository.required:
                skipped.append(
                    RetrievalEvalSkippedSuite(
                        suite_id=suite.id,
                        repository_id=repository.id,
                        reason=f"repository not found at {repo_root}",
                    )
                )
                continue
            msg = (
                f"Retrieval eval repository {repository.id!r} not found at {repo_root}. "
                "Clone or check out the pinned repository before running this suite."
            )
            raise FileNotFoundError(msg)
        results.append(
            evaluate_retrieval_suite(
                repo_root,
                suite,
                index=index,
                retrieval=retrieval,
                indexer=indexer,
            )
        )
    return RetrievalEvalRun(results=tuple(results), skipped=tuple(skipped))


def evaluate_retrieval_suite(
    repo_root: Path,
    suite: RetrievalEvalSuite,
    *,
    index: bool = True,
    retrieval: RetrievalEngine | None = None,
    indexer: RepositoryIndexer | None = None,
) -> RetrievalEvalSuiteResult:
    if index:
        (indexer or RepositoryIndexer()).index(repo_root, incremental=False)
    engine = retrieval or RetrievalEngine()
    case_results = tuple(_evaluate_case(repo_root, engine, case) for case in suite.cases)
    return RetrievalEvalSuiteResult(
        suite=suite,
        repo_root=repo_root,
        case_results=case_results,
    )


def resolve_manifest_repo_path(path: str, manifest_dir: Path) -> Path:
    expanded = Path(os.path.expandvars(path)).expanduser()
    if not expanded.is_absolute():
        expanded = manifest_dir / expanded
    return expanded.resolve()


def _parse_suite(payload: dict[str, Any]) -> RetrievalEvalSuite:
    default_k = int(payload.get("k", 10))
    default_depth = int(payload.get("depth", 2))
    default_max_tokens = int(payload.get("max_tokens", 8000))
    return RetrievalEvalSuite(
        id=str(payload["id"]),
        repository_id=str(payload["repository"]),
        k=default_k,
        min_file_recall=float(payload.get("min_file_recall", 1.0)),
        min_symbol_recall=float(payload.get("min_symbol_recall", 1.0)),
        depth=default_depth,
        max_tokens=default_max_tokens,
        cases=tuple(
            RetrievalEvalCase(
                query=str(item["query"]),
                expected_files=tuple(str(value) for value in item.get("expected_files", ())),
                expected_symbols=tuple(str(value) for value in item.get("expected_symbols", ())),
                k=int(item.get("k", default_k)),
                depth=int(item.get("depth", default_depth)),
                max_tokens=int(item.get("max_tokens", default_max_tokens)),
                description=item.get("description"),
            )
            for item in payload.get("queries", ())
        ),
    )


def _evaluate_case(
    repo_root: Path,
    engine: RetrievalEngine,
    case: RetrievalEvalCase,
) -> RetrievalEvalCaseResult:
    result = engine.retrieve(
        repo_root,
        case.query,
        depth=case.depth,
        max_tokens=case.max_tokens,
    )
    snippets = result.snippets[: case.k]
    returned_files = tuple(dict.fromkeys(snippet.file_path for snippet in snippets))
    returned_symbols = tuple(
        dict.fromkeys(
            symbol
            for snippet in snippets
            for symbol in (snippet.qualified_name, snippet.symbol_name)
            if symbol
        )
    )
    returned_file_set = set(returned_files)
    returned_symbol_set = set(returned_symbols)
    return RetrievalEvalCaseResult(
        case=case,
        returned_files=returned_files,
        returned_symbols=returned_symbols,
        file_hits=tuple(file for file in case.expected_files if file in returned_file_set),
        symbol_hits=tuple(
            symbol for symbol in case.expected_symbols if symbol in returned_symbol_set
        ),
    )
