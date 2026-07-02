from __future__ import annotations

import json
import re
import tomllib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .config import CodeAtlasPaths, resolve_repo_root
from .memory import MemoryStore, component_for_path, metadata_files, parse_json
from .storage import GraphStore


FLOW_KEYWORDS = {
    "api": ("api", "route", "routes", "wsgi", "rest", "controller", "endpoint", "http"),
    "service": ("service", "manager", "handler", "worker", "cmd", "main", "conductor", "scheduler"),
    "data": ("model", "models", "object", "objects", "db", "database", "schema", "migration", "store"),
    "integration": ("client", "driver", "virt", "volume", "network", "compute", "adapter", "plugin"),
    "tests": ("test", "tests", "spec", "fixtures", "fake", "mock"),
    "docs": ("doc", "docs", "readme", "guide", "config", "setup", "requirements"),
}
SCHEDULER_ORCHESTRATION_KEYWORDS = (
    "scheduler",
    "schedule",
    "conductor",
    "orchestrator",
    "orchestration",
    "workflow",
    "worker",
    "queue",
    "task",
    "job",
    "runner",
    "executor",
)
STARTUP_CONFIG_KEYWORDS = (
    "readme",
    "pyproject",
    "package",
    "setup",
    "requirements",
    "bindep",
    "tox",
    "pre-commit",
    "config",
    "settings",
    "conf",
    "env",
    "ini",
    "cfg",
    "toml",
    "yaml",
    "yml",
    "docker",
    "compose",
    "makefile",
    "manage",
    "wsgi",
    "asgi",
    "cmd",
    "main",
    "serve",
    "run",
    "scheduler",
    "conductor",
)
SELF_DESCRIPTION_FILENAMES = {
    "readme.md",
    "readme.rst",
    "readme.txt",
    "pyproject.toml",
    "package.json",
    "setup.cfg",
    "cargo.toml",
    "go.mod",
}
PURPOSE_VERBS = (
    " is ",
    " are ",
    " provides ",
    " provide ",
    " enables ",
    " enable ",
    " builds ",
    " build ",
    " indexes ",
    " index ",
    " turns ",
    " turn ",
    " helps ",
    " help ",
)
STOP_WORDS = {
    "app",
    "src",
    "lib",
    "test",
    "tests",
    "unit",
    "common",
    "utils",
    "base",
    "main",
    "core",
    "file",
    "files",
    "code",
    "module",
    "modules",
    "for",
    "from",
    "with",
    "this",
    "that",
    "into",
    "onto",
    "return",
    "returns",
    "value",
    "data",
}


def repo_briefing(repo_path: str | Path) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    paths = CodeAtlasPaths(repo_root)
    if not paths.database_path.exists():
        msg = f"No CodeAtlas index found at {paths.database_path}. Run `codeatlas index {repo_root}` first."
        raise FileNotFoundError(msg)

    graph_store = GraphStore(paths.database_path)
    memory_store = MemoryStore(paths.database_path)
    try:
        graph_store.initialize()
        memory_store.initialize()
        files = [dict(row) for row in graph_store.file_rows()]
        symbols = [dict(row) for row in graph_store.all_symbols()]
        code_edges = _code_edges(graph_store)
        commit_rows = memory_store.commit_evidence(limit=500)
        stats = graph_store.repository_stats()

        component_metrics = _component_metrics(files, symbols, commit_rows)
        component_edges = _component_edges(code_edges)
        identity = _repo_identity(repo_root, files, component_metrics, symbols)
        docs_files = _rank_files(files, categories=("docs",))[:12]
        test_files = _rank_files(files, categories=("tests",))[:12]
        api_nodes = _route_nodes(graph_store)
        top_components = _top_components(component_metrics, limit=12)
        recent_commits = _commit_payloads(commit_rows, limit=8)
        risky_components = _risky_components(component_metrics, component_edges, limit=8)
        start_here = _start_here(files, symbols, component_metrics, docs_files, api_nodes, identity)
        chapters = _chapters(
            files=files,
            symbols=symbols,
            component_metrics=component_metrics,
            component_edges=component_edges,
            docs_files=docs_files,
            test_files=test_files,
            api_nodes=api_nodes,
            recent_commits=recent_commits,
            risky_components=risky_components,
        )
        flows = _flows(
            top_components=top_components,
            component_metrics=component_metrics,
            component_edges=component_edges,
            symbols=symbols,
            api_nodes=api_nodes,
            docs_files=docs_files,
            test_files=test_files,
            recent_commits=recent_commits,
            risky_components=risky_components,
        )
        concepts = _concepts(component_metrics, symbols, files, identity)
        ignore_for_now = _ignore_for_now(graph_store, files, component_metrics)
        dashboard = _dashboard(stats, files, symbols, code_edges, commit_rows, component_metrics)
        summary = _summary(repo_root, stats, top_components, docs_files, api_nodes, recent_commits, identity)
        new_engineer_dashboard = _new_engineer_dashboard(
            start_here=start_here,
            flows=flows,
            chapters=chapters,
            ignore_for_now=ignore_for_now,
            summary=summary,
        )
        return {
            "repo": {"name": repo_root.name, "path": str(repo_root), "database": str(paths.database_path)},
            "identity": identity,
            "summary": summary,
            "dashboard": dashboard,
            "new_engineer_dashboard": new_engineer_dashboard,
            "start_here": start_here,
            "chapters": chapters,
            "flows": flows,
            "concepts": concepts,
            "ignore_for_now": ignore_for_now,
            "agent_brief": _agent_brief(repo_root, summary, start_here, chapters, flows),
        }
    finally:
        graph_store.close()
        memory_store.close()


