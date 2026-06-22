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
    ROUTE = "ROUTE"


class EdgeType(StrEnum):
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"
    REFERENCES = "REFERENCES"
    DEFINES = "DEFINES"
    INHERITS = "INHERITS"
    HANDLES = "HANDLES"
    HTTP_CALLS = "HTTP_CALLS"


class MemoryEntityKind(StrEnum):
    REPOSITORY = "Repository"
    FILE = "File"
    SERVICE = "Service"
    MODULE = "Module"
    FEATURE = "Feature"
    DEVELOPER = "Developer"
    COMMIT = "Commit"
    PULL_REQUEST = "PullRequest"
    ARCHITECTURE_DECISION = "ArchitectureDecision"
    REPOSITORY_EVENT = "RepositoryEvent"
    INCIDENT = "Incident"
    RELEASE = "Release"


class MemoryRelationshipKind(StrEnum):
    INTRODUCED_BY = "introduced_by"
    MODIFIED_BY = "modified_by"
    REVIEWED_BY = "reviewed_by"
    CAUSED_BY = "caused_by"
    RELATED_TO = "related_to"
    SUPERSEDED_BY = "superseded_by"
    DEPENDS_ON = "depends_on"
    CONTRIBUTES_TO = "contributes_to"


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
    arguments: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class MemoryEvidence:
    id: int | None
    source_type: str
    source_id: str
    title: str
    snippet: str
    path: str | None = None
    author: str | None = None
    timestamp: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryEntity:
    key: str
    kind: MemoryEntityKind
    name: str
    summary: str
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryRelationship:
    source_key: str
    target_key: str
    relationship: MemoryRelationshipKind
    confidence: float
    evidence_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryIndexReport:
    repo_root: Path
    database_path: Path
    duration_seconds: float
    git_available: bool
    commits_indexed: int
    documents_indexed: int
    entities_indexed: int
    relationships_indexed: int
    evidence_indexed: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceRef:
    source_type: str
    source_id: str
    title: str
    snippet: str
    path: str | None = None
    author: str | None = None
    timestamp: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class HistoryEvent:
    date: str | None
    title: str
    summary: str
    entity_key: str
    entity_kind: str
    confidence: float
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class OwnershipEntry:
    developer: str
    email: str | None
    expertise_score: float
    commits: int
    files_touched: int
    last_active: str | None
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class DecisionAnswer:
    question: str
    answer: str
    confidence: float
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class ArchitectureFinding:
    topic: str
    summary: str
    confidence: float
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class CompressedContext:
    query: str
    architecture: tuple[ArchitectureFinding, ...]
    history: tuple[HistoryEvent, ...]
    design_decisions: tuple[DecisionAnswer, ...]
    ownership: tuple[OwnershipEntry, ...]
    dependencies: dict[str, Any]
    critical_files: tuple[str, ...]
    related_changes: tuple[str, ...]
    relevant_context: tuple[str, ...]
    estimated_tokens: int
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class CoChangeLink:
    file_path: str
    related_file_path: str
    commits: int
    confidence: float
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class HotspotEntry:
    component: str
    commits: int
    authors: int
    files: int
    risk_score: float
    last_changed: str | None
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class ImpactedFile:
    file_path: str
    status: str
    component: str
    risk: str
    reasons: tuple[str, ...]
    owners: tuple[OwnershipEntry, ...]
    related_files: tuple[CoChangeLink, ...]
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class ImpactReport:
    base_ref: str
    changed_files: tuple[str, ...]
    impacted_files: tuple[ImpactedFile, ...]
    related_commits: tuple[str, ...]
    token_report: TokenReport
    risk_level: str
    summary: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComponentSummary:
    component: str
    summary: str
    commits: int
    authors: tuple[str, ...]
    files: tuple[str, ...]
    related_files: tuple[CoChangeLink, ...]
    evidence: tuple[EvidenceRef, ...] = ()


def estimate_tokens(text: str) -> int:
    """Estimate tokens with the project-wide 1 token ~= 4 characters rule."""

    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
