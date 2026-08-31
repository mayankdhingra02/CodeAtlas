from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from .config import CodeAtlasPaths, resolve_repo_root
from .models import EdgeType as RelationshipType
from .models import NodeType
from .storage import GraphStore

TraceRole = Literal[
    "route",
    "handler",
    "function",
    "method",
    "external_http",
    "unresolved",
]
TraceStatus = Literal["resolved", "external", "unresolved"]
TraceEdgeType = Literal["HANDLES", "CALLS", "HTTP_CALLS"]
OrderingBasis = Literal["source_order", "graph_path", "unknown"]

FLOW_TRACE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class TraceStep:
    id: str
    node_key: str
    label: str
    role: TraceRole
    file_path: str | None
    qualified_name: str | None
    line_start: int | None
    line_end: int | None
    signature: str | None
    status: TraceStatus
    is_sink: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node_key": self.node_key,
            "label": self.label,
            "role": self.role,
            "file_path": self.file_path,
            "qualified_name": self.qualified_name,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "signature": self.signature,
            "status": self.status,
            "is_sink": self.is_sink,
        }


@dataclass(frozen=True)
class TraceLink:
    edge_id: int
    source_step_id: str
    target_step_id: str
    source_node_key: str
    target_node_key: str
    edge_type: TraceEdgeType
    source_line: int | None
    source_lines: tuple[int, ...]
    arguments: tuple[str, ...]
    confidence: float
    resolution_tier: str
    display: str | None
    source_file_path: str | None
    target_file_path: str | None
    source_signature: str | None
    target_signature: str | None
    http_method: str | None
    http_target: str | None
    ordering_basis: OrderingBasis

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_step_id": self.source_step_id,
            "target_step_id": self.target_step_id,
            "source_node_key": self.source_node_key,
            "target_node_key": self.target_node_key,
            "edge_type": self.edge_type,
            "source_line": self.source_line,
            "source_lines": list(self.source_lines),
            "arguments": list(self.arguments),
            "confidence": self.confidence,
            "resolution_tier": self.resolution_tier,
            "display": self.display,
            "source_file_path": self.source_file_path,
            "target_file_path": self.target_file_path,
            "source_signature": self.source_signature,
            "target_signature": self.target_signature,
            "http_method": self.http_method,
            "http_target": self.http_target,
            "ordering_basis": self.ordering_basis,
        }


