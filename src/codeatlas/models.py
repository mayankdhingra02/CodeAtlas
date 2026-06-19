from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class NodeType(StrEnum):
    FILE = "FILE"
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    SYMBOL = "SYMBOL"


class EdgeType(StrEnum):
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"
    REFERENCES = "REFERENCES"
    DEFINES = "DEFINES"
    INHERITS = "INHERITS"


class SymbolKind(StrEnum):
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    language: str
    size_bytes: int
    mtime_ns: int
    sha256: str
    line_count: int


@dataclass(frozen=True)
class ImportRecord:
    module: str
    name: str | None
    alias: str | None
    line_number: int
    is_from: bool

    @property
    def display_name(self) -> str:
        if self.name:
            value = f"{self.module}.{self.name}" if self.module else self.name
        else:
            value = self.module
        if self.alias:
            return f"{value} as {self.alias}"
        return value


@dataclass(frozen=True)
class CallRecord:
    source_qualified_name: str
    target_name: str
    display_name: str
    line_number: int


@dataclass(frozen=True)
class InheritanceRecord:
    source_qualified_name: str
    target_name: str
    line_number: int


@dataclass(frozen=True)
class ReferenceRecord:
    source_qualified_name: str
    target_name: str
    line_number: int


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    qualified_name: str
    kind: SymbolKind
    module: str
    line_start: int
    line_end: int
    col_start: int
    col_end: int
    docstring: str | None = None
    decorators: tuple[str, ...] = ()
    signature: str | None = None
    parent_qualified_name: str | None = None

    @property
    def node_type(self) -> NodeType:
        if self.kind is SymbolKind.CLASS:
            return NodeType.CLASS
        if self.kind is SymbolKind.METHOD:
            return NodeType.METHOD
        return NodeType.FUNCTION

    @property
    def node_key(self) -> str:
        return f"symbol:{self.qualified_name}"


@dataclass(frozen=True)
class ParseResult:
    source_file: SourceFile
    module_name: str
    imports: tuple[ImportRecord, ...] = ()
    symbols: tuple[SymbolRecord, ...] = ()
    calls: tuple[CallRecord, ...] = ()
    inheritance: tuple[InheritanceRecord, ...] = ()
    references: tuple[ReferenceRecord, ...] = ()


@dataclass(frozen=True)
class IndexedFileResult:
    relative_path: str
    status: str
    symbols: int
    imports: int


@dataclass(frozen=True)
class IndexReport:
    repo_root: Path
    database_path: Path
    full_rebuild: bool
    duration_seconds: float
    files_scanned: int
    files_indexed: int
    files_skipped: int
    files_deleted: int
    symbols_indexed: int
    edges_indexed: int
    parser_errors: tuple[str, ...] = ()
    file_results: tuple[IndexedFileResult, ...] = ()


@dataclass(frozen=True)
class ContextSnippet:
    file_path: str
    symbol_name: str
    qualified_name: str
    kind: str
    line_start: int
    line_end: int
    score: float
    reason: str
    code: str

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.code)


@dataclass(frozen=True)
class TokenReport:
    baseline_tokens: int
    optimized_tokens: int

    @property
    def savings_percent(self) -> float:
        if self.baseline_tokens <= 0:
            return 0.0
        saved = max(self.baseline_tokens - self.optimized_tokens, 0)
        return (saved / self.baseline_tokens) * 100


@dataclass(frozen=True)
class RetrievalTimings:
    symbol_lookup_ms: float
    graph_traversal_ms: float
    ranking_ms: float
    total_ms: float


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    snippets: tuple[ContextSnippet, ...]
    token_report: TokenReport
    timings: RetrievalTimings


@dataclass(frozen=True)
class RepositoryStats:
    repo_root: Path
    database_path: Path
    files_indexed: int
    classes: int
    functions: int
    methods: int
    graph_nodes: int
    graph_edges: int
    imports: int
    index_size_bytes: int
    last_indexed_at: str | None
    supported_languages: tuple[str, ...]


@dataclass(frozen=True)
class DependencyExplanation:
    symbol_name: str
    callers: tuple[str, ...]
    callees: tuple[str, ...]
    imports: tuple[str, ...]
    inheritance: tuple[str, ...]
    references: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkReport:
    repo_root: Path
    indexing_time_seconds: float
    retrieval_latency_ms: float
    files_scanned: int
    files_returned: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    token_reduction_percent: float
    graph_traversal_ms: float
    retrieval_accuracy: str
    query: str
    extra: dict[str, Any] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    """Estimate tokens with the project-wide 1 token ~= 4 characters rule."""

    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
