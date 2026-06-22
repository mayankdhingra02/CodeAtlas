from __future__ import annotations

import errno
import json
import socket
import subprocess
import tarfile
import tempfile
import webbrowser
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

from .analysis import dead_code, http_confidence_summary, route_summary, structural_query
from .config import CodeAtlasPaths, resolve_repo_root
from .indexer import RepositoryIndexer
from .memory import MemoryQueryEngine, MemoryStore, component_for_path, metadata_files, parse_json
from .packs import context_pack, render_context_pack
from .project_config import load_project_config, restore_classification_config, update_classification_config
from .retrieval import RetrievalEngine
from .rules import run_rule_checks
from .source import source_outline
from .status import index_status
from .storage import GraphStore
from .verification import verification_plan
from .workflow_cache import cached_workflow

COMPARE_CACHE_VERSION = 1
SERVER_STARTED = datetime.now(UTC)
SERVER_STARTED_AT = SERVER_STARTED.isoformat()
UI_VERSION = "compare-evidence-mode-1"
ASSET_DIR = Path(__file__).with_name("assets")
VISUALIZATION_ASSET_NAMES = (
    "visualization.html",
    "visualization.css",
    "visualization.js",
)


def source_freshness() -> dict[str, Any]:
    paths = [Path(__file__), *(ASSET_DIR / name for name in VISUALIZATION_ASSET_NAMES)]
    try:
        modified_at = max(datetime.fromtimestamp(path.stat().st_mtime, UTC) for path in paths)
    except OSError:
        return {"source_modified_at": "", "server_source_stale": False}
    return {
        "source_modified_at": modified_at.isoformat(),
        "server_source_stale": modified_at > SERVER_STARTED,
    }