@dataclass(frozen=True)
class FlowTrace:
    schema_version: int
    entrypoint: str
    trace_kind: Literal["static"]
    ordering_basis: OrderingBasis
    steps: tuple[TraceStep, ...]
    links: tuple[TraceLink, ...]
    primary_path: tuple[str, ...]
    complete: bool
    gaps: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-safe payload used by every trace surface."""
        return {
            "schema_version": self.schema_version,
            "entrypoint": self.entrypoint,
            "trace_kind": self.trace_kind,
            "ordering_basis": self.ordering_basis,
            "steps": [step.to_dict() for step in self.steps],
            "links": [link.to_dict() for link in self.links],
            "primary_path": list(self.primary_path),
            "complete": self.complete,
            "gaps": list(self.gaps),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class _NodeRecord:
    key: str
    node_type: str
    label: str
    file_path: str | None
    evidence: dict[str, Any]
    qualified_name: str | None
    symbol_kind: str | None
    line_start: int | None
    line_end: int | None
    signature: str | None


def trace_flow(
    repo_path: str | Path,
    entrypoint: str,
    max_hops: int = 12,
) -> FlowTrace:
    """Build a directed, evidence-backed static trace from a persisted route node."""
    normalized_entrypoint = " ".join(entrypoint.strip().split())
    if not normalized_entrypoint:
        raise ValueError("entrypoint must not be empty")
    if max_hops < 1:
        raise ValueError("max_hops must be at least 1")

    repo_root = resolve_repo_root(repo_path)
    database_path = CodeAtlasPaths(repo_root).database_path
    if not database_path.exists():
        msg = (
            f"No CodeAtlas index found at {database_path}. "
            f"Run `codeatlas index {repo_root}` first."
        )
        raise FileNotFoundError(msg)

    store = GraphStore(database_path)
    try:
        store.initialize()
        return _trace_from_store(store, normalized_entrypoint, max_hops)
    finally:
        store.close()


def flow_trace_payload(trace: FlowTrace) -> dict[str, Any]:
    """Serialize a trace without exposing dataclass or tuple implementation details."""
    return trace.to_dict()


def _trace_from_store(store: GraphStore, entrypoint: str, max_hops: int) -> FlowTrace:
    route, resolution_warnings = _resolve_route(store, entrypoint)
    if route is None:
        return FlowTrace(
            schema_version=FLOW_TRACE_SCHEMA_VERSION,
            entrypoint=entrypoint,
            trace_kind="static",
            ordering_basis="unknown",
            steps=(),
            links=(),
            primary_path=(),
            complete=False,
            gaps=(f"Could not resolve route entrypoint {entrypoint!r} by label or path.",),
            warnings=resolution_warnings,
        )

    steps: dict[str, TraceStep] = {}
    node_records: dict[str, _NodeRecord] = {route.key: route}
    links: list[TraceLink] = []
    gaps: list[str] = []
    warnings = list(resolution_warnings)
    visited_relationship_ids: set[int] = set()
    visited_node_paths: set[tuple[str, tuple[str, ...]]] = set()
    expanded_keys: set[str] = set()
    queue: deque[tuple[str, tuple[str, ...], int]] = deque([(route.key, (route.key,), 0)])
    complete = True

    _ensure_step(steps, route)

    while queue:
        node_key, node_path, depth = queue.popleft()
        state = (node_key, node_path)
        if state in visited_node_paths:
            continue
        visited_node_paths.add(state)
        if node_key in expanded_keys:
            continue

        node = node_records[node_key]
        current_step = steps[_step_id(node.key)]
        if current_step.status != "resolved" or current_step.role == "external_http":
            continue

        relationship_types = _allowed_relationships(current_step)
        rows = (
            store.outgoing_edges(node_key, edge_types=relationship_types)
            if relationship_types
            else []
        )
        rows, companion_arguments, evidence_overrides = _semantic_link_rows(rows)

        if depth >= max_hops:
            if rows:
                complete = False
                _append_unique(
                    warnings,
                    f"Stopped tracing from {current_step.label} at max_hops={max_hops}.",
                )
            else:
                _mark_sink(steps, current_step.id)
            continue

        expanded_keys.add(node_key)
        if not rows:
            if current_step.role == "route":
                complete = False
                _append_unique(
                    gaps,
                    f"Route {current_step.label} has no persisted directed HANDLES edge.",
                )
            else:
                _mark_sink(steps, current_step.id)
            continue

        if len(rows) > 1:
            _append_unique(
                warnings,
                (
                    f"Static trace has {len(rows)} possible continuations from "
                    f"{current_step.label}; primary_path is ranked deterministically and does "
                    "not assert runtime order."
                ),
            )

        for row in rows:
            relationship_id = int(row["id"])
            if relationship_id in visited_relationship_ids:
                continue
            visited_relationship_ids.add(relationship_id)

            relationship_type = _trace_link_type(str(row["edge_type"]))
            target_key = str(row["target_key"])
            target = _load_node(store, target_key)
            if target is None:
                target = _unresolved_node(target_key)
            node_records[target.key] = target
            role_override: TraceRole | None = (
                "handler" if relationship_type == "HANDLES" else None
            )
            target_step = _ensure_step(steps, target, role_override=role_override)

            evidence = evidence_overrides.get(
                relationship_id,
                _decode_evidence(row["metadata_json"]),
            )
            arguments = _call_arguments(evidence)
            if not arguments:
                arguments = companion_arguments.get(relationship_id, ())
            source_step = steps[current_step.id]
            link = _make_link(
                row,
                evidence,
                source_step,
                target_step,
                arguments=arguments,
                target_evidence=target.evidence,
            )
            links.append(link)

            if target_step.status == "unresolved":
                complete = False
                _append_unique(gaps, _unresolved_gap(source_step, target_step, link.source_line))
                continue
            if target_step.is_sink:
                continue
            if target_key in node_path:
                complete = False
                _append_unique(
                    warnings,
                    f"Cycle detected at {target_step.label}; that branch was not expanded again.",
                )
                continue
            queue.append((target_key, (*node_path, target_key), depth + 1))

    primary_path = _select_primary_path(route.key, steps, links)
    if not primary_path and links:
        complete = False
        _append_unique(
            warnings,
            "No resolved sink-reaching primary path could be established from persisted edges.",
        )

    return FlowTrace(
        schema_version=FLOW_TRACE_SCHEMA_VERSION,
        entrypoint=entrypoint,
        trace_kind="static",
        ordering_basis="graph_path" if links else "unknown",
        steps=tuple(steps.values()),
        links=tuple(links),
        primary_path=primary_path,
        complete=complete and not gaps,
        gaps=tuple(gaps),
        warnings=tuple(warnings),
    )


def _resolve_route(
    store: GraphStore,
    entrypoint: str,
) -> tuple[_NodeRecord | None, tuple[str, ...]]:
    rows = store.connection.execute(
        """
        SELECT
          n.key, n.type, n.label, n.file_path, n.metadata_json,
          s.qualified_name, s.kind AS symbol_kind, s.line_start, s.line_end, s.signature
        FROM nodes n
        LEFT JOIN symbols s ON s.id = n.symbol_id
        WHERE n.type = ?
        ORDER BY n.label, n.key
        """,
        (NodeType.ROUTE.value,),
    ).fetchall()
    candidates: list[tuple[int, _NodeRecord]] = []
    entrypoint_folded = entrypoint.casefold()
    for row in rows:
        node = _node_record(row)
        if bool(node.evidence.get("external")):
            continue
        path = str(node.evidence.get("path") or "").strip()
        if node.key == entrypoint:
            candidates.append((0, node))
        elif node.label.casefold() == entrypoint_folded:
            candidates.append((1, node))
        elif path and path == entrypoint:
            candidates.append((2, node))
    if not candidates:
        return None, ()
    candidates.sort(key=lambda item: (item[0], item[1].label, item[1].key))
    best_score = candidates[0][0]
    best = [node for score, node in candidates if score == best_score]
    if len(best) == 1:
        return best[0], ()
    selected = best[0]
    warning = (
        f"Entrypoint {entrypoint!r} matched {len(best)} routes; selected "
        f"{selected.label} ({selected.key}) deterministically."
    )
    return selected, (warning,)


def _load_node(store: GraphStore, key: str) -> _NodeRecord | None:
    row = store.connection.execute(
        """
        SELECT
          n.key, n.type, n.label, n.file_path, n.metadata_json,
          s.qualified_name, s.kind AS symbol_kind, s.line_start, s.line_end, s.signature
        FROM nodes n
        LEFT JOIN symbols s ON s.id = n.symbol_id
        WHERE n.key = ?
        """,
        (key,),
    ).fetchone()
    return _node_record(row) if row is not None else None


def _node_record(row: sqlite3.Row) -> _NodeRecord:
    return _NodeRecord(
        key=str(row["key"]),
        node_type=str(row["type"]),
        label=str(row["label"]),
        file_path=str(row["file_path"]) if row["file_path"] is not None else None,
        evidence=_decode_evidence(row["metadata_json"]),
        qualified_name=(
            str(row["qualified_name"]) if row["qualified_name"] is not None else None
        ),
        symbol_kind=str(row["symbol_kind"]) if row["symbol_kind"] is not None else None,
        line_start=_optional_int(row["line_start"]),
        line_end=_optional_int(row["line_end"]),
        signature=str(row["signature"]) if row["signature"] is not None else None,
    )


def _unresolved_node(key: str) -> _NodeRecord:
    label = key.removeprefix("symbol_ref:") or key
    return _NodeRecord(
        key=key,
        node_type=NodeType.SYMBOL.value,
        label=label,
        file_path=None,
        evidence={"unresolved": True},
        qualified_name=None,
        symbol_kind=None,
        line_start=None,
        line_end=None,
        signature=None,
    )


def _ensure_step(
    steps: dict[str, TraceStep],
    node: _NodeRecord,
    *,
    role_override: TraceRole | None = None,
) -> TraceStep:
    step_id = _step_id(node.key)
    existing = steps.get(step_id)
    if existing is not None:
        if role_override == "handler" and existing.role not in {"handler", "external_http"}:
            existing = replace(existing, role="handler")
            steps[step_id] = existing
        return existing

    unresolved = node.key.startswith("symbol_ref:") or bool(node.evidence.get("unresolved"))
    external = node.node_type == NodeType.ROUTE.value and bool(node.evidence.get("external"))
    role = role_override or _node_role(node, unresolved=unresolved, external=external)
    status: TraceStatus = "unresolved" if unresolved else "external" if external else "resolved"
    step = TraceStep(
        id=step_id,
        node_key=node.key,
        label=node.label,
        role=role,
        file_path=node.file_path,
        qualified_name=node.qualified_name,
        line_start=node.line_start,
        line_end=node.line_end,
        signature=node.signature,
        status=status,
        is_sink=external,
    )
    steps[step_id] = step
    return step


def _node_role(node: _NodeRecord, *, unresolved: bool, external: bool) -> TraceRole:
    if unresolved:
        return "unresolved"
    if external:
        return "external_http"
    if node.node_type == NodeType.ROUTE.value:
        return "route"
    if node.node_type == NodeType.METHOD.value or node.symbol_kind == "METHOD":
        return "method"
    return "function"


def _allowed_relationships(step: TraceStep) -> tuple[str, ...]:
    if step.role == "route":
        return (RelationshipType.HANDLES.value,)
    if step.role in {"handler", "function", "method"}:
        return (RelationshipType.CALLS.value, RelationshipType.HTTP_CALLS.value)
    return ()


def _semantic_link_rows(
    rows: list[sqlite3.Row],
) -> tuple[
    list[sqlite3.Row],
    dict[int, tuple[str, ...]],
    dict[int, dict[str, Any]],
]:
    """Collapse the indexer's unresolved CALLS companion for a recognized HTTP call."""
    http_rows = [
        row for row in rows if str(row["edge_type"]) == RelationshipType.HTTP_CALLS.value
    ]
    call_rows = [
        row
        for row in rows
        if str(row["edge_type"]) == RelationshipType.CALLS.value
        and str(row["target_key"]).startswith("symbol_ref:")
    ]
    suppressed: set[int] = set()
    companion_arguments: dict[int, tuple[str, ...]] = {}
    evidence_overrides: dict[int, dict[str, Any]] = {}
    call_evidence = {
        int(row["id"]): _decode_evidence(row["metadata_json"])
        for row in call_rows
    }
    call_occurrences = {
        relationship_id: _edge_occurrences(evidence)
        for relationship_id, evidence in call_evidence.items()
    }
    matched_occurrences: dict[int, set[int]] = {
        relationship_id: set() for relationship_id in call_evidence
    }
    matched_occurrence_counts: dict[int, int] = {
        relationship_id: 0 for relationship_id in call_evidence
    }

    for http_row in http_rows:
        http_evidence = _decode_evidence(http_row["metadata_json"])
        http_occurrences = _edge_occurrences(http_evidence)
        matches_for_http_row: dict[int, int] = {}
        for http_occurrence in http_occurrences:
            for call_row in call_rows:
                relationship_id = int(call_row["id"])
                occurrence_index = _matching_occurrence_index(
                    http_occurrence,
                    call_occurrences[relationship_id],
                    matched_occurrences[relationship_id],
                )
                if occurrence_index is None:
                    continue
                matched_occurrences[relationship_id].add(occurrence_index)
                matches_for_http_row[relationship_id] = (
                    matches_for_http_row.get(relationship_id, 0) + 1
                )
                companion_arguments.setdefault(
                    int(http_row["id"]),
                    _call_arguments(call_occurrences[relationship_id][occurrence_index]),
                )
                break
        if len(matches_for_http_row) == 1:
            relationship_id, represented_matches = next(iter(matches_for_http_row.items()))
            matched_occurrence_counts[relationship_id] += max(
                represented_matches,
                _occurrence_count(http_evidence, len(http_occurrences)),
            )
        else:
            for relationship_id, represented_matches in matches_for_http_row.items():
                matched_occurrence_counts[relationship_id] += represented_matches

    for call_row in call_rows:
        relationship_id = int(call_row["id"])
        evidence = call_evidence[relationship_id]
        occurrences = call_occurrences[relationship_id]
        matched = matched_occurrences[relationship_id]
        occurrence_count = _occurrence_count(evidence, len(occurrences))
        if matched_occurrence_counts[relationship_id] >= occurrence_count:
            suppressed.add(relationship_id)
            continue
        unmatched = [
            occurrence
            for index, occurrence in enumerate(occurrences)
            if index not in matched
        ]
        if matched and unmatched:
            evidence_overrides[relationship_id] = _merge_occurrence_evidence(unmatched)

    semantic_rows = [row for row in rows if int(row["id"]) not in suppressed]
    semantic_rows.sort(
        key=lambda row: _link_sort_key(
            row,
            evidence_overrides.get(int(row["id"])),
        )
    )
    return semantic_rows, companion_arguments, evidence_overrides