def render_briefing_markdown(payload: dict[str, Any]) -> str:
    identity = payload.get("identity", {})
    summary = payload.get("summary", {})
    purpose = identity.get("purpose") or summary.get("headline", "")
    lines = [
        f"# CodeAtlas Repo Briefing: {payload.get('repo', {}).get('name', 'repository')}",
        "",
        "## Purpose",
        purpose,
        "",
        "## What this repo looks like",
    ]
    for bullet in summary.get("bullets", []):
        lines.append(f"- {bullet}")
    lines.extend(["", "## New engineer dashboard"])
    for section in payload.get("new_engineer_dashboard", {}).get("sections", []):
        lines.append(f"- {section.get('title', '')}: {section.get('summary', '')}")
    lines.extend(["", "## Start here"])
    for item in payload.get("start_here", [])[:8]:
        evidence = item.get("evidence", [{}])[0]
        location = evidence.get("path") or item.get("component") or ""
        lines.append(f"- {item.get('title', '')}: {item.get('reason', '')} ({location})")
    lines.extend(["", "## Guided chapters"])
    for chapter in payload.get("chapters", []):
        lines.append(f"- {chapter.get('title', '')}: {chapter.get('why', '')}")
    lines.extend(["", "## Important flows"])
    for flow in payload.get("flows", []):
        step_titles = " -> ".join(step.get("title", "") for step in flow.get("steps", [])[:5])
        lines.append(f"- {flow.get('title', '')}: {step_titles}")
    lines.extend(["", "## Ignore at first"])
    for item in payload.get("ignore_for_now", [])[:8]:
        lines.append(f"- {item.get('name', '')}: {item.get('reason', '')}")
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def _code_edges(store: GraphStore) -> list[dict[str, Any]]:
    rows = store.connection.execute(
        """
        SELECT
          e.edge_type,
          e.weight,
          e.metadata_json,
          s.key AS source_key,
          s.type AS source_type,
          s.label AS source_label,
          s.file_path AS source_path,
          ss.qualified_name AS source_qualified_name,
          ss.name AS source_symbol_name,
          ss.kind AS source_symbol_kind,
          ss.line_start AS source_line_start,
          t.key AS target_key,
          t.type AS target_type,
          t.label AS target_label,
          t.file_path AS target_path,
          ts.qualified_name AS target_qualified_name,
          ts.name AS target_symbol_name,
          ts.kind AS target_symbol_kind,
          ts.line_start AS target_line_start
        FROM edges e
        LEFT JOIN nodes s ON s.key = e.source_key
        LEFT JOIN nodes t ON t.key = e.target_key
        LEFT JOIN symbols ss ON ss.id = s.symbol_id
        LEFT JOIN symbols ts ON ts.id = t.symbol_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _component_metrics(
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    commit_rows: list[Any],
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = defaultdict(lambda: _empty_component())
    for row in files:
        path = str(row.get("path") or "")
        component = component_for_path(path)
        item = metrics[component]
        item["component"] = component
        item["files"] += 1
        item["lines"] += int(row.get("line_count") or 0)
        item["sample_files"].append(path)
        item["categories"].update(_path_categories(path))
    for row in symbols:
        file_path = str(row.get("file_path") or "")
        component = component_for_path(file_path)
        item = metrics[component]
        item["component"] = component
        item["symbols"] += 1
        kind = str(row.get("kind") or "").upper()
        if kind:
            item["symbol_kinds"][kind] += 1
        if len(item["sample_symbols"]) < 8:
            item["sample_symbols"].append(
                {
                    "name": str(row.get("name") or ""),
                    "qualified_name": str(row.get("qualified_name") or ""),
                    "kind": kind,
                    "path": file_path,
                    "line": int(row.get("line_start") or 0),
                }
            )
    for row in commit_rows:
        files_touched = metadata_files(row)
        for file_path in files_touched:
            component = component_for_path(file_path)
            item = metrics[component]
            item["component"] = component
            item["commits"] += 1
            timestamp = str(row["timestamp"] or "")
            if timestamp > str(item.get("last_changed") or ""):
                item["last_changed"] = timestamp
            if len(item["sample_commits"]) < 5:
                item["sample_commits"].append(
                    {
                        "sha": str(row["source_id"])[:12],
                        "title": str(row["title"] or ""),
                        "date": timestamp[:10],
                    }
                )
    for item in metrics.values():
        item["sample_files"] = item["sample_files"][:8]
        item["categories"] = sorted(item["categories"])
        item["symbol_kinds"] = dict(item["symbol_kinds"])
        item["score"] = (
            item["files"] * 4
            + item["symbols"]
            + item["commits"] * 2
            + min(item["lines"] // 250, 30)
        )
    return dict(metrics)


def _empty_component() -> dict[str, Any]:
    return {
        "component": "",
        "files": 0,
        "lines": 0,
        "symbols": 0,
        "commits": 0,
        "last_changed": "",
        "sample_files": [],
        "sample_symbols": [],
        "sample_commits": [],
        "symbol_kinds": Counter(),
        "categories": set(),
        "score": 0,
    }


def _component_edges(code_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in code_edges:
        source_path = str(row.get("source_path") or "")
        target_path = str(row.get("target_path") or "")
        edge_type = str(row.get("edge_type") or "").lower()
        if not source_path or not target_path:
            continue
        source = component_for_path(source_path) if source_path else _label_root(row.get("source_label"))
        target = component_for_path(target_path) if target_path else _label_root(row.get("target_label"))
        if not source or not target or source == target:
            continue
        key = (source, target, edge_type)
        item = grouped.setdefault(
            key,
            {
                "source": source,
                "target": target,
                "type": edge_type,
                "weight": 0,
                "examples": [],
                "confidence": 0.72,
            },
        )
        item["weight"] += 1
        if len(item["examples"]) < 5:
            metadata = _json(row.get("metadata_json"))
            source_name = row.get("source_qualified_name") or row.get("source_label") or row.get("source_key")
            target_name = (
                metadata.get("display")
                or row.get("target_qualified_name")
                or row.get("target_label")
                or row.get("target_key")
            )
            item["examples"].append(
                {
                    "kind": edge_type,
                    "title": f"{source_name} -> {target_name}",
                    "path": source_path or target_path,
                    "line": metadata.get("line") or row.get("source_line_start") or row.get("target_line_start"),
                    "detail": f"{source} {edge_type} {target}",
                    "confidence": 0.78 if target_path else 0.58,
                }
            )
    return sorted(grouped.values(), key=lambda item: (-int(item["weight"]), item["source"], item["target"]))


def _repo_identity(
    repo_root: Path,
    files: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
    symbols: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = _project_metadata(repo_root)
    documents = _repo_documents(repo_root, files, metadata)
    candidates = _purpose_candidates(repo_root, metadata, documents)
    code_candidate = _code_identity_candidate(repo_root, files, components, symbols)
    best = candidates[0] if candidates else code_candidate
    fallback = metadata.get("description") or f"{repo_root.name} is an indexed repository."
    purpose = str(best.get("text") or fallback).strip()
    confidence = float(best.get("confidence") or (0.62 if metadata.get("description") else 0.42))
    evidence = list(best.get("evidence_items") or [])
    if not evidence and best.get("evidence"):
        evidence = [best.get("evidence")]
    for doc in documents:
        if len(evidence) >= 4:
            break
        doc_evidence = doc.get("evidence")
        if doc_evidence and doc_evidence not in evidence:
            evidence.append(doc_evidence)
    return {
        "name": metadata.get("name") or repo_root.name,
        "title": _first_non_empty([doc.get("title") for doc in documents]) or metadata.get("name") or repo_root.name,
        "purpose": purpose,
        "confidence": round(confidence, 2),
        "evidence": evidence,
        "source": best.get("source") or (evidence[0].get("path") if evidence else ""),
        "basis": best.get("basis") or ("docs" if documents or metadata.get("description") else "code"),
        "has_self_description": bool(documents or metadata.get("description")),
        "documents": [
            {
                "path": doc["path"],
                "title": doc.get("title", ""),
                "summary": doc.get("summary", ""),
                "evidence": doc.get("evidence"),
                "score": doc.get("score", 0),
            }
            for doc in documents[:8]
        ],
        "domain_terms": _domain_terms_from_identity(purpose, documents, code_candidate.get("terms", [])),
        "metadata": {key: value for key, value in metadata.items() if key in {"name", "description", "readme"}},
    }


def _project_metadata(repo_root: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        try:
            payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            payload = {}
        project = payload.get("project") if isinstance(payload, dict) else {}
        if isinstance(project, dict):
            metadata.update(
                {
                    "name": str(project.get("name") or ""),
                    "description": str(project.get("description") or ""),
                    "readme": str(project.get("readme") or ""),
                    "metadata_path": "pyproject.toml",
                }
            )
    package_json = repo_root / "package.json"
    if package_json.exists() and not metadata.get("description"):
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            metadata.update(
                {
                    "name": str(payload.get("name") or metadata.get("name") or ""),
                    "description": str(payload.get("description") or ""),
                    "metadata_path": "package.json",
                }
            )
    return {key: value for key, value in metadata.items() if value}


def _repo_documents(
    repo_root: Path,
    files: list[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    candidates: dict[str, float] = {}
    for filename in SELF_DESCRIPTION_FILENAMES:
        relative_path = _root_entry_name(repo_root, filename)
        path = repo_root / relative_path
        if path.exists():
            candidates[relative_path] = max(candidates.get(relative_path, 0), _document_score(relative_path) + 30)
    readme = str(metadata.get("readme") or "")
    if readme and not readme.startswith("{"):
        candidates[readme] = max(candidates.get(readme, 0), _document_score(readme) + 40)
    for row in files:
        path = str(row.get("path") or "")
        if _looks_like_self_description(path):
            candidates[path] = max(candidates.get(path, 0), _document_score(path))
    docs = []
    for relative_path, score in sorted(candidates.items(), key=lambda item: (-item[1], item[0]))[: limit * 2]:
        text = _read_repo_text(repo_root, relative_path)
        if not text:
            continue
        title = _first_heading(text) or Path(relative_path).name
        summary, line = _document_summary(text)
        docs.append(
            {
                "path": relative_path,
                "title": title,
                "summary": summary,
                "text": text,
                "score": score,
                "evidence": _text_evidence(
                    "document",
                    title,
                    relative_path,
                    line,
                    summary or title,
                    0.84 if Path(relative_path).name.lower().startswith("readme") else 0.72,
                ),
            }
        )
        if len(docs) >= limit:
            break
    return docs


def _root_entry_name(repo_root: Path, filename: str) -> str:
    try:
        for child in repo_root.iterdir():
            if child.name.lower() == filename.lower():
                return child.name
    except OSError:
        pass
    return filename


def _purpose_candidates(
    repo_root: Path,
    metadata: dict[str, Any],
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    description = str(metadata.get("description") or "").strip()
    if description:
        path = str(metadata.get("metadata_path") or "project metadata")
        candidates.append(
            {
                "text": description,
                "confidence": 0.82,
                "source": path,
                "basis": "docs",
                "score": 82 + _purpose_sentence_score(repo_root.name, description),
                "evidence": _text_evidence("metadata", "Project description", path, 1, description, 0.82),
            }
        )
    for doc in documents:
        text = str(doc.get("text") or "")
        for sentence, line in _candidate_sentences(text):
            score = _purpose_sentence_score(repo_root.name, sentence) + int(doc.get("score") or 0)
            if score < 12:
                continue
            candidates.append(
                {
                    "text": sentence,
                    "confidence": min(0.92, 0.58 + score / 100),
                    "source": doc["path"],
                    "basis": "docs",
                    "score": score,
                    "evidence": _text_evidence("document", doc.get("title") or doc["path"], doc["path"], line, sentence, min(0.92, 0.58 + score / 100)),
                }
            )
    return sorted(candidates, key=lambda item: (-float(item["score"]), str(item["source"])))


def _code_identity_candidate(
    repo_root: Path,
    files: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
    symbols: list[dict[str, Any]],
) -> dict[str, Any]:
    top_components = _top_components(components, limit=5)
    entry_symbols = _entry_symbols(symbols)[:4]
    code_files = _high_signal_code_files(files)[:4]
    terms = _domain_terms_from_code(top_components, entry_symbols, code_files)
    component_names = ", ".join(item["component"] for item in top_components[:4])
    descriptor = _codebase_descriptor(files, top_components, symbols)
    focus = component_names or ", ".join(item["path"] for item in code_files[:3]) or "indexed source files"
    text = (
        f"No README or project description was found. CodeAtlas inferred from code structure that "
        f"{repo_root.name} is a {descriptor} organized around {focus}."
    )
    evidence_items = []
    evidence_items.extend(_component_evidence(item, "Code-inferred purpose evidence") for item in top_components[:3])
    evidence_items.extend(_symbol_identity_evidence(item) for item in entry_symbols[:2])
    evidence_items.extend(_file_evidence(item, "High-signal code file") for item in code_files[:2])
    evidence_items = [item for item in evidence_items if item.get("title") or item.get("path")]
    confidence = 0.58
    if top_components:
        confidence += 0.08
    if entry_symbols:
        confidence += 0.04
    if code_files:
        confidence += 0.02
    return {
        "text": text,
        "confidence": min(confidence, 0.72),
        "source": "code structure",
        "basis": "code",
        "score": 35 + len(top_components) * 4 + len(entry_symbols) * 2,
        "evidence": evidence_items[0] if evidence_items else {},
        "evidence_items": evidence_items[:5],
        "terms": terms,
    }


def _high_signal_code_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for row in files:
        path = str(row.get("path") or "")
        categories = _path_categories(path)
        if "tests" in categories or _component_is_noise(component_for_path(path)):
            continue
        score = _file_start_score(path) + int(row.get("line_count") or 0) / 180
        if "docs" in categories:
            score -= 35
        if {"api", "service", "data", "integration"} & categories:
            score += 24
        items.append({**_file_payload(row), "score": round(score, 2), "categories": sorted(categories)})
    return sorted(items, key=lambda item: (-float(item.get("score") or 0), item["path"]))


def _codebase_descriptor(
    files: list[dict[str, Any]],
    top_components: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
) -> str:
    categories: Counter[str] = Counter()
    for row in files:
        categories.update(_path_categories(str(row.get("path") or "")))
    component_text = " ".join(item.get("component", "") for item in top_components).lower()
    symbol_text = " ".join(str(row.get("qualified_name") or row.get("name") or "") for row in symbols[:200]).lower()
    text = " ".join([component_text, symbol_text])
    if categories["api"] and (categories["service"] or "service" in text):
        return "service/backend codebase"
    if categories["api"]:
        return "API-facing codebase"
    if categories["data"] and categories["service"]:
        return "application with service and data/model layers"
    if categories["data"]:
        return "data/model-heavy codebase"
    if categories["integration"]:
        return "integration-heavy codebase"
    if categories["tests"] > categories["runtime"]:
        return "test-heavy codebase"
    return "codebase"


def _symbol_identity_evidence(symbol: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "symbol",
        "title": str(symbol.get("qualified_name") or symbol.get("name") or ""),
        "path": str(symbol.get("file_path") or ""),
        "line": int(symbol.get("line_start") or 1),
        "detail": str(symbol.get("signature") or symbol.get("kind") or "Entry-shaped symbol"),
        "confidence": 0.64,
    }


def _domain_terms_from_code(
    top_components: list[dict[str, Any]],
    entry_symbols: list[dict[str, Any]],
    code_files: list[dict[str, Any]],
) -> list[str]:
    counts: Counter[str] = Counter()
    for component in top_components:
        for token in _tokens(str(component.get("component") or "")):
            counts[token] += 6
        for path in component.get("sample_files", [])[:4]:
            for token in _tokens(str(path)):
                counts[token] += 2
    for symbol in entry_symbols:
        for token in _tokens(str(symbol.get("qualified_name") or symbol.get("name") or "")):
            counts[token] += 4
    for file_item in code_files:
        for token in _tokens(str(file_item.get("path") or "")):
            counts[token] += 2
    return [term for term, _ in counts.most_common(12) if term not in STOP_WORDS]


def _candidate_sentences(text: str) -> list[tuple[str, int]]:
    paragraphs = _meaningful_paragraphs(text, max_paragraphs=5)
    candidates = []
    for paragraph, line in paragraphs:
        for sentence in _split_sentences(paragraph):
            clean = _clean_sentence(sentence)
            if 35 <= len(clean) <= 360:
                candidates.append((clean, line))
    return candidates


def _meaningful_paragraphs(text: str, *, max_paragraphs: int) -> list[tuple[str, int]]:
    paragraphs: list[tuple[str, int]] = []
    current: list[str] = []
    start_line = 1
    in_code = False
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            in_code = not in_code
            continue
        if in_code or _skip_doc_line(line):
            if current:
                paragraphs.append((" ".join(current), start_line))
                current = []
                if len(paragraphs) >= max_paragraphs:
                    break
            continue
        cleaned = _clean_doc_line(line)
        if not cleaned:
            if current:
                paragraphs.append((" ".join(current), start_line))
                current = []
                if len(paragraphs) >= max_paragraphs:
                    break
            continue
        if not current:
            start_line = line_number
        current.append(cleaned)
    if current and len(paragraphs) < max_paragraphs:
        paragraphs.append((" ".join(current), start_line))
    return paragraphs[:max_paragraphs]


def _document_summary(text: str) -> tuple[str, int]:
    paragraphs = _meaningful_paragraphs(text, max_paragraphs=3)
    if paragraphs:
        paragraph, line = paragraphs[0]
        sentences = _split_sentences(paragraph)
        return (_clean_sentence(sentences[0]) if sentences else _clean_sentence(paragraph), line)
    title = _first_heading(text)
    return title, 1


def _first_heading(text: str) -> str:
    for line in text.splitlines()[:80]:
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if stripped and not _skip_doc_line(stripped):
            return _clean_doc_line(stripped)
    return ""


def _read_repo_text(repo_root: Path, relative_path: str, *, max_chars: int = 24000) -> str:
    try:
        root = repo_root.resolve()
        path = (repo_root / relative_path).resolve()
        if root != path and root not in path.parents:
            return ""
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _looks_like_self_description(path: str) -> bool:
    lower = path.lower()
    name = Path(lower).name
    if name in SELF_DESCRIPTION_FILENAMES:
        return True
    if name.startswith(("readme", "overview", "architecture", "getting-started", "index")) and name.endswith((".md", ".rst", ".txt")):
        return True
    return lower.startswith(("docs/", "doc/")) and name.endswith((".md", ".rst", ".txt"))


def _document_score(path: str) -> float:
    lower = path.lower()
    name = Path(lower).name
    score = 0.0
    if name.startswith("readme"):
        score += 100
    if name in {"pyproject.toml", "package.json", "setup.cfg"}:
        score += 80
    if "overview" in lower or "getting-started" in lower or "intro" in lower:
        score += 55
    if "architecture" in lower:
        score += 48
    if lower.startswith(("docs/", "doc/")):
        score += 25
    return score


def _purpose_sentence_score(repo_name: str, sentence: str) -> int:
    lower = " " + sentence.lower() + " "
    score = 0
    repo_tokens = [token for token in _tokens(repo_name) if token not in STOP_WORDS]
    if any(token in lower for token in repo_tokens):
        score += 24
    if any(verb in lower for verb in PURPOSE_VERBS):
        score += 22
    if any(word in lower for word in ("service", "platform", "tool", "library", "framework", "application", "assistant", "server", "engine")):
        score += 14
    if any(word in lower for word in ("repository", "compute", "authentication", "payment", "index", "graph", "retrieval", "workflow")):
        score += 10
    if "://" in lower or lower.startswith("!["):
        score -= 30
    return score


def _domain_terms_from_identity(
    purpose: str,
    documents: list[dict[str, Any]],
    code_terms: list[str] | None = None,
) -> list[str]:
    counts: Counter[str] = Counter()
    for token in _tokens(purpose):
        counts[token] += 8
    for doc in documents[:4]:
        for token in _tokens(" ".join([str(doc.get("title") or ""), str(doc.get("summary") or "")])):
            counts[token] += 3
    for token in code_terms or []:
        counts[token] += 4
    return [term for term, _ in counts.most_common(12) if term not in STOP_WORDS]


def _split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`])", normalized)
    return [part.strip() for part in parts if part.strip()]


def _clean_sentence(text: str) -> str:
    clean = _clean_doc_line(text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _skip_doc_line(line: str) -> bool:
    if not line:
        return False
    lower = line.lower()
    return (
        line.startswith(("#", ">", "|", "-", "* ", "+ ", "```", "~~~", "![", "[!"))
        or lower.startswith(("<!--", ".. image::", ".. badge::"))
        or "badge.svg" in lower
    )


def _clean_doc_line(line: str) -> str:
    clean = line.strip()
    clean = clean.lstrip("#").strip()
    clean = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", clean)
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
    clean = clean.replace("`", "")
    clean = clean.strip(" -*")
    return clean.strip()


def _route_nodes(store: GraphStore) -> list[dict[str, Any]]:
    rows = store.connection.execute(
        """
        SELECT n.key, n.label, n.file_path, n.metadata_json
        FROM nodes n
        WHERE n.type = 'ROUTE'
        ORDER BY n.file_path, n.label
        LIMIT 60
        """
    ).fetchall()
    nodes = []
    for row in rows:
        metadata = _json(row["metadata_json"])
        label = str(row["label"])
        route_path = str(metadata.get("path") or label)
        if "/" not in route_path:
            continue
        nodes.append(
            {
                "key": str(row["key"]),
                "title": label,
                "path": str(row["file_path"] or metadata.get("path") or ""),
                "line": metadata.get("line"),
                "detail": str(metadata.get("path") or metadata.get("method") or "route"),
                "confidence": 0.84,
            }
        )
    return nodes


def _rank_files(files: list[dict[str, Any]], *, categories: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    wanted = set(categories)
    items = []
    for row in files:
        path = str(row.get("path") or "")
        file_categories = _path_categories(path)
        if wanted and not (wanted & file_categories):
            continue
        score = _file_start_score(path) + int(row.get("line_count") or 0) / 120
        items.append(
            {
                "path": path,
                "component": component_for_path(path),
                "language": str(row.get("language") or ""),
                "lines": int(row.get("line_count") or 0),
                "categories": sorted(file_categories),
                "score": round(score, 2),
            }
        )
    return sorted(items, key=lambda item: (-float(item["score"]), item["path"]))


def _start_here(
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
    docs_files: list[dict[str, Any]],
    api_nodes: list[dict[str, Any]],
    identity: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    has_self_description = bool(identity.get("has_self_description"))
    for doc in identity.get("documents", [])[:3]:
        path = str(doc.get("path") or "")
        if not path:
            continue
        evidence = doc.get("evidence") or _text_evidence(
            "document",
            doc.get("title") or path,
            path,
            1,
            doc.get("summary") or "Repository self-description document",
            0.74,
        )
        items.append(
            {
                "title": path,
                "kind": "document",
                "reason": "Repository self-description from docs or project metadata.",
                "component": component_for_path(path),
                "confidence": max(0.76, float(evidence.get("confidence") or 0.0)),
                "evidence": [evidence],
            }
        )
    for file_item in docs_files[:3]:
        items.append(
            {
                "title": file_item["path"],
                "kind": "file",
                "reason": "Orientation file with repository-level docs/config context.",
                "component": file_item["component"],
                "confidence": 0.82,
                "evidence": [_file_evidence(file_item, "Indexed orientation file")],
            }
        )
    for component in _top_components(components, limit=5):
        evidence = _component_evidence(component, "Largest/highest-signal component by indexed files, symbols, and commits")
        items.append(
            {
                "title": component["component"],
                "kind": "component",
                "reason": (
                    "Major component worth reading after the docs."
                    if has_self_description or docs_files
                    else "Major component worth reading first because no README/docs purpose was found."
                ),
                "component": component["component"],
                "confidence": 0.76,
                "evidence": [evidence],
            }
        )
    if api_nodes:
        route = api_nodes[0]
        items.append(
            {
                "title": "API/request surface",
                "kind": "flow",
                "reason": "Route evidence gives a concrete entry point into runtime behavior.",
                "component": component_for_path(route.get("path") or ""),
                "confidence": 0.8,
                "evidence": [route],
            }
        )
    for symbol in _entry_symbols(symbols)[:3]:
        items.append(
            {
                "title": symbol["qualified_name"],
                "kind": "symbol",
                "reason": "Entrypoint-shaped symbol from naming/decorator evidence.",
                "component": component_for_path(symbol["file_path"]),
                "confidence": 0.68,
                "evidence": [
                    {
                        "kind": "symbol",
                        "title": symbol["qualified_name"],
                        "path": symbol["file_path"],
                        "line": symbol["line_start"],
                        "detail": symbol.get("signature") or symbol["kind"],
                        "confidence": 0.68,
                    }
                ],
            }
        )
    return _dedupe_by_title(items)[:10]


def _chapters(
    *,
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    component_metrics: dict[str, dict[str, Any]],
    component_edges: list[dict[str, Any]],
    docs_files: list[dict[str, Any]],
    test_files: list[dict[str, Any]],
    api_nodes: list[dict[str, Any]],
    recent_commits: list[dict[str, Any]],
    risky_components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    api_components = _components_by_keywords(component_metrics, FLOW_KEYWORDS["api"], limit=8)
    scheduler_components = _components_by_keywords(component_metrics, SCHEDULER_ORCHESTRATION_KEYWORDS, limit=8)
    scheduler_names = {item["component"] for item in scheduler_components}
    service_components = [
        item
        for item in _components_by_keywords(component_metrics, FLOW_KEYWORDS["service"], limit=12)
        if item["component"] not in scheduler_names
    ][:8]
    data_components = _components_by_keywords(component_metrics, FLOW_KEYWORDS["data"], limit=8)
    integration_components = _components_by_keywords(component_metrics, FLOW_KEYWORDS["integration"], limit=8)
    test_components = _components_by_keywords(component_metrics, FLOW_KEYWORDS["tests"], limit=8)
    docs_components = _unique_component_payloads(docs_files)
    return [
        _chapter(
            "api",
            "API",
            "Routes, API folders, command modules, and handlers show how work enters the system.",
            "Trace one route or command downward into service and data/model chapters.",
            api_nodes[:6] + [_component_evidence(item, "API naming evidence") for item in api_components[:4]],
            api_components[:6],
        ),
        _chapter(
            "services",
            "Services",
            "Service, manager, handler, command, and worker components usually contain product behavior and orchestration entrypoints.",
            "Read public methods and trace callees from the highest-signal service component.",
            [_component_evidence(item, "Service naming evidence") for item in service_components[:6]],
            service_components[:6],
        ),
        _chapter(
            "scheduler-orchestration",
            "Scheduler/orchestration",
            "Schedulers, conductors, workers, queues, runners, and executors explain background work and cross-component coordination.",
            "Trace from scheduler/orchestration components into services, integrations, and data/model boundaries.",
            [_component_evidence(item, "Scheduler/orchestration naming evidence") for item in scheduler_components[:6]],
            scheduler_components[:6],
        ),
        _chapter(
            "data-model",
            "Data/model",
            "Data/model/object/database components explain the domain objects and persistence boundaries.",
            "Read model/object classes before making behavior changes that cross components.",
            [_component_evidence(item, "Data/model naming evidence") for item in data_components[:6]],
            data_components[:6],
        ),
        _chapter(
            "integrations",
            "Integrations",
            "Clients, drivers, adapters, and external-facing components show where this repo depends on other systems.",
            "Keep third-party nodes hidden until this chapter, then inspect only the boundary edges.",
            [_component_evidence(item, "Boundary/integration naming evidence") for item in integration_components[:6]],
            integration_components[:6],
        ),
        _chapter(
            "tests",
            "Tests",
            "Tests and fixtures reveal supported behavior and the safest verification commands to run after edits.",
            "Find tests near the component you plan to change before editing.",
            [_file_evidence(item, "Indexed test or fixture file") for item in test_files[:8]],
            test_components[:6],
        ),
        _chapter(
            "docs-config",
            "Docs/config",
            "README, docs, install, requirements, and config files explain setup, constraints, and repository intent.",
            "Read these before expanding runtime components so setup noise does not dominate the map.",
            [_file_evidence(item, "Docs/config evidence") for item in docs_files[:8]],
            docs_components[:6],
        ),
        _chapter(
            "change-risk",
            "Change Risk",
            "High-churn or high-degree components deserve extra evidence and focused verification.",
            "Inspect recent commits and co-changing components before changing these areas.",
            [_component_evidence(item, "Risk score from size, commits, and relationship degree") for item in risky_components[:6]]
            + [_commit_evidence(item) for item in recent_commits[:3]],
            risky_components[:6],
        ),
    ]


def _chapter(
    chapter_id: str,
    title: str,
    why: str,
    action: str,
    evidence: list[dict[str, Any]],
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    clean_evidence = [item for item in evidence if item][:8]
    return {
        "id": chapter_id,
        "title": title,
        "why": why,
        "action": action,
        "confidence": round(_avg([item.get("confidence", 0.65) for item in clean_evidence], default=0.62), 2),
        "components": [item.get("component") for item in components if item.get("component")][:8],
        "evidence": clean_evidence,
    }


def _flows(
    *,
    top_components: list[dict[str, Any]],
    component_metrics: dict[str, dict[str, Any]],
    component_edges: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    api_nodes: list[dict[str, Any]],
    docs_files: list[dict[str, Any]],
    test_files: list[dict[str, Any]],
    recent_commits: list[dict[str, Any]],
    risky_components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    data_components = _components_by_keywords(component_metrics, FLOW_KEYWORDS["data"], limit=8)
    dependency_steps = []
    seen_dependency_pairs = set()
    for edge in component_edges[:6]:
        pair = (edge["source"], edge["target"])
        if pair in seen_dependency_pairs:
            continue
        seen_dependency_pairs.add(pair)
        dependency_steps.append(
            {
                "title": f"{edge['source']} -> {edge['target']}",
                "detail": f"{edge['type']} relationship observed {edge['weight']} time(s).",
                "component": edge["source"],
                "evidence": edge["examples"][:2],
                "confidence": edge.get("confidence", 0.7),
            }
        )
    if not dependency_steps:
        dependency_steps.append(
            {
                "title": "No strong internal component edges yet",
                "detail": "The current index did not find cross-component source-to-source edges for this view.",
                "component": "",
                "evidence": [],
                "confidence": 0.45,
            }
        )
    return [
        {
            "id": "first-read",
            "title": "First Read Path",
            "intent": "Give a new engineer the shortest non-random tour through the repository.",
            "steps": _first_read_steps(docs_files, top_components, api_nodes, test_files),
            "lens": "subway",
        },
        {
            "id": "request-flow",
            "title": "API/Request Flow",
            "intent": "Start from routes/API-shaped modules, then follow calls/imports into services and data.",
            "steps": _route_steps(api_nodes, component_edges),
            "lens": "apis",
        },
        {
            "id": "startup-config-flow",
            "title": "Startup/Config Flow",
            "intent": "Show the files and entrypoint-shaped symbols that explain how the repository starts, configures, or boots.",
            "steps": _startup_config_steps(docs_files, symbols, component_metrics),
            "lens": "subway",
        },
        {
            "id": "data-model-flow",
            "title": "Data/Model Flow",
            "intent": "Show model, object, database, schema, and state boundaries before following behavior across components.",
            "steps": _data_model_steps(data_components, component_edges),
            "lens": "database",
        },
        {
            "id": "dependency-flow",
            "title": "Component Dependency Flow",
            "intent": "Show the strongest component-to-component evidence instead of every graph edge.",
            "steps": dependency_steps,
            "lens": "overview",
        },
        {
            "id": "git-change-flow",
            "title": "Git/Change Flow",
            "intent": "Explain where edits are likely to ripple based on churn and recent history.",
            "steps": _change_steps(risky_components, recent_commits),
            "lens": "git",
        },
        {
            "id": "test-flow",
            "title": "Test Flow",
            "intent": "Connect understanding work to the tests and commands a new contributor should run.",
            "steps": _verification_steps(test_files),
            "lens": "tests",
        },
    ]


def _first_read_steps(
    docs_files: list[dict[str, Any]],
    top_components: list[dict[str, Any]],
    api_nodes: list[dict[str, Any]],
    test_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    steps = []
    if docs_files:
        steps.append(_step_from_file("Read repository orientation", docs_files[0], "Start with the highest-signal docs/config file."))
    for component in top_components[:4]:
        steps.append(
            {
                "title": f"Open {component['component']}",
                "detail": f"{component['files']} files, {component['symbols']} symbols, {component['commits']} commit touches.",
                "component": component["component"],
                "evidence": [_component_evidence(component, "Major component")],
                "confidence": 0.76,
            }
        )
    if api_nodes:
        steps.append(
            {
                "title": "Trace an entrypoint",
                "detail": "Use route evidence to connect the map to runtime behavior.",
                "component": component_for_path(api_nodes[0].get("path") or ""),
                "evidence": [api_nodes[0]],
                "confidence": 0.78,
            }
        )
    if test_files:
        steps.append(_step_from_file("Find nearby tests", test_files[0], "Use tests to validate the mental model."))
    return steps[:8]


def _route_steps(api_nodes: list[dict[str, Any]], component_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps = []
    for route in api_nodes[:4]:
        steps.append(
            {
                "title": route.get("title") or "Route",
                "detail": route.get("detail") or "Indexed route node",
                "component": component_for_path(route.get("path") or ""),
                "evidence": [route],
                "confidence": route.get("confidence", 0.72),
            }
        )
    api_edges = [
        edge
        for edge in component_edges
        if _matches_keywords(edge["source"], FLOW_KEYWORDS["api"])
        or _matches_keywords(edge["target"], FLOW_KEYWORDS["api"])
        or edge["type"] in {"handles", "http_calls"}
    ]
    for edge in api_edges[:4]:
        steps.append(
            {
                "title": f"{edge['source']} -> {edge['target']}",
                "detail": f"{edge['type']} x{edge['weight']}",
                "component": edge["source"],
                "evidence": edge["examples"][:2],
                "confidence": edge.get("confidence", 0.68),
            }
        )
    if not steps:
        steps.append(
            {
                "title": "No strong request/API entrypoint evidence yet",
                "detail": "Use Source outline or search for route/api/cmd modules, then trace from a selected component.",
                "component": "",
                "evidence": [],
                "confidence": 0.45,
            }
        )
    return steps[:8]


def _startup_config_steps(
    docs_files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    steps = []
    startup_files = sorted(
        [item for item in docs_files if _startup_config_score(str(item.get("path") or "")) > 0],
        key=lambda item: (-_startup_config_score(str(item.get("path") or "")), item["path"]),
    )
    for file_item in startup_files[:4]:
        steps.append(_step_from_file("Read " + file_item["path"], file_item, "Startup/config file evidence."))
    for symbol in _startup_symbols(symbols)[:3]:
        steps.append(
            {
                "title": symbol.get("qualified_name") or symbol.get("name") or "Entrypoint-shaped symbol",
                "detail": symbol.get("signature") or symbol.get("kind") or "Entrypoint-shaped symbol from naming evidence.",
                "component": component_for_path(str(symbol.get("file_path") or "")),
                "evidence": [
                    {
                        "kind": "symbol",
                        "title": str(symbol.get("qualified_name") or symbol.get("name") or ""),
                        "path": str(symbol.get("file_path") or ""),
                        "line": int(symbol.get("line_start") or 0),
                        "detail": str(symbol.get("kind") or ""),
                        "confidence": 0.66,
                    }
                ],
                "confidence": 0.66,
            }
        )
    startup_components = _components_by_keywords(components, STARTUP_CONFIG_KEYWORDS, limit=4)
    for component in startup_components:
        steps.append(
            {
                "title": f"Open {component['component']}",
                "detail": "Component or files contain startup/config naming evidence.",
                "component": component["component"],
                "evidence": [_component_evidence(component, "Startup/config naming evidence")],
                "confidence": 0.66,
            }
        )
    if not steps:
        steps.append(
            {
                "title": "No strong startup/config flow evidence yet",
                "detail": "Look for README, setup, config, cmd/main, WSGI/ASGI, scheduler, or service bootstrap files.",
                "component": "",
                "evidence": [],
                "confidence": 0.45,
            }
        )
    return _dedupe_by_title(steps)[:8]


def _data_model_steps(data_components: list[dict[str, Any]], component_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps = []
    for component in data_components[:4]:
        steps.append(
            {
                "title": f"Inspect {component['component']}",
                "detail": "Data/model/object/database naming evidence marks this as a state boundary.",
                "component": component["component"],
                "evidence": [_component_evidence(component, "Data/model naming evidence")],
                "confidence": 0.7,
            }
        )
    data_edges = [
        edge
        for edge in component_edges
        if _matches_keywords(edge["source"], FLOW_KEYWORDS["data"])
        or _matches_keywords(edge["target"], FLOW_KEYWORDS["data"])
    ]
    for edge in data_edges[:4]:
        steps.append(
            {
                "title": f"{edge['source']} -> {edge['target']}",
                "detail": f"{edge['type']} relationship observed {edge['weight']} time(s) near data/model naming evidence.",
                "component": edge["source"],
                "evidence": edge["examples"][:2],
                "confidence": edge.get("confidence", 0.68),
            }
        )
    if not steps:
        steps.append(
            {
                "title": "No strong data/model flow evidence yet",
                "detail": "Search for model, object, database, schema, migration, store, or state modules before editing persistence behavior.",
                "component": "",
                "evidence": [],
                "confidence": 0.45,
            }
        )
    return _dedupe_by_title(steps)[:8]


def _change_steps(risky_components: list[dict[str, Any]], recent_commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps = []
    for component in risky_components[:4]:
        steps.append(
            {
                "title": f"Inspect {component['component']}",
                "detail": f"Risk score {component.get('risk_score', 0)} from size, churn, and relationship degree.",
                "component": component["component"],
                "evidence": [_component_evidence(component, "High-risk component")],
                "confidence": 0.7,
            }
        )
    for commit in recent_commits[:4]:
        steps.append(
            {
                "title": commit.get("title") or commit.get("sha"),
                "detail": f"{commit.get('date', 'unknown date')} by {commit.get('author', 'Unknown')}",
                "component": commit.get("component", ""),
                "evidence": [_commit_evidence(commit)],
                "confidence": 0.72,
            }
        )
    return steps[:8]


def _verification_steps(test_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not test_files:
        return [
            {
                "title": "No test files were obvious in the current index",
                "detail": "Use the Verify plan workflow after selecting a component or task.",
                "component": "",
                "evidence": [],
                "confidence": 0.46,
            }
        ]
    return [
        _step_from_file("Read or run " + item["path"], item, "Indexed test/fixture file that can anchor verification.")
        for item in test_files[:8]
    ]


def _step_from_file(title: str, file_item: dict[str, Any], detail: str) -> dict[str, Any]:
    return {
        "title": title,
        "detail": detail,
        "component": file_item.get("component", ""),
        "evidence": [_file_evidence(file_item, detail)],
        "confidence": 0.72,
    }


def _concepts(
    components: dict[str, dict[str, Any]],
    symbols: list[dict[str, Any]],
    files: list[dict[str, Any]],
    identity: dict[str, Any],
) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    identity_evidence = identity.get("evidence") or []
    for token in identity.get("domain_terms", []):
        if token in STOP_WORDS:
            continue
        counts[str(token)] += 10
        if identity_evidence and len(evidence[str(token)]) < 3:
            evidence[str(token)].append(dict(identity_evidence[0]))
    for component, item in components.items():
        for token in _tokens(component):
            counts[token] += max(2, int(item.get("files") or 0))
            if len(evidence[token]) < 3:
                evidence[token].append(_component_evidence(item, "Component name evidence"))
    for row in symbols[:5000]:
        for token in _tokens(str(row.get("name") or "")):
            counts[token] += 1
            if len(evidence[token]) < 3:
                evidence[token].append(
                    {
                        "kind": "symbol",
                        "title": str(row.get("qualified_name") or row.get("name") or ""),
                        "path": str(row.get("file_path") or ""),
                        "line": int(row.get("line_start") or 0),
                        "detail": str(row.get("kind") or ""),
                        "confidence": 0.62,
                    }
                )
    for row in files:
        path = str(row.get("path") or "")
        for token in _tokens(path):
            if token in STOP_WORDS:
                continue
            counts[token] += 1
            if len(evidence[token]) < 3:
                evidence[token].append(
                    {
                        "kind": "file",
                        "title": path,
                        "path": path,
                        "detail": "Path token appears in indexed repository structure",
                        "confidence": 0.58,
                    }
                )
    concepts = []
    for term, count in counts.most_common(12):
        if term in STOP_WORDS or len(term) < 3:
            continue
        concepts.append(
            {
                "term": term,
                "weight": int(count),
                "why": f"Appears across indexed components, symbols, or paths {count} time(s).",
                "evidence": evidence[term][:3],
            }
        )
    return concepts[:10]


def _ignore_for_now(
    store: GraphStore,
    files: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = store.connection.execute(
        """
        SELECT module, COUNT(*) AS count, MIN(f.path) AS sample_path
        FROM imports i
        JOIN files f ON f.id = i.file_id
        GROUP BY module
        ORDER BY count DESC, module
        LIMIT 80
        """
    ).fetchall()
    local_roots = set(components)
    ignored = []
    for row in rows:
        module = str(row["module"] or "")
        root = module.split(".", 1)[0]
        if not root or root in local_roots or root.startswith("."):
            continue
        ignored.append(
            {
                "name": module,
                "kind": "dependency",
                "count": int(row["count"] or 0),
                "reason": "External/library import. Hide it until you are reading integration boundaries.",
                "evidence": [
                    {
                        "kind": "import",
                        "title": module,
                        "path": str(row["sample_path"] or ""),
                        "detail": f"Imported {int(row['count'] or 0)} time(s)",
                        "confidence": 0.62,
                    }
                ],
            }
        )
        if len(ignored) >= 8:
            break
    docs_noise = [
        {
            "name": str(row.get("path") or ""),
            "kind": "docs_config",
            "count": 1,
            "reason": "Docs/config files are useful for setup but should not dominate the runtime map.",
            "evidence": [_file_evidence(_file_payload(row), "Docs/config path")],
        }
        for row in files
        if "docs" in _path_categories(str(row.get("path") or ""))
    ][:4]
    return ignored + docs_noise


def _dashboard(
    stats: dict[str, Any],
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    code_edges: list[dict[str, Any]],
    commit_rows: list[Any],
    components: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    categories = Counter()
    for row in files:
        categories.update(_path_categories(str(row.get("path") or "")))
    unresolved_calls = sum(
        1
        for row in code_edges
        if str(row.get("edge_type") or "").upper() == "CALLS" and not row.get("target_path")
    )
    return {
        "stats": {
            "files": len(files),
            "symbols": len(symbols),
            "components": len(components),
            "edges": int(stats.get("graph_edges") or len(code_edges)),
            "commits": len(commit_rows),
        },
        "coverage": {
            "docs_config_files": categories["docs"],
            "test_files": categories["tests"],
            "runtime_files": max(0, len(files) - categories["docs"] - categories["tests"]),
            "unresolved_calls": unresolved_calls,
        },
        "recommended_next_actions": [
            "Read the First Read Path before expanding the whole map.",
            "Keep third-party and docs/config hidden until you need boundary evidence.",
            "Use Trace API/data flow from one selected component, then inspect evidence cards in order.",
            "Generate an Agent Pack once you know the task area.",
        ],
    }


def _new_engineer_dashboard(
    *,
    start_here: list[dict[str, Any]],
    flows: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    ignore_for_now: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    flow_ids = ["request-flow", "startup-config-flow", "data-model-flow", "test-flow", "git-change-flow"]
    flow_by_id = {str(flow.get("id") or ""): flow for flow in flows}
    chapter_by_id = {str(chapter.get("id") or ""): chapter for chapter in chapters}
    sections = [
        _new_engineer_section(
            "read-first",
            "Read these first",
            "The shortest evidence-backed reading queue before opening the full graph.",
            "start",
            [_dashboard_item_from_start(item) for item in start_here[:4]],
        ),
        _new_engineer_section(
            "understand-flows",
            "Understand these flows",
            "Runtime, startup/config, data/model, tests, and git/change paths that explain how the repo moves.",
            "runtime",
            [_dashboard_item_from_flow(flow_by_id[flow_id]) for flow_id in flow_ids if flow_id in flow_by_id][:5],
        ),
        _new_engineer_section(
            "avoid-noise",
            "Avoid this noise",
            "Third-party imports and setup/docs paths that can overwhelm a first read.",
            "start",
            [_dashboard_item_from_noise(item) for item in ignore_for_now[:4]],
        ),
        _new_engineer_section(
            "high-risk",
            "High-risk areas",
            "High-churn, high-degree, or recently touched areas that deserve careful verification.",
            "risk",
            _dashboard_risk_items(chapter_by_id.get("change-risk", {}), flow_by_id.get("git-change-flow", {}))[:4],
        ),
    ]
    return {
        "title": "New Engineer Dashboard",
        "summary": summary.get("headline") or "Start from evidence, then move into flows and risk.",
        "sections": sections,
    }


def _new_engineer_section(
    section_id: str,
    title: str,
    summary: str,
    target_section: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": section_id,
        "title": title,
        "summary": summary,
        "target_section": target_section,
        "items": [item for item in items if item][:5],
    }


def _dashboard_item_from_start(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(item.get("title") or ""),
        "kind": str(item.get("kind") or "start"),
        "reason": str(item.get("reason") or "Start-here evidence for a first read."),
        "component": str(item.get("component") or ""),
        "target_section": "start",
        "confidence": float(item.get("confidence") or 0.62),
        "evidence": list(item.get("evidence") or [])[:3],
    }


def _dashboard_item_from_flow(flow: dict[str, Any]) -> dict[str, Any]:
    evidence = []
    component = ""
    confidences = []
    for step in flow.get("steps", [])[:4]:
        if not component:
            component = str(step.get("component") or "")
        evidence.extend(step.get("evidence") or [])
        confidences.append(step.get("confidence"))
    flow_id = str(flow.get("id") or "")
    section = {
        "request-flow": "runtime",
        "startup-config-flow": "runtime",
        "data-model-flow": "data",
        "test-flow": "tests",
        "git-change-flow": "risk",
    }.get(flow_id, "runtime")
    return {
        "title": str(flow.get("title") or ""),
        "kind": "flow",
        "reason": str(flow.get("intent") or "Evidence-backed repository flow."),
        "component": component,
        "target_section": section,
        "target_id": flow_id,
        "confidence": round(_avg(confidences, default=0.62), 2),
        "evidence": evidence[:3],
    }


def _dashboard_item_from_noise(item: dict[str, Any]) -> dict[str, Any]:
    evidence = list(item.get("evidence") or [])[:3]
    return {
        "title": str(item.get("name") or ""),
        "kind": str(item.get("kind") or "noise"),
        "reason": str(item.get("reason") or "Hide this until it is needed for boundary evidence."),
        "component": "",
        "target_section": "start",
        "confidence": round(_avg([entry.get("confidence") for entry in evidence], default=0.58), 2),
        "evidence": evidence,
    }


def _dashboard_risk_items(risk_chapter: dict[str, Any], change_flow: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    evidence_by_title = {
        str(item.get("title") or ""): item
        for item in risk_chapter.get("evidence", [])
        if item.get("title")
    }
    for component in risk_chapter.get("components", [])[:4]:
        evidence = evidence_by_title.get(str(component)) or (risk_chapter.get("evidence") or [{}])[0]
        items.append(
            {
                "title": str(component),
                "kind": "component",
                "reason": str(risk_chapter.get("why") or "High-risk component from churn, size, or relationship evidence."),
                "component": str(component),
                "target_section": "risk",
                "confidence": float(risk_chapter.get("confidence") or evidence.get("confidence") or 0.64),
                "evidence": [evidence] if evidence else [],
            }
        )
    for step in change_flow.get("steps", []):
        if len(items) >= 4:
            break
        items.append(
            {
                "title": str(step.get("title") or ""),
                "kind": "change",
                "reason": str(step.get("detail") or change_flow.get("intent") or "Recent change or churn evidence."),
                "component": str(step.get("component") or ""),
                "target_section": "risk",
                "target_id": str(change_flow.get("id") or "git-change-flow"),
                "confidence": float(step.get("confidence") or 0.62),
                "evidence": list(step.get("evidence") or [])[:3],
            }
        )
    return items


def _summary(
    repo_root: Path,
    stats: dict[str, Any],
    top_components: list[dict[str, Any]],
    docs_files: list[dict[str, Any]],
    api_nodes: list[dict[str, Any]],
    recent_commits: list[dict[str, Any]],
    identity: dict[str, Any],
) -> dict[str, Any]:
    component_names = ", ".join(item["component"] for item in top_components[:5]) or "indexed components"
    structure_headline = (
        f"{repo_root.name} has {int(stats.get('files_indexed') or 0):,} indexed files across "
        f"{len(top_components)} major components; start with {component_names}."
    )
    purpose = str(identity.get("purpose") or "").strip()
    headline = purpose or structure_headline
    bullets = []
    if purpose:
        source = str(identity.get("source") or "repository docs")
        if identity.get("basis") == "code":
            bullets.append("Purpose evidence: inferred from indexed code structure because no README/docs purpose was found.")
        else:
            bullets.append(f"Purpose evidence: {source}.")
    bullets.extend(
        [
            structure_headline,
            f"Top components by indexed surface area: {component_names}.",
            f"Indexed symbols: {int(stats.get('classes') or 0):,} classes, {int(stats.get('functions') or 0):,} functions, {int(stats.get('methods') or 0):,} methods.",
            (
                f"Docs/config anchors found: {len(docs_files)}. Test anchors found through the briefing flow if present."
                if docs_files
                else "No README/docs/config anchors were found; first-read guidance is based on code components, symbols, imports/calls, tests, and commits."
            ),
        ]
    )
    if api_nodes:
        bullets.append(f"API/route evidence exists: {len(api_nodes)} route-like node(s) in the current index.")
    if recent_commits:
        bullets.append(f"Recent git memory is available; newest indexed commit: {recent_commits[0].get('title', '')}.")
    evidence = list(identity.get("evidence") or [])
    evidence.extend(_component_evidence(item, "Top component") for item in top_components[:3])
    evidence.extend(_file_evidence(item, "Orientation file") for item in docs_files[:2])
    evidence.extend(_commit_evidence(item) for item in recent_commits[:1])
    return {
        "headline": headline,
        "structure_headline": structure_headline,
        "purpose": purpose,
        "bullets": bullets,
        "confidence": max(float(identity.get("confidence") or 0), 0.76 if top_components else 0.52),
        "evidence": evidence[:8],
    }


def _agent_brief(
    repo_root: Path,
    summary: dict[str, Any],
    start_here: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    flows: list[dict[str, Any]],
) -> str:
    purpose = summary.get("purpose") or summary.get("headline", "")
    lines = [
        f"# First-time repo brief for {repo_root.name}",
        "",
        f"Purpose: {purpose}",
        "",
        "Use this as orientation before editing. Every item below comes from repository docs, project metadata, or the local CodeAtlas index.",
        "",
        "## Start here",
    ]
    for item in start_here[:8]:
        evidence = item.get("evidence", [{}])[0]
        location = evidence.get("path") or item.get("component") or ""
        lines.append(f"- {item.get('title')}: {item.get('reason')} ({location})")
    lines.extend(["", "## Chapters"])
    for chapter in chapters:
        lines.append(f"- {chapter.get('title')}: {chapter.get('action')}")
    lines.extend(["", "## Flows"])
    for flow in flows:
        lines.append(f"- {flow.get('title')}: {flow.get('intent')}")
    lines.extend(["", "Suggested first task: pick one component, trace API/data flow, inspect evidence, then generate a task-specific Agent Pack."])
    return "\n".join(lines).strip() + "\n"


def _top_components(components: dict[str, dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    items = [
        dict(item)
        for item in components.values()
        if _component_has_indexed_surface(item) and item.get("component") and not _component_is_noise(str(item["component"]))
    ]
    return sorted(items, key=lambda item: (-int(item.get("score") or 0), item["component"]))[:limit]


def _risky_components(
    components: dict[str, dict[str, Any]],
    component_edges: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    degree = Counter()
    for edge in component_edges:
        degree[edge["source"]] += int(edge["weight"])
        degree[edge["target"]] += int(edge["weight"])
    items = []
    for component, item in components.items():
        if _component_is_noise(component) or not _component_has_indexed_surface(item):
            continue
        score = int(item.get("commits") or 0) * 3 + degree[component] + int(item.get("files") or 0)
        enriched = dict(item)
        enriched["risk_score"] = score
        enriched["relationship_degree"] = degree[component]
        items.append(enriched)
    return sorted(items, key=lambda item: (-int(item.get("risk_score") or 0), item["component"]))[:limit]


def _components_by_keywords(
    components: dict[str, dict[str, Any]],
    keywords: tuple[str, ...],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    matches = []
    for component, item in components.items():
        text = " ".join(
            [
                component,
                " ".join(str(path) for path in item.get("sample_files", [])),
                " ".join(str(symbol.get("name", "")) for symbol in item.get("sample_symbols", [])),
            ]
        ).lower()
        if any(keyword in text for keyword in keywords):
            matches.append(dict(item))
    return sorted(matches, key=lambda item: (-int(item.get("score") or 0), item["component"]))[:limit]


def _unique_component_payloads(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_component: dict[str, dict[str, Any]] = {}
    for file_item in files:
        path = str(file_item.get("path") or "")
        component = str(file_item.get("component") or component_for_path(path))
        if not component:
            continue
        item = by_component.setdefault(
            component,
            {
                "component": component,
                "files": 0,
                "symbols": 0,
                "commits": 0,
                "sample_files": [],
                "score": 0,
            },
        )
        item["files"] += 1
        item["score"] += max(1, int(file_item.get("lines") or 0) // 20 + 1)
        if len(item["sample_files"]) < 5:
            item["sample_files"].append(path)
    return sorted(by_component.values(), key=lambda item: (-int(item.get("score") or 0), item["component"]))


def _entry_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hints = ("main", "run", "start", "serve", "handler", "route", "create", "execute", "cmd", "command")
    matches = []
    for row in symbols:
        name = str(row.get("name") or "").lower()
        qname = str(row.get("qualified_name") or "").lower()
        path = str(row.get("file_path") or "").lower()
        if "tests" in _path_categories(path):
            continue
        if any(hint in name or hint in qname or hint in path for hint in hints):
            matches.append(dict(row))
    return sorted(matches, key=lambda row: (_file_start_score(str(row.get("file_path") or "")) * -1, str(row.get("qualified_name") or "")))


def _startup_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hints = {"main", "run", "start", "serve", "cli", "cmd", "command", "wsgi", "asgi", "manage", "app"}
    matches = []
    for row in symbols:
        path = str(row.get("file_path") or "")
        if "tests" in _path_categories(path.lower()):
            continue
        name = str(row.get("name") or "")
        if name.startswith("_") and name not in {"__main__"}:
            continue
        qname = str(row.get("qualified_name") or "")
        tokens = set(_tokens(name) + _tokens(qname) + _tokens(path))
        path_name = Path(path.lower()).name
        if tokens & hints or path_name in {"__main__.py", "cli.py", "manage.py", "wsgi.py", "asgi.py"}:
            matches.append(dict(row))
    return sorted(matches, key=lambda row: (_startup_symbol_score(str(row.get("file_path") or ""), str(row.get("qualified_name") or "")) * -1, str(row.get("qualified_name") or "")))


def _commit_payloads(commit_rows: list[Any], *, limit: int) -> list[dict[str, Any]]:
    payloads = []
    for row in commit_rows[:limit]:
        files = metadata_files(row)
        component = component_for_path(files[0]) if files else ""
        payloads.append(
            {
                "sha": str(row["source_id"])[:12],
                "title": str(row["title"] or ""),
                "author": str(row["author"] or "Unknown"),
                "date": str(row["timestamp"] or "")[:10],
                "component": component,
                "files": files[:5],
            }
        )
    return payloads


def _component_evidence(component: dict[str, Any], detail: str) -> dict[str, Any]:
    sample = component.get("sample_files") or []
    return {
        "kind": "component",
        "title": str(component.get("component") or ""),
        "path": sample[0] if sample else "",
        "detail": (
            f"{detail}: {int(component.get('files') or 0)} files, "
            f"{int(component.get('symbols') or 0)} symbols, {int(component.get('commits') or 0)} commit touches"
        ),
        "confidence": 0.72,
    }


def _file_evidence(file_item: dict[str, Any], detail: str) -> dict[str, Any]:
    return {
        "kind": "file",
        "title": str(file_item.get("path") or ""),
        "path": str(file_item.get("path") or ""),
        "detail": f"{detail}: {file_item.get('language', '')}, {int(file_item.get('lines') or 0)} lines",
        "confidence": 0.7,
    }


def _commit_evidence(commit: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "commit",
        "title": str(commit.get("title") or commit.get("sha") or ""),
        "path": ", ".join(commit.get("files", [])[:2]) if isinstance(commit.get("files"), list) else "",
        "detail": f"{commit.get('date', 'unknown date')} by {commit.get('author', 'Unknown')}",
        "confidence": 0.72,
    }


def _text_evidence(
    kind: str,
    title: Any,
    path: Any,
    line: Any,
    text: Any,
    confidence: float,
) -> dict[str, Any]:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return {
        "kind": kind,
        "title": str(title or path or kind),
        "path": str(path or ""),
        "line": int(line or 1),
        "detail": clean[:320],
        "confidence": round(float(confidence), 2),
    }


def _first_non_empty(values: list[Any]) -> str:
    for value in values:
        clean = str(value or "").strip()
        if clean:
            return clean
    return ""


def _file_payload(row: dict[str, Any]) -> dict[str, Any]:
    path = str(row.get("path") or "")
    return {
        "path": path,
        "component": component_for_path(path),
        "language": str(row.get("language") or ""),
        "lines": int(row.get("line_count") or 0),
    }


def _path_categories(path: str) -> set[str]:
    lower = path.lower()
    categories = set()
    if any(part in lower for part in FLOW_KEYWORDS["docs"]) or re.search(r"\.(md|rst|txt|ya?ml|toml|ini|cfg)$", lower):
        categories.add("docs")
    if any(part in lower for part in FLOW_KEYWORDS["tests"]):
        categories.add("tests")
    if any(part in lower for part in FLOW_KEYWORDS["api"]):
        categories.add("api")
    if any(part in lower for part in FLOW_KEYWORDS["data"]):
        categories.add("data")
    if any(part in lower for part in FLOW_KEYWORDS["service"]):
        categories.add("service")
    if any(part in lower for part in FLOW_KEYWORDS["integration"]):
        categories.add("integration")
    if not categories:
        categories.add("runtime")
    return categories


def _file_start_score(path: str) -> float:
    lower = path.lower()
    score = 0.0
    if Path(path).name.lower() in {"readme.md", "readme.rst", "pyproject.toml", "setup.py", "package.json"}:
        score += 100
    if lower.startswith(("docs/", "doc/")):
        score += 30
    if any(token in lower for token in ("api", "route", "cmd", "main", "service", "manager", "scheduler", "conductor")):
        score += 18
    if any(token in lower for token in ("test", "fixture", "mock")):
        score += 8
    return score


def _startup_config_score(path: str) -> float:
    lower = path.lower()
    name = Path(lower).name
    score = 0.0
    if name in {"readme.md", "readme.rst", "pyproject.toml", "package.json", "setup.cfg", "setup.py"}:
        score += 90
    if name in {"requirements.txt", "test-requirements.txt", "tox.ini", ".pre-commit-config.yaml", ".zuul.yaml"}:
        score += 72
    if any(token in lower for token in STARTUP_CONFIG_KEYWORDS):
        score += 28
    if lower.startswith(("docs/", "doc/")):
        score += 12
    return score


def _startup_symbol_score(path: str, qualified_name: str) -> float:
    lower_path = path.lower()
    lower_name = qualified_name.lower()
    score = _startup_config_score(path)
    if Path(lower_path).name in {"__main__.py", "cli.py", "manage.py", "wsgi.py", "asgi.py"}:
        score += 70
    if any(token in set(_tokens(lower_name)) for token in ("main", "run", "serve", "start")):
        score += 40
    if any(token in lower_path for token in ("cmd", "cli", "main", "wsgi", "asgi", "manage")):
        score += 28
    return score


def _matches_keywords(value: str, keywords: tuple[str, ...]) -> bool:
    lower = str(value or "").lower()
    return any(keyword in lower for keyword in keywords)


def _component_is_noise(component: str) -> bool:
    lower = component.lower()
    return lower in {"", ".", "__pycache__", ".git", ".tox", ".venv", "node_modules"} or lower.startswith(".")


def _component_has_indexed_surface(component: dict[str, Any]) -> bool:
    return int(component.get("files") or 0) > 0 or int(component.get("symbols") or 0) > 0


def _label_root(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split(".", 1)[0].split("/", 1)[0]


def _tokens(value: str) -> list[str]:
    words = re.split(r"[^A-Za-z0-9]+", value)
    tokens = []
    for word in words:
        for token in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", word):
            clean = token.lower()
            if len(clean) >= 3 and clean not in STOP_WORDS:
                tokens.append(clean)
    return tokens


def _dedupe_by_title(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        title = str(item.get("title") or "")
        if title in seen:
            continue
        seen.add(title)
        result.append(item)
    return result


def _avg(values: list[Any], *, default: float) -> float:
    numbers = [float(value) for value in values if isinstance(value, int | float)]
    return sum(numbers) / len(numbers) if numbers else default


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