class VisualizationService:
    def __init__(self) -> None:
        self._snapshot_locks: dict[str, Lock] = {}

    def prepare(
        self,
        repo_path: str | Path,
        *,
        refresh: bool = True,
        max_commits: int = 1000,
    ) -> Path:
        repo_root = resolve_repo_root(repo_path)
        paths = CodeAtlasPaths(repo_root)
        if refresh or not paths.database_path.exists():
            RepositoryIndexer().index(repo_root, incremental=True)
            MemoryQueryEngine().index_memory(
                repo_root,
                max_commits=max_commits,
                incremental=True,
            )
        return repo_root

    def build_map(self, repo_path: str | Path) -> dict[str, Any]:
        repo_root = resolve_repo_root(repo_path)
        paths = CodeAtlasPaths(repo_root)
        project_config = load_project_config(repo_root)
        if not paths.database_path.exists():
            msg = f"No CodeAtlas index found at {paths.database_path}. Run `codeatlas serve {repo_root}`."
            raise FileNotFoundError(msg)

        graph_store = GraphStore(paths.database_path)
        memory_store = MemoryStore(paths.database_path)
        try:
            graph_store.initialize()
            memory_store.initialize()
            files = [dict(row) for row in graph_store.file_rows()]
            symbols = [dict(row) for row in graph_store.all_symbols()]
            code_edges = [dict(row) for row in self._code_edges(graph_store)]
            commit_rows = memory_store.commit_evidence(limit=2000)
            memory_edges = [dict(row) for row in self._memory_edges(memory_store)]
            supported_languages = graph_store.get_metadata("supported_languages", ["python"])
            index_report = graph_store.get_metadata("last_index_report", {})
            repository_stats = graph_store.repository_stats()

            component_graph = self._component_graph(files, symbols, code_edges, commit_rows, memory_edges)
            commit_graph = self._commit_graph(commit_rows)
            stats = {
                "files": len(files),
                "symbols": len(symbols),
                "components": len(component_graph["nodes"]),
                "component_edges": len(component_graph["edges"]),
                "commits": len(commit_rows),
                "last_indexed_at": repository_stats.get("last_indexed_at"),
                "generated_at": datetime.now(UTC).isoformat(),
            }
            return {
                "repo": {
                    "name": repo_root.name,
                    "path": str(repo_root),
                    "database": str(paths.database_path),
                },
                "stats": stats,
                "build": {
                    "server_started_at": SERVER_STARTED_AT,
                    "ui_version": UI_VERSION,
                    "config_fingerprint": project_config.fingerprint,
                    "config_path": str(project_config.path) if project_config.path else "",
                    "cache_enabled": project_config.cache.enabled,
                    "cache_ttl_seconds": project_config.cache.ttl_seconds,
                    **source_freshness(),
                },
                "config": project_config.public_payload(),
                "inventory": {
                    "files": compact_file_inventory(files),
                    "symbols": compact_symbol_inventory(symbols),
                    "commits": compact_commit_inventory(commit_rows),
                },
                "diagnostics": compact_index_diagnostics(
                    files,
                    symbols,
                    code_edges,
                    stats,
                    supported_languages=supported_languages,
                    index_report=index_report,
                ),
                "component_graph": component_graph,
                "commit_graph": commit_graph,
            }
        finally:
            graph_store.close()
            memory_store.close()

    def build_compare(
        self,
        repo_path: str | Path,
        *,
        base_ref: str,
        head_ref: str,
    ) -> dict[str, Any]:
        repo_root = resolve_repo_root(repo_path)
        base_sha = resolve_git_ref(repo_root, base_ref)
        head_sha = resolve_git_ref(repo_root, head_ref)
        base_map = self._compare_snapshot(repo_root, base_sha)
        head_map = self._compare_snapshot(repo_root, head_sha)

        base_graph = base_map["component_graph"]
        head_graph = head_map["component_graph"]
        annotated_base, annotated_head, summary = annotate_architecture_diff(base_graph, head_graph)
        return {
            "repo": {
                "name": repo_root.name,
                "path": str(repo_root),
            },
            "base": {
                "ref": base_ref,
                "sha": base_sha,
                "graph": annotated_base,
                "stats": base_map["stats"],
            },
            "head": {
                "ref": head_ref,
                "sha": head_sha,
                "graph": annotated_head,
                "stats": head_map["stats"],
            },
            "summary": summary
            | {
                "generated_at": datetime.now(UTC).isoformat(),
                "cache": {
                    "base": "hit" if base_map.get("cache_hit") else "miss",
                    "head": "hit" if head_map.get("cache_hit") else "miss",
                },
            },
        }

    def warm_compare_cache(self, repo_path: str | Path, refs: list[str]) -> dict[str, Any]:
        repo_root = resolve_repo_root(repo_path)
        warmed = []
        for ref in refs[:4]:
            clean_ref = str(ref).strip()
            if not clean_ref:
                continue
            sha = resolve_git_ref(repo_root, clean_ref)
            snapshot = self._compare_snapshot(repo_root, sha)
            warmed.append(
                {
                    "ref": clean_ref,
                    "sha": sha,
                    "cache": "hit" if snapshot.get("cache_hit") else "miss",
                }
            )
        return {"warmed": warmed, "generated_at": datetime.now(UTC).isoformat()}

    def _compare_snapshot(self, repo_root: Path, sha: str) -> dict[str, Any]:
        lock = self._snapshot_locks.setdefault(sha, Lock())
        with lock:
            cached = load_compare_snapshot(repo_root, sha)
            if cached:
                return cached | {"cache_hit": True}
            with tempfile.TemporaryDirectory(prefix="codeatlas-compare-") as temp_name:
                snapshot_root = Path(temp_name) / "snapshot"
                snapshot_root.mkdir()
                extract_git_archive(repo_root, sha, snapshot_root)
                RepositoryIndexer().index(snapshot_root, incremental=False)
                snapshot_map = self.build_map(snapshot_root)
            snapshot = {
                "schema_version": COMPARE_CACHE_VERSION,
                "sha": sha,
                "generated_at": datetime.now(UTC).isoformat(),
                "stats": snapshot_map["stats"],
                "component_graph": snapshot_map["component_graph"],
            }
            write_compare_snapshot(repo_root, sha, snapshot)
            return snapshot | {"cache_hit": False}

    def ask(
        self,
        repo_path: str | Path,
        question: str,
        *,
        max_tokens: int = 3000,
    ) -> dict[str, Any]:
        query = question.strip()
        if not query:
            raise ValueError("Question cannot be empty.")
        repo_root = resolve_repo_root(repo_path)
        memory = MemoryQueryEngine()
        retrieval = RetrievalEngine()
        context = memory.compressed_context(repo_root, query, max_tokens=max_tokens)
        code_result = retrieval.retrieve(repo_root, query, depth=2, max_tokens=max_tokens)
        code_snippets = list(code_result.snippets)
        if not code_snippets or all(snippet.kind == "FILE" for snippet in code_snippets):
            seen_symbols: set[str] = set()
            text_fallback_snippets = code_snippets
            code_snippets = []
            for term in chat_query_terms(query):
                fallback = retrieval.retrieve(repo_root, term, depth=2, max_tokens=max_tokens)
                for snippet in fallback.snippets:
                    if snippet.kind == "FILE":
                        continue
                    if snippet.qualified_name in seen_symbols:
                        continue
                    seen_symbols.add(snippet.qualified_name)
                    code_snippets.append(snippet)
                if len(code_snippets) >= 5:
                    break
            code_snippets.extend(text_fallback_snippets)
        snippets = [
            {
                "file_path": snippet.file_path,
                "symbol": snippet.qualified_name,
                "kind": snippet.kind,
                "lines": f"{snippet.line_start}-{snippet.line_end}",
                "reason": snippet.reason,
                "code": snippet.code,
            }
            for snippet in code_snippets[:5]
        ]
        history = [asdict(event) for event in context.history[:5]]
        architecture = [
            asdict(finding)
            for finding in context.architecture
            if finding.confidence > 0 or "No architecture-specific evidence" not in finding.summary
        ][:4]
        decisions = [
            asdict(answer)
            for answer in context.design_decisions
            if answer.confidence > 0 or "No evidence-backed" not in answer.answer
        ][:3]
        ownership = [asdict(entry) for entry in context.ownership[:3]]
        evidence = [asdict(ref) for ref in context.evidence[:8]]
        return {
            "question": query,
            "answer": build_chat_answer(
                query=query,
                snippets=snippets,
                history=history,
                architecture=architecture,
                decisions=decisions,
                ownership=ownership,
            ),
            "code": snippets,
            "history": history,
            "architecture": architecture,
            "decisions": decisions,
            "ownership": ownership,
            "evidence": evidence,
            "token_report": code_result.token_report.__dict__
            | {"savings_percent": code_result.token_report.savings_percent},
            "estimated_context_tokens": context.estimated_tokens,
        }

    def agent_context(
        self,
        repo_path: str | Path,
        task: str,
        *,
        max_tokens: int = 5000,
    ) -> dict[str, Any]:
        query = task.strip()
        if not query:
            raise ValueError("Task cannot be empty.")
        repo_root = resolve_repo_root(repo_path)
        retrieval = RetrievalEngine()
        memory = MemoryQueryEngine()
        code_result = retrieval.retrieve(repo_root, query, depth=2, max_tokens=max_tokens)
        context = memory.compressed_context(repo_root, query, max_tokens=max_tokens)
        snippets = [
            {
                "file_path": snippet.file_path,
                "symbol": snippet.qualified_name,
                "kind": snippet.kind,
                "lines": f"{snippet.line_start}-{snippet.line_end}",
                "reason": snippet.reason,
                "code": snippet.code,
            }
            for snippet in code_result.snippets[:8]
        ]
        evidence = [asdict(ref) for ref in context.evidence[:8]]
        ownership = [asdict(entry) for entry in context.ownership[:5]]
        payload = {
            "task": query,
            "code": snippets,
            "evidence": evidence,
            "ownership": ownership,
            "token_report": code_result.token_report.__dict__
            | {"savings_percent": code_result.token_report.savings_percent},
            "estimated_context_tokens": context.estimated_tokens,
        }
        payload["markdown"] = build_agent_context_markdown(payload)
        return payload

    def serve(
        self,
        repo_path: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        refresh: bool = True,
        open_browser: bool = True,
        max_commits: int = 1000,
    ) -> None:
        repo_root = self.prepare(repo_path, refresh=refresh, max_commits=max_commits)
        server_port = find_available_port(host, port)
        server = create_visualization_server(repo_root, host=host, port=server_port)
        url = f"http://{host}:{server_port}/"
        if open_browser:
            webbrowser.open(url)
        print(f"CodeAtlas visualization running at {url}", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()

    def _code_edges(self, store: GraphStore) -> list[Any]:
        return list(
            store.connection.execute(
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
                  ss.module AS source_module,
                  ss.signature AS source_signature,
                  ss.line_start AS source_line_start,
                  ss.line_end AS source_line_end,
                  t.key AS target_key,
                  t.type AS target_type,
                  t.label AS target_label,
                  t.file_path AS target_path,
                  ts.qualified_name AS target_qualified_name,
                  ts.name AS target_symbol_name,
                  ts.kind AS target_symbol_kind,
                  ts.module AS target_module,
                  ts.signature AS target_signature,
                  ts.line_start AS target_line_start,
                  ts.line_end AS target_line_end
                FROM edges e
                LEFT JOIN nodes s ON s.key = e.source_key
                LEFT JOIN nodes t ON t.key = e.target_key
                LEFT JOIN symbols ss ON ss.id = s.symbol_id
                LEFT JOIN symbols ts ON ts.id = t.symbol_id
                """
            ).fetchall()
        )

    def _memory_edges(self, store: MemoryStore) -> list[Any]:
        return list(
            store.connection.execute(
                """
                SELECT source_key, target_key, relationship, confidence, metadata_json
                FROM memory_relationships
                """
            ).fetchall()
        )

    def _component_graph(
        self,
        files: list[dict[str, Any]],
        symbols: list[dict[str, Any]],
        code_edges: list[dict[str, Any]],
        commit_rows: list[Any],
        memory_edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        components: dict[str, dict[str, Any]] = {}
        edge_weights: dict[tuple[str, str, str], dict[str, Any]] = {}
        import_targets_by_file = import_targets_for_files(code_edges)

        for file_row in files:
            component = component_for_path(str(file_row["path"]))
            node = components.setdefault(component, component_node(component))
            node["metrics"]["files"] += 1
            node["metrics"]["lines"] += int(file_row.get("line_count") or 0)

        for symbol in symbols:
            component = component_for_path(str(symbol["file_path"]))
            node = components.setdefault(component, component_node(component))
            node["metrics"]["symbols"] += 1
            kind = str(symbol["kind"]).upper()
            metric = {
                "CLASS": "classes",
                "FUNCTION": "functions",
                "METHOD": "methods",
            }.get(kind, kind.lower() + "s")
            node["metrics"][metric] = node["metrics"].get(metric, 0) + 1
            if str(symbol["name"]).lower().endswith("service"):
                node["tags"].append("service")

        for commit_row in commit_rows:
            files_touched = metadata_files(commit_row)
            author = str(commit_row["author"] or "Unknown")
            for file_path in files_touched:
                component = component_for_path(file_path)
                node = components.setdefault(component, component_node(component))
                node["metrics"]["commits"] += 1
                node["authors"].add(author)

        for row in code_edges:
            edge_type = str(row["edge_type"]).upper()
            target_component = target_component_for_code_edge(
                row,
                edge_type=edge_type,
                import_targets_by_file=import_targets_by_file,
            )
            if row.get("target_path") is None and edge_type != "IMPORTS" and target_component is None:
                continue
            source_component = component_from_edge_path(row.get("source_path"), row.get("source_label"))
            if not source_component or not target_component or source_component == target_component:
                continue
            if row.get("target_path") is None:
                components.setdefault(target_component, external_node(target_component))
            edge_key = (source_component, target_component, edge_type)
            edge = edge_weights.setdefault(
                edge_key,
                {
                    "id": "|".join(edge_key),
                    "source": source_component,
                    "target": target_component,
                    "type": str(row["edge_type"]).lower(),
                    "weight": 0,
                    "reasons": [],
                    "examples": [],
                },
            )
            edge["weight"] += 1
            if len(edge["reasons"]) < 5:
                edge["reasons"].append(str(row.get("source_label") or row.get("source_key")))
            if len(edge["examples"]) < 10:
                edge["examples"].append(
                    component_edge_example(row, target_component=target_component)
                )

        for row in memory_edges:
            metadata = parse_json(row.get("metadata_json"))
            if not metadata.get("cochange"):
                continue
            source = str(row["source_key"]).removeprefix("file:")
            target = str(row["target_key"]).removeprefix("file:")
            if source == row["source_key"] or target == row["target_key"]:
                continue
            source_component = component_for_path(source)
            target_component = component_for_path(target)
            if source_component == target_component:
                continue
            edge_key = (source_component, target_component, "cochange")
            edge = edge_weights.setdefault(
                edge_key,
                {
                    "id": "|".join(edge_key),
                    "source": source_component,
                    "target": target_component,
                    "type": "cochange",
                    "weight": 0,
                    "reasons": [],
                    "examples": [],
                },
            )
            edge["weight"] += 1
            if len(edge["reasons"]) < 5:
                edge["reasons"].append(str(metadata.get("commit", ""))[:12])
            if len(edge["examples"]) < 10:
                edge["examples"].append(
                    {
                        "type": "cochange",
                        "source": {"label": source, "path": source},
                        "target": {"label": target, "path": target},
                        "file_path": source,
                        "related_file_path": target,
                        "commit": str(metadata.get("commit", ""))[:12],
                    }
                )

        nodes = []
        for component, node in components.items():
            authors = node.pop("authors", set())
            node["metrics"]["authors"] = len(authors)
            node["tags"] = sorted(set(node["tags"]))
            node["size"] = 12 + min(30, node["metrics"]["files"] * 3 + node["metrics"]["symbols"])
            node["risk"] = component_risk(node)
            nodes.append(node)
        nodes.sort(key=lambda item: (-item["size"], item["label"]))
        edges = sorted(edge_weights.values(), key=lambda item: (-item["weight"], item["source"], item["target"]))
        return {"nodes": nodes, "edges": edges}

    def _commit_graph(self, commit_rows: list[Any]) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        recent_rows = list(reversed(commit_rows[:180]))
        for row in recent_rows:
            sha = str(row["source_id"])
            metadata = parse_json(row["metadata_json"])
            commit_id = f"commit:{sha[:12]}"
            author = str(row["author"] or "Unknown")
            author_id = f"author:{author}"
            files = metadata_files(row)
            nodes[commit_id] = {
                "id": commit_id,
                "label": str(row["title"]),
                "type": "commit",
                "timestamp": row["timestamp"],
                "size": 9 + min(18, len(files) * 2),
                "metrics": {
                    "files": len(files),
                    "risk": metadata.get("risk", "low"),
                    "architectural_impact": metadata.get("architectural_impact", "low"),
                },
                "details": str(row["snippet"]),
            }
            nodes.setdefault(
                author_id,
                {
                    "id": author_id,
                    "label": author,
                    "type": "developer",
                    "size": 18,
                    "metrics": {"commits": 0},
                    "details": "Commit author",
                },
            )
            nodes[author_id]["metrics"]["commits"] += 1
            edges.append(
                {
                    "id": f"{author_id}->{commit_id}",
                    "source": author_id,
                    "target": commit_id,
                    "type": "authored",
                    "weight": 1,
                }
            )
            for file_path in files[:20]:
                component = component_for_path(file_path)
                component_id = f"component:{component}"
                nodes.setdefault(
                    component_id,
                    {
                        "id": component_id,
                        "label": component,
                        "type": "component",
                        "size": 16,
                        "metrics": {"files": 0},
                        "details": "Component touched by commits",
                    },
                )
                nodes[component_id]["metrics"]["files"] += 1
                edges.append(
                    {
                        "id": f"{commit_id}->{component_id}:{file_path}",
                        "source": commit_id,
                        "target": component_id,
                        "type": "touched",
                        "weight": 1,
                        "file": file_path,
                    }
                )
        return {"nodes": list(nodes.values()), "edges": edges}


def create_visualization_server(
    repo_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    service = VisualizationService()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path
            if route == "/":
                self._send_text(HTML_APP, content_type="text/html")
            elif route == "/api/graph":
                payload = service.build_map(repo_root)
                self._send_json(payload)
            elif route == "/api/compare":
                query = parse_qs(parsed.query)
                base_ref = query.get("base", ["HEAD~1"])[0]
                head_ref = query.get("head", ["HEAD"])[0]
                try:
                    payload = service.build_compare(
                        repo_root,
                        base_ref=base_ref,
                        head_ref=head_ref,
                    )
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json(payload)
            elif route == "/api/health":
                self._send_json({"ok": True, "repo": str(repo_root)})
            elif route == "/api/index-status":
                try:
                    payload = index_status(repo_root)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **payload})
            elif route == "/api/routes":
                try:
                    payload = route_summary(repo_root)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **payload})
            elif route == "/api/http-confidence":
                try:
                    payload = http_confidence_summary(repo_root)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **payload})
            elif route == "/api/rules":
                try:
                    payload = cached_workflow(
                        repo_root,
                        "rules",
                        {"limit": 100, "severity": ""},
                        lambda: run_rule_checks(repo_root, limit=100),
                    )
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **payload})
            elif route == "/api/source-outline":
                try:
                    query = parse_qs(parsed.query).get("query", [""])[0]
                    payload = cached_workflow(
                        repo_root,
                        "source-outline",
                        {"query": query, "limit": 80},
                        lambda: source_outline(repo_root, query, limit=80),
                    )
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **payload})
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/refresh":
                try:
                    payload = self._read_json()
                    max_commits = int(payload.get("max_commits", 1000))
                    service.prepare(repo_root, refresh=True, max_commits=max_commits)
                    graph = service.build_map(repo_root)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **graph})
                return

            if parsed.path == "/api/compare/warm":
                try:
                    payload = self._read_json()
                    refs_payload = payload.get("refs", [])
                    refs = [str(ref) for ref in refs_payload] if isinstance(refs_payload, list) else []
                    result = service.warm_compare_cache(repo_root, refs)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **result})
                return

            if parsed.path == "/api/agent-context":
                try:
                    payload = self._read_json()
                    task = str(payload.get("task", ""))
                    max_tokens = int(payload.get("max_tokens", 5000))
                    result = service.agent_context(repo_root, task, max_tokens=max_tokens)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **result})
                return

            if parsed.path == "/api/index-status":
                try:
                    result = index_status(repo_root)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **result})
                return

            if parsed.path == "/api/classification":
                try:
                    payload = self._read_json()
                    package_name = str(payload.get("package", ""))
                    category = str(payload.get("category", ""))
                    previous = load_project_config(repo_root).public_payload()["classification"]
                    updated = update_classification_config(repo_root, package_name, category)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json(
                    {
                        "ok": True,
                        "config": updated.public_payload(),
                        "previous_classification": previous,
                        "build": {
                            "ui_version": UI_VERSION,
                            "server_started_at": SERVER_STARTED_AT,
                            "config_fingerprint": updated.fingerprint,
                            "config_path": str(updated.path) if updated.path else "",
                            **source_freshness(),
                        },
                    }
                )
                return

            if parsed.path == "/api/classification/restore":
                try:
                    payload = self._read_json()
                    classification = payload.get("classification")
                    if not isinstance(classification, dict):
                        raise ValueError("classification payload is required")
                    updated = restore_classification_config(repo_root, classification)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json(
                    {
                        "ok": True,
                        "config": updated.public_payload(),
                        "build": {
                            "ui_version": UI_VERSION,
                            "server_started_at": SERVER_STARTED_AT,
                            "config_fingerprint": updated.fingerprint,
                            "config_path": str(updated.path) if updated.path else "",
                            **source_freshness(),
                        },
                    }
                )
                return

            if parsed.path == "/api/query":
                try:
                    payload = self._read_json()
                    expression = str(payload.get("query", ""))
                    limit = int(payload.get("limit", 25))
                    result = structural_query(repo_root, expression, limit=limit)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **result})
                return

            if parsed.path == "/api/dead-code":
                try:
                    payload = self._read_json()
                    limit = int(payload.get("limit", 50))
                    result = dead_code(repo_root, limit=limit)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **result})
                return

            if parsed.path == "/api/routes":
                try:
                    result = route_summary(repo_root)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **result})
                return

            if parsed.path == "/api/http-confidence":
                try:
                    result = http_confidence_summary(repo_root)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **result})
                return

            if parsed.path == "/api/context-pack":
                try:
                    payload = self._read_json()
                    task = str(payload.get("task", ""))
                    max_tokens = int(payload.get("max_tokens", 6000))
                    output_format = str(payload.get("format", "markdown"))
                    result = cached_workflow(
                        repo_root,
                        "context-pack",
                        {"task": task, "max_tokens": max_tokens, "format": output_format},
                        lambda: context_pack(repo_root, task, max_tokens=max_tokens),
                    )
                    pack_cache = result.pop("cache", None)
                    rendered = render_context_pack(result, output_format=output_format)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, "pack": result, "rendered": rendered, "format": output_format, "cache": pack_cache})
                return

            if parsed.path == "/api/verify-plan":
                try:
                    payload = self._read_json()
                    base_ref = str(payload.get("base_ref", "HEAD"))
                    task = str(payload.get("task", ""))
                    result = cached_workflow(
                        repo_root,
                        "verify-plan",
                        {"base_ref": base_ref, "task": task},
                        lambda: verification_plan(repo_root, base_ref=base_ref, task=task),
                    )
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **result})
                return

            if parsed.path == "/api/rules":
                try:
                    payload = self._read_json()
                    limit = int(payload.get("limit", 100))
                    severity = payload.get("severity")
                    severity_text = str(severity) if severity else ""
                    result = cached_workflow(
                        repo_root,
                        "rules",
                        {"limit": limit, "severity": severity_text},
                        lambda: run_rule_checks(repo_root, limit=limit, severity=severity_text or None),
                    )
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **result})
                return

            if parsed.path == "/api/source-outline":
                try:
                    payload = self._read_json()
                    query = str(payload.get("query", ""))
                    limit = int(payload.get("limit", 80))
                    result = cached_workflow(
                        repo_root,
                        "source-outline",
                        {"query": query, "limit": limit},
                        lambda: source_outline(repo_root, query, limit=limit),
                    )
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, **result})
                return

            if parsed.path != "/api/chat":
                self.send_error(404)
                return
            try:
                payload = self._read_json()
                question = str(payload.get("question", ""))
                max_tokens = int(payload.get("max_tokens", 3000))
                answer = service.ask(repo_root, question, max_tokens=max_tokens)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json({"ok": True, **answer})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length)
            if not raw:
                return {}
            payload = json.loads(raw.decode("utf-8"))
            return payload if isinstance(payload, dict) else {}

        def _send_text(self, body: str, *, content_type: str, status: int = 200) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            self._send_text(
                json.dumps(payload, sort_keys=True),
                content_type="application/json",
                status=status,
            )

    return ThreadingHTTPServer((host, port), Handler)


def find_available_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EPERM}:
                    msg = f"Cannot bind local visualization server on {host}:{port}: permission denied."
                    raise RuntimeError(msg) from exc
                continue
            return port
    msg = f"No free port found near {preferred} on {host}."
    raise RuntimeError(msg)


def resolve_git_ref(repo_root: Path, ref: str) -> str:
    clean_ref = ref.strip()
    if not clean_ref:
        raise ValueError("Git ref cannot be empty.")
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", f"{clean_ref}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error = result.stderr.strip() or f"Could not resolve git ref {clean_ref!r}."
        raise ValueError(error)
    return result.stdout.strip()


def extract_git_archive(repo_root: Path, ref: str, destination: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "archive", "--format=tar", ref],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(error or f"Could not archive git ref {ref}.")
    destination_resolved = destination.resolve()
    with tarfile.open(fileobj=BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if destination_resolved != target and destination_resolved not in target.parents:
                raise ValueError(f"Ref archive contains unsafe path: {member.name}")
            try:
                archive.extract(member, destination, filter="data")
            except TypeError:
                archive.extract(member, destination)


def annotate_architecture_diff(
    base_graph: dict[str, Any],
    head_graph: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    base_nodes = {str(node["id"]): dict(node) for node in base_graph["nodes"]}
    head_nodes = {str(node["id"]): dict(node) for node in head_graph["nodes"]}
    base_edges = {str(edge["id"]): dict(edge) for edge in base_graph["edges"]}
    head_edges = {str(edge["id"]): dict(edge) for edge in head_graph["edges"]}

    added_nodes = set(head_nodes) - set(base_nodes)
    removed_nodes = set(base_nodes) - set(head_nodes)
    changed_nodes = {
        node_id
        for node_id in set(base_nodes) & set(head_nodes)
        if node_signature(base_nodes[node_id]) != node_signature(head_nodes[node_id])
    }
    added_edges = set(head_edges) - set(base_edges)
    removed_edges = set(base_edges) - set(head_edges)
    changed_edges = {
        edge_id
        for edge_id in set(base_edges) & set(head_edges)
        if edge_signature(base_edges[edge_id]) != edge_signature(head_edges[edge_id])
    }

    for node_id, node in base_nodes.items():
        node["change"] = "removed" if node_id in removed_nodes else "changed" if node_id in changed_nodes else "unchanged"
    for node_id, node in head_nodes.items():
        node["change"] = "added" if node_id in added_nodes else "changed" if node_id in changed_nodes else "unchanged"
    for edge_id, edge in base_edges.items():
        edge["change"] = "removed" if edge_id in removed_edges else "changed" if edge_id in changed_edges else "unchanged"
    for edge_id, edge in head_edges.items():
        edge["change"] = "added" if edge_id in added_edges else "changed" if edge_id in changed_edges else "unchanged"

    summary = {
        "added_nodes": len(added_nodes),
        "removed_nodes": len(removed_nodes),
        "changed_nodes": len(changed_nodes),
        "added_edges": len(added_edges),
        "removed_edges": len(removed_edges),
        "changed_edges": len(changed_edges),
    }
    annotated_base = {
        "nodes": sorted(base_nodes.values(), key=lambda item: (-int(item.get("size", 0)), str(item["label"]))),
        "edges": sorted(base_edges.values(), key=lambda item: (-int(item.get("weight", 0)), str(item["source"]), str(item["target"]))),
    }
    annotated_head = {
        "nodes": sorted(head_nodes.values(), key=lambda item: (-int(item.get("size", 0)), str(item["label"]))),
        "edges": sorted(head_edges.values(), key=lambda item: (-int(item.get("weight", 0)), str(item["source"]), str(item["target"]))),
    }
    return annotated_base, annotated_head, summary


def build_chat_answer(
    *,
    query: str,
    snippets: list[dict[str, Any]],
    history: list[dict[str, Any]],
    architecture: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    ownership: list[dict[str, Any]],
) -> str:
    lines = [f"Question: {query}", ""]
    if snippets:
        lines.append("Code context:")
        for snippet in snippets[:3]:
            lines.append(
                f"- {snippet['symbol']} in {snippet['file_path']}:{snippet['lines']} "
                f"({snippet['reason']})"
            )
    else:
        lines.append("Code context: no matching indexed symbols were found.")

    if history:
        lines.append("")
        lines.append("Commit and repository history:")
        for event in history[:3]:
            date = event.get("date") or "unknown date"
            lines.append(f"- {date}: {event.get('title', 'Untitled event')}")

    if architecture:
        lines.append("")
        lines.append("Architecture evidence:")
        for finding in architecture[:3]:
            lines.append(f"- {finding.get('summary', '')}")

    if decisions:
        lines.append("")
        lines.append("Decision evidence:")
        for decision in decisions[:2]:
            lines.append(f"- {decision.get('answer', '')}")

    if ownership:
        lines.append("")
        lines.append("Likely owners:")
        for owner in ownership[:3]:
            lines.append(
                f"- {owner.get('developer', 'Unknown')} "
                f"({owner.get('commits', 0)} commits, {owner.get('files_touched', 0)} files)"
            )

    lines.append("")
    lines.append("Use the code snippets and evidence below for exact inspection.")
    return "\n".join(lines)


def build_agent_context_markdown(payload: dict[str, Any]) -> str:
    code = payload.get("code") or []
    evidence = payload.get("evidence") or []
    ownership = payload.get("ownership") or []
    token_report = payload.get("token_report") or {}
    lines = [
        "# CodeAtlas Agent Context",
        "",
        f"Task: {payload.get('task', '')}",
        "",
        "## Likely Edit/Inspection Files",
    ]
    if code:
        for item in code[:8]:
            lines.append(
                f"- {item.get('file_path')}:{item.get('lines')} "
                f"- {item.get('symbol')} ({item.get('reason')})"
            )
    else:
        lines.append("- No matching indexed code snippets were found.")
    lines.extend(["", "## Context Snippets"])
    for item in code[:5]:
        language = "python" if item.get("file_path", "").endswith(".py") else ""
        lines.extend(
            [
                f"### {item.get('symbol')} - {item.get('file_path')}:{item.get('lines')}",
                f"```{language}",
                str(item.get("code") or ""),
                "```",
            ]
        )
    lines.extend(["", "## Evidence"])
    if evidence:
        for item in evidence[:8]:
            source = item.get("path") or str(item.get("source_id") or "")[:12]
            lines.append(f"- {item.get('source_type', 'evidence')} {source}: {item.get('title', '')}")
    else:
        lines.append("- No repository-memory evidence matched this task.")
    lines.extend(["", "## Likely Owners"])
    if ownership:
        for owner in ownership[:5]:
            lines.append(
                f"- {owner.get('developer', 'Unknown')}: "
                f"{owner.get('commits', 0)} commits, {owner.get('files_touched', 0)} files"
            )
    else:
        lines.append("- No ownership signal found.")
    lines.extend(
        [
            "",
            "## Verification Hints",
            "- Run the most focused tests for the files above.",
            "- Inspect direct callers/callees before editing shared functions.",
            "- Re-run `codeatlas context` or refresh the map after large edits.",
            "",
            "## Token Report",
            f"- Optimized context: {token_report.get('optimized_tokens', 0)} tokens",
            f"- Baseline estimate: {token_report.get('baseline_tokens', 0)} tokens",
            f"- Estimated savings: {token_report.get('savings_percent', 0):.0f}%",
        ]
    )
    return "\n".join(lines)


def chat_query_terms(query: str) -> tuple[str, ...]:
    raw_terms = [
        term.strip(".,:;!?()[]{}'\"`")
        for term in query.replace("/", " ").replace("_", " ").split()
    ]
    terms: list[str] = []
    for term in raw_terms:
        if len(term) < 3:
            continue
        lowered = term.lower()
        if lowered in {"the", "and", "for", "with", "from", "about", "code", "commit", "commits"}:
            continue
        terms.append(term)
    terms.sort(key=lambda item: (not any(char.isupper() for char in item), -len(item), item.lower()))
    return tuple(dict.fromkeys(terms[:8]))


def node_signature(node: dict[str, Any]) -> tuple[Any, ...]:
    return (
        node.get("type"),
        tuple(sorted(node.get("tags", ()))),
        tuple(sorted((node.get("metrics") or {}).items())),
        node.get("risk"),
    )


def import_targets_for_files(code_edges: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    targets: dict[str, dict[str, str]] = defaultdict(dict)
    for row in code_edges:
        if str(row.get("edge_type", "")).upper() != "IMPORTS":
            continue
        source_path = row.get("source_path")
        if not source_path:
            continue
        metadata = parse_json(row.get("metadata_json"))
        module = str(row.get("target_label") or "").strip()
        component = component_from_edge_path(row.get("target_path"), module)
        if not component:
            continue
        module_root = module.split(".", 1)[0] if module else ""
        for alias in (module_root, metadata.get("alias"), metadata.get("name")):
            alias_text = str(alias or "").strip()
            if alias_text:
                targets[str(source_path)][alias_text] = component
    return targets


def target_component_for_code_edge(
    row: dict[str, Any],
    *,
    edge_type: str,
    import_targets_by_file: dict[str, dict[str, str]],
) -> str | None:
    if edge_type == "CALLS" and row.get("target_path") is None:
        metadata = parse_json(row.get("metadata_json"))
        display = str(metadata.get("display") or "").strip()
        if not display:
            return None
        root = display.split(".", 1)[0]
        source_path = str(row.get("source_path") or "")
        return import_targets_by_file.get(source_path, {}).get(root)
    component = component_from_edge_path(row.get("target_path"), row.get("target_label"))
    if component is not None:
        return component
    return None


def component_edge_example(row: dict[str, Any], *, target_component: str) -> dict[str, Any]:
    metadata = parse_json(row.get("metadata_json"))
    edge_type = str(row.get("edge_type", "")).lower()
    source = edge_endpoint(row, "source")
    target = edge_endpoint(row, "target")
    display = edge_display(edge_type, row, metadata)
    arguments = edge_arguments(metadata)

    if edge_type == "calls" and display:
        target["label"] = display
        if row.get("target_path") is None:
            target["kind"] = "external api"
            target["component"] = target_component
    elif edge_type == "imports" and display:
        target["label"] = display
        target["component"] = target_component

    return {
        "type": edge_type,
        "source": source,
        "target": target,
        "display": display,
        "arguments": arguments,
        "file_path": source.get("path") or target.get("path"),
        "line": metadata.get("line") or source.get("line_start"),
    }


def edge_endpoint(row: dict[str, Any], side: str) -> dict[str, Any]:
    qualified = row.get(f"{side}_qualified_name")
    label = qualified or row.get(f"{side}_label") or row.get(f"{side}_key")
    endpoint = {
        "key": row.get(f"{side}_key"),
        "type": row.get(f"{side}_type"),
        "label": label,
        "name": row.get(f"{side}_symbol_name") or row.get(f"{side}_label"),
        "qualified_name": qualified,
        "kind": row.get(f"{side}_symbol_kind") or row.get(f"{side}_type"),
        "module": row.get(f"{side}_module"),
        "signature": row.get(f"{side}_signature"),
        "path": row.get(f"{side}_path"),
        "line_start": row.get(f"{side}_line_start"),
        "line_end": row.get(f"{side}_line_end"),
    }
    return {key: value for key, value in endpoint.items() if value not in (None, "")}


def edge_display(edge_type: str, row: dict[str, Any], metadata: dict[str, Any]) -> str:
    if edge_type == "imports":
        module = str(row.get("target_label") or "")
        name = str(metadata.get("name") or "")
        alias = str(metadata.get("alias") or "")
        display = f"{module}.{name}" if module and name else module or name
        return f"{display} as {alias}" if alias else display
    for key in ("display", "import"):
        value = metadata.get(key)
        if value:
            return str(value)
    return str(row.get("target_qualified_name") or row.get("target_label") or "")


def edge_arguments(metadata: dict[str, Any]) -> list[str]:
    arguments = metadata.get("arguments")
    if not isinstance(arguments, list):
        return []
    return [str(argument) for argument in arguments if str(argument).strip()]


def edge_example_signature(example: dict[str, Any]) -> tuple[Any, ...]:
    source = example.get("source") or {}
    target = example.get("target") or {}
    return (
        example.get("type"),
        source.get("qualified_name") or source.get("label"),
        target.get("qualified_name") or target.get("label"),
        example.get("display"),
        tuple(example.get("arguments") or ()),
        example.get("file_path"),
        example.get("line"),
    )


def edge_signature(edge: dict[str, Any]) -> tuple[Any, ...]:
    return (
        edge.get("source"),
        edge.get("target"),
        edge.get("type"),
        edge.get("weight"),
        tuple(edge_example_signature(example) for example in edge.get("examples", ())[:20]),
    )


def component_node(component: str) -> dict[str, Any]:
    return {
        "id": component,
        "label": component,
        "type": "component",
        "tags": [],
        "authors": set(),
        "metrics": {
            "files": 0,
            "lines": 0,
            "symbols": 0,
            "classes": 0,
            "functions": 0,
            "methods": 0,
            "commits": 0,
            "authors": 0,
        },
    }


def external_node(label: str) -> dict[str, Any]:
    node = component_node(label)
    node["type"] = "external"
    node["tags"] = ["external"]
    return node


def compact_file_inventory(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for row in files:
        path = str(row.get("path") or "")
        items.append(
            {
                "path": path,
                "component": component_for_path(path),
                "language": str(row.get("language") or ""),
                "lines": int(row.get("line_count") or 0),
                "size_bytes": int(row.get("size_bytes") or 0),
            }
        )
    return sorted(items, key=lambda item: item["path"])


def compact_symbol_inventory(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for row in symbols:
        file_path = str(row.get("file_path") or "")
        items.append(
            {
                "name": str(row.get("name") or ""),
                "qualified_name": str(row.get("qualified_name") or ""),
                "kind": str(row.get("kind") or ""),
                "module": str(row.get("module") or ""),
                "file_path": file_path,
                "component": component_for_path(file_path),
                "line_start": int(row.get("line_start") or 0),
                "line_end": int(row.get("line_end") or 0),
                "signature": str(row.get("signature") or ""),
            }
        )
    return sorted(items, key=lambda item: (item["qualified_name"], item["file_path"]))


def compact_commit_inventory(commit_rows: list[Any]) -> list[dict[str, Any]]:
    items = []
    for row in commit_rows:
        metadata = parse_json(row["metadata_json"])
        files = metadata_files(row)
        sha = str(row["source_id"])
        items.append(
            {
                "sha": sha,
                "short_sha": sha[:12],
                "title": str(row["title"]),
                "author": str(row["author"] or "Unknown"),
                "timestamp": str(row["timestamp"] or ""),
                "files": len(files),
                "risk": str(metadata.get("risk", "low")),
                "architectural_impact": str(metadata.get("architectural_impact", "low")),
                "snippet": str(row["snippet"] or ""),
            }
        )
    return items


def compact_index_diagnostics(
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    code_edges: list[dict[str, Any]],
    stats: dict[str, Any],
    *,
    supported_languages: Any,
    index_report: Any,
) -> dict[str, Any]:
    language_counts: dict[str, int] = {}
    for row in files:
        language = str(row.get("language") or "unknown")
        language_counts[language] = language_counts.get(language, 0) + 1
    symbol_files = {str(row.get("file_path") or "") for row in symbols}
    files_without_symbols = [
        str(row.get("path") or "")
        for row in files
        if str(row.get("path") or "") not in symbol_files
    ]
    unresolved_calls = [
        row
        for row in code_edges
        if str(row.get("edge_type") or "").upper() == "CALLS" and row.get("target_path") is None
    ]
    external_dependencies = {
        str(row.get("target_label") or row.get("target_key") or "")
        for row in code_edges
        if row.get("target_path") is None
    }
    report = index_report if isinstance(index_report, dict) else {}
    parser_errors = report.get("parser_errors") if isinstance(report.get("parser_errors"), list) else []
    files_skipped = int(report.get("files_skipped") or 0)
    last_indexed = str(stats.get("last_indexed_at") or "")
    stale = False
    if last_indexed:
        try:
            indexed_at = datetime.fromisoformat(last_indexed.replace("Z", "+00:00"))
            stale = (datetime.now(UTC) - indexed_at).total_seconds() > 60 * 60 * 24
        except ValueError:
            stale = False
    edge_count = int(stats.get("component_edges") or 0)
    node_count = int(stats.get("components") or 0)
    suggestions = []
    if files_without_symbols:
        suggestions.append(
            f"{len(files_without_symbols)} indexed file(s) have no parsed symbols; check parser coverage or generated/config files."
        )
    if unresolved_calls:
        suggestions.append(
            f"{len(unresolved_calls)} call edge(s) resolve to external or unknown targets; use evidence cards before relying on them."
        )
    if parser_errors:
        suggestions.append(f"{len(parser_errors)} parser error(s) were recorded during the last index.")
    if files_skipped:
        suggestions.append(f"{files_skipped} file(s) were skipped in the last incremental index.")
    if stale:
        suggestions.append("Index is older than 24 hours; refresh before making risky changes.")
    if node_count > 180 or edge_count > 280:
        suggestions.append("Large graph detected; use Overview, Trace, or API/Data lenses for progressive disclosure.")
    if not suggestions:
        suggestions.append("Index health looks good for the currently supported languages.")
    return {
        "supported_languages": [str(language) for language in supported_languages or []],
        "language_counts": dict(sorted(language_counts.items())),
        "files_without_symbols": len(files_without_symbols),
        "sample_files_without_symbols": files_without_symbols[:8],
        "files_skipped": files_skipped,
        "parser_errors": len(parser_errors),
        "sample_parser_errors": parser_errors[:5],
        "unresolved_calls": len(unresolved_calls),
        "external_dependencies": len([value for value in external_dependencies if value]),
        "sample_external_dependencies": sorted(value for value in external_dependencies if value)[:8],
        "last_indexed_at": last_indexed,
        "stale": stale,
        "symbols_per_file": round(len(symbols) / len(files), 2) if files else 0,
        "graph_density": round(edge_count / max(node_count, 1), 2),
        "suggestions": suggestions,
    }


def compare_snapshot_path(repo_root: Path, sha: str) -> Path:
    return CodeAtlasPaths(repo_root).cache_dir / "compare" / f"{sha}.json"


def load_compare_snapshot(repo_root: Path, sha: str) -> dict[str, Any] | None:
    path = compare_snapshot_path(repo_root, sha)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != COMPARE_CACHE_VERSION:
        return None
    if payload.get("sha") != sha:
        return None
    if "component_graph" not in payload or "stats" not in payload:
        return None
    return payload


def write_compare_snapshot(repo_root: Path, sha: str, payload: dict[str, Any]) -> None:
    path = compare_snapshot_path(repo_root, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def component_from_edge_path(path: Any, label: Any) -> str | None:
    if path:
        return component_for_path(str(path))
    if label:
        text = str(label).strip()
        if text and not text.startswith("."):
            return text.split(".")[0]
    return None


def component_risk(node: dict[str, Any]) -> str:
    metrics = node["metrics"]
    if metrics["commits"] >= 25 or metrics["authors"] >= 8 or "service" in node["tags"]:
        return "high"
    if metrics["commits"] >= 8 or metrics["files"] >= 8:
        return "medium"
    return "low"


def load_visualization_asset(name: str) -> str:
    return (ASSET_DIR / name).read_text(encoding="utf-8")


def render_visualization_app() -> str:
    css = load_visualization_asset("visualization.css").rstrip()
    js = load_visualization_asset("visualization.js").replace("{{ UI_VERSION }}", UI_VERSION).rstrip()
    template = load_visualization_asset("visualization.html")
    return template.replace("{{ CODEATLAS_CSS }}", css).replace("{{ CODEATLAS_JS }}", js)


HTML_APP = render_visualization_app()