def _edge_occurrences(evidence: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    examples = evidence.get("examples")
    if isinstance(examples, list):
        occurrences = tuple(dict(example) for example in examples if isinstance(example, dict))
        if occurrences:
            return occurrences
    return (dict(evidence),)


def _matching_occurrence_index(
    http_evidence: dict[str, Any],
    occurrences: tuple[dict[str, Any], ...],
    already_matched: set[int],
) -> int | None:
    candidates = [
        index
        for index, occurrence in enumerate(occurrences)
        if index not in already_matched and _same_occurrence(http_evidence, occurrence)
    ]
    if not candidates:
        return None
    http_target = _optional_text(http_evidence.get("target"))
    if http_target:
        for index in candidates:
            if _first_literal_argument(_call_arguments(occurrences[index])) == http_target:
                return index
    return candidates[0]


def _first_literal_argument(arguments: tuple[str, ...]) -> str | None:
    if not arguments:
        return None
    value = arguments[0].strip().lstrip("rubfRUBF")
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        return value[1:-1]
    if value.startswith(("http://", "https://", "/")):
        return value
    return None


def _occurrence_count(evidence: dict[str, Any], fallback: int) -> int:
    try:
        return max(1, int(evidence.get("count") or fallback))
    except (TypeError, ValueError):
        return max(1, fallback)


def _merge_occurrence_evidence(occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(occurrences[0])
    if len(occurrences) == 1:
        return merged
    lines = [
        line
        for occurrence in occurrences
        if (line := _optional_int(occurrence.get("line"))) is not None
    ]
    if lines:
        merged["line"] = lines[0]
        merged["lines"] = lines
    merged["count"] = len(occurrences)
    merged["examples"] = [dict(occurrence) for occurrence in occurrences]
    return merged


def _same_occurrence(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_line = _optional_int(left.get("line"))
    right_line = _optional_int(right.get("line"))
    if left_line is None or left_line != right_line:
        return False
    left_display = str(left.get("display") or "").strip()
    right_display = str(right.get("display") or "").strip()
    return bool(left_display and left_display == right_display)


def _link_sort_key(
    row: sqlite3.Row,
    evidence_override: dict[str, Any] | None = None,
) -> tuple[int, int, str, str, int]:
    evidence = evidence_override or _decode_evidence(row["metadata_json"])
    line = _optional_int(evidence.get("line"))
    return (
        0 if line is not None else 1,
        line or 0,
        str(row["edge_type"]),
        str(row["target_key"]),
        int(row["id"]),
    )


def _make_link(
    row: sqlite3.Row,
    evidence: dict[str, Any],
    source: TraceStep,
    target: TraceStep,
    *,
    arguments: tuple[str, ...],
    target_evidence: dict[str, Any],
) -> TraceLink:
    source_lines = _source_lines(evidence)
    source_line = source_lines[0] if source_lines else None
    confidence = _optional_float(evidence.get("confidence"))
    if confidence is None:
        confidence = float(row["weight"])
    relationship_type = _trace_link_type(str(row["edge_type"]))
    http_method = _optional_text(evidence.get("method"))
    http_target = _optional_text(evidence.get("target"))
    if relationship_type == "HTTP_CALLS":
        http_method = http_method or _optional_text(target_evidence.get("method"))
        http_target = http_target or _optional_text(target_evidence.get("target"))
    return TraceLink(
        edge_id=int(row["id"]),
        source_step_id=source.id,
        target_step_id=target.id,
        source_node_key=str(row["source_key"]),
        target_node_key=str(row["target_key"]),
        edge_type=relationship_type,
        source_line=source_line,
        source_lines=source_lines,
        arguments=arguments,
        confidence=confidence,
        resolution_tier=str(evidence.get("resolution_tier") or "unknown"),
        display=_optional_text(evidence.get("display") or evidence.get("label")),
        source_file_path=source.file_path,
        target_file_path=target.file_path,
        source_signature=source.signature,
        target_signature=target.signature,
        http_method=http_method,
        http_target=http_target,
        ordering_basis="source_order" if source_line is not None else "graph_path",
    )


def _select_primary_path(
    route_key: str,
    steps: dict[str, TraceStep],
    links: list[TraceLink],
) -> tuple[str, ...]:
    route_step_id = _step_id(route_key)
    adjacency: dict[str, list[TraceLink]] = {}
    for link in links:
        if steps[link.target_step_id].status == "unresolved":
            continue
        adjacency.setdefault(link.source_step_id, []).append(link)
    for outgoing in adjacency.values():
        outgoing.sort(key=_primary_link_rank)

    candidates: list[tuple[tuple[str, ...], tuple[TraceLink, ...]]] = []

    def visit(
        step_id: str,
        path: tuple[str, ...],
        path_links: tuple[TraceLink, ...],
    ) -> None:
        step = steps[step_id]
        if step.is_sink:
            candidates.append((path, path_links))
            return
        for link in adjacency.get(step_id, []):
            if link.target_step_id in path:
                continue
            visit(link.target_step_id, (*path, link.target_step_id), (*path_links, link))

    visit(route_step_id, (route_step_id,), ())
    if not candidates:
        return ()
    candidates.sort(key=lambda candidate: _candidate_path_rank(candidate, steps))
    return candidates[0][0]


def _candidate_path_rank(
    candidate: tuple[tuple[str, ...], tuple[TraceLink, ...]],
    steps: dict[str, TraceStep],
) -> tuple[int, tuple[tuple[int, int, str, str, int], ...], tuple[str, ...]]:
    path, path_links = candidate
    sink = steps[path[-1]]
    return (
        0 if sink.role == "external_http" else 1,
        tuple(_primary_link_rank(link) for link in path_links),
        path,
    )


def _primary_link_rank(link: TraceLink) -> tuple[int, int, str, str, int]:
    return (
        0 if link.source_line is not None else 1,
        link.source_line or 0,
        link.edge_type,
        link.target_node_key,
        link.edge_id,
    )


def _mark_sink(steps: dict[str, TraceStep], step_id: str) -> None:
    step = steps[step_id]
    if not step.is_sink:
        steps[step_id] = replace(step, is_sink=True)


def _unresolved_gap(source: TraceStep, target: TraceStep, line: int | None) -> str:
    caller = source.qualified_name or source.label
    location = source.file_path or "unknown file"
    if line is not None:
        location = f"{location}:{line}"
    return f"Could not resolve {target.label} called from {caller} at {location}."


def _decode_evidence(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_lines(evidence: dict[str, Any]) -> tuple[int, ...]:
    raw_lines: list[object] = []
    if "line" in evidence:
        raw_lines.append(evidence["line"])
    if isinstance(evidence.get("lines"), list):
        raw_lines.extend(evidence["lines"])
    lines: list[int] = []
    for raw_line in raw_lines:
        line = _optional_int(raw_line)
        if line is not None and line > 0 and line not in lines:
            lines.append(line)
    return tuple(lines)


def _call_arguments(evidence: dict[str, Any]) -> tuple[str, ...]:
    raw_arguments = evidence.get("arguments")
    if isinstance(raw_arguments, (list, tuple)):
        return tuple(str(argument) for argument in raw_arguments)
    examples = evidence.get("examples")
    if isinstance(examples, list):
        for example in examples:
            if isinstance(example, dict):
                arguments = example.get("arguments")
                if isinstance(arguments, (list, tuple)):
                    return tuple(str(argument) for argument in arguments)
    return ()


def _trace_link_type(value: str) -> TraceEdgeType:
    if value == RelationshipType.HANDLES.value:
        return "HANDLES"
    if value == RelationshipType.CALLS.value:
        return "CALLS"
    if value == RelationshipType.HTTP_CALLS.value:
        return "HTTP_CALLS"
    raise ValueError(f"Unsupported flow edge type: {value}")


def _step_id(node_key: str) -> str:
    return f"step:{node_key}"


def _optional_int(value: object) -> int | None:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted > 0 else None


def _optional_float(value: object) -> float | None:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
