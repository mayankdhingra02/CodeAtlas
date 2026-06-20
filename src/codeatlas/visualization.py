from __future__ import annotations

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
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import CodeAtlasPaths, resolve_repo_root
from .indexer import RepositoryIndexer
from .memory import MemoryQueryEngine, MemoryStore, component_for_path, metadata_files, parse_json
from .retrieval import RetrievalEngine
from .storage import GraphStore


class VisualizationService:
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

            component_graph = self._component_graph(files, symbols, code_edges, commit_rows, memory_edges)
            commit_graph = self._commit_graph(commit_rows)
            stats = {
                "files": len(files),
                "symbols": len(symbols),
                "components": len(component_graph["nodes"]),
                "component_edges": len(component_graph["edges"]),
                "commits": len(commit_rows),
                "generated_at": datetime.now(UTC).isoformat(),
            }
            return {
                "repo": {
                    "name": repo_root.name,
                    "path": str(repo_root),
                    "database": str(paths.database_path),
                },
                "stats": stats,
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
        with tempfile.TemporaryDirectory(prefix="codeatlas-compare-") as temp_name:
            temp_root = Path(temp_name)
            base_root = temp_root / "base"
            head_root = temp_root / "head"
            base_root.mkdir()
            head_root.mkdir()
            extract_git_archive(repo_root, base_sha, base_root)
            extract_git_archive(repo_root, head_sha, head_root)
            RepositoryIndexer().index(base_root, incremental=False)
            RepositoryIndexer().index(head_root, incremental=False)
            base_map = self.build_map(base_root)
            head_map = self.build_map(head_root)

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
            },
        }

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
        if not code_snippets:
            seen_symbols: set[str] = set()
            for term in chat_query_terms(query):
                fallback = retrieval.retrieve(repo_root, term, depth=2, max_tokens=max_tokens)
                for snippet in fallback.snippets:
                    if snippet.qualified_name in seen_symbols:
                        continue
                    seen_symbols.add(snippet.qualified_name)
                    code_snippets.append(snippet)
                if len(code_snippets) >= 5:
                    break
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
            except OSError:
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


HTML_APP = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CodeAtlas Map</title>
  <style>
    :root {
      --bg: #0f1115;
      --panel: #171a21;
      --panel2: #20242d;
      --text: #eef1f5;
      --muted: #9ba7b6;
      --line: #343a46;
      --blue: #77a7ff;
      --green: #71d49b;
      --amber: #e4b363;
      --red: #ef7b7b;
      --violet: #b69cff;
      --soft-red: rgba(239, 123, 123, .16);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; background: var(--bg); color: var(--text); font: 14px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { overflow: hidden; }
    #app { display: grid; grid-template-columns: 280px minmax(0, 1fr) 330px; grid-template-rows: 54px 1fr; height: 100%; }
    header { grid-column: 1 / 4; display: flex; align-items: center; gap: 12px; padding: 0 14px; border-bottom: 1px solid var(--line); background: #12151b; }
    h1 { margin: 0; font-size: 16px; font-weight: 650; }
    .meta { color: var(--muted); font-size: 12px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .toolbar { margin-left: auto; display: flex; align-items: center; gap: 8px; }
    button, input, select, textarea { background: var(--panel2); color: var(--text); border: 1px solid var(--line); border-radius: 6px; }
    input, select { height: 34px; padding: 0 10px; }
    textarea { width: 100%; min-height: 72px; padding: 8px 10px; resize: vertical; font: inherit; }
    button { cursor: pointer; }
    .icon-btn { width: 34px; height: 34px; padding: 0; font-size: 18px; line-height: 1; }
    button.active { border-color: var(--blue); color: white; background: #23304a; }
    input, select { width: 220px; outline: none; }
    .compare-picker { display: none; align-items: center; gap: 6px; min-width: 0; }
    .compare-picker.active { display: flex; }
    .compare-picker select { width: clamp(130px, 16vw, 230px); }
    .compare-picker button { height: 34px; }
    main { position: relative; min-width: 0; min-height: 0; }
    canvas { width: 100%; height: 100%; display: block; touch-action: none; cursor: grab; }
    canvas.panning { cursor: grabbing; }
    aside { background: var(--panel); overflow: auto; padding: 14px; }
    .filter-panel { border-right: 1px solid var(--line); }
    .detail-panel { border-left: 1px solid var(--line); }
    .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 14px; }
    .stat { background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: 8px; }
    .stat span { display: block; color: var(--muted); font-size: 11px; }
    .stat strong { font-size: 18px; }
    .section-title { margin: 16px 0 8px; color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .08em; }
    .pill { display: inline-flex; border: 1px solid var(--line); background: var(--panel2); border-radius: 999px; padding: 3px 8px; margin: 0 5px 5px 0; color: var(--muted); font-size: 12px; cursor: pointer; }
    .details { color: var(--muted); }
    .detail-stack { display: grid; gap: 8px; }
    .detail-card { border: 1px solid var(--line); background: #151922; border-radius: 6px; overflow: hidden; }
    .detail-card summary { cursor: pointer; padding: 8px 10px; color: var(--text); font-weight: 700; }
    .detail-card[open] summary { color: var(--blue); }
    .detail-body { border-top: 1px solid var(--line); padding: 8px 10px 10px; }
    .detail-lines { white-space: pre-wrap; }
    .detail-nested { margin-top: 8px; background: rgba(32, 36, 45, .55); }
    .detail-nested summary { color: var(--amber); font-size: 12px; }
    .detail-empty { color: var(--muted); font-size: 12px; }
    .details .detail-key { color: var(--blue); font-weight: 650; }
    .details .detail-section { color: var(--text); font-weight: 700; }
    .details .detail-edge { color: var(--amber); font-weight: 650; }
    .details .detail-call { color: var(--green); }
    .details .detail-signature { color: var(--violet); }
    .details .detail-path { color: #8fc7ff; }
    .details .detail-change { color: var(--red); }
    .details .detail-value { color: var(--text); }
    .chat-box { display: grid; gap: 8px; }
    .chat-actions { display: flex; gap: 8px; align-items: center; }
    .chat-actions button { flex: 0 0 auto; }
    .chat-status { color: var(--muted); font-size: 12px; }
    .chat-answer { white-space: pre-wrap; color: var(--text); background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: 10px; min-height: 72px; }
    .chat-section { margin-top: 8px; }
    .chat-item { border: 1px solid var(--line); background: #151922; border-radius: 6px; padding: 8px; margin-top: 6px; color: var(--muted); font-size: 12px; }
    .chat-item strong { color: var(--text); font-size: 13px; }
    .filter-tools { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 10px 0; }
    .filter-tools button { width: 100%; }
    .filter-toggle { display: flex; align-items: center; gap: 8px; color: var(--text); margin: 8px 0 10px; }
    .filter-toggle input, .filter-row input { width: 16px; height: 16px; accent-color: var(--blue); }
    .filter-search { width: 100%; margin-bottom: 4px; }
    .compare-controls { display: grid; gap: 8px; margin-top: 10px; }
    .compare-controls input { width: 100%; }
    .compare-controls button { width: 100%; }
    .compare-status { color: var(--muted); font-size: 12px; min-height: 18px; }
    .filter-summary { color: var(--muted); font-size: 12px; margin-top: 4px; }
    .filter-list { display: grid; gap: 4px; margin-top: 8px; }
    .filter-row { display: grid; grid-template-columns: 18px 10px minmax(0, 1fr) auto; align-items: center; gap: 8px; min-height: 30px; color: var(--text); }
    .filter-row.disabled { opacity: .45; }
    .filter-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .filter-kind { color: var(--muted); font-size: 11px; }
    .swatch { width: 9px; height: 9px; border-radius: 50%; }
    .legend { position: absolute; left: 14px; bottom: 14px; display: flex; gap: 8px; flex-wrap: wrap; max-width: calc(100% - 28px); }
    .legend span { background: rgba(23, 26, 33, .9); border: 1px solid var(--line); border-radius: 999px; padding: 5px 8px; color: var(--muted); font-size: 12px; }
    .error-text { color: var(--red); }
    @media (max-width: 860px) {
      #app { grid-template-columns: 1fr; grid-template-rows: 54px 220px minmax(0, 1fr) 280px; }
      header, aside, main { grid-column: 1; }
      .filter-panel, .detail-panel { border-left: 0; border-right: 0; border-top: 1px solid var(--line); }
      input, select { width: 140px; }
      .compare-picker select { width: 120px; }
    }
  </style>
</head>
<body>
  <div id="app">
    <header>
      <h1>CodeAtlas Map</h1>
      <div id="repoMeta" class="meta">Loading repository graph...</div>
      <div class="toolbar">
        <button id="refreshBtn">Refresh</button>
        <button id="architectureBtn" class="active">Architecture</button>
        <button id="commitsBtn">Commits</button>
        <button id="compareViewBtn">Compare</button>
        <div id="topCompareControls" class="compare-picker">
          <select id="baseCommitSelect" aria-label="Base commit"></select>
          <select id="headCommitSelect" aria-label="Head commit"></select>
          <button id="runTopCompareBtn">Compare</button>
        </div>
        <button id="zoomOutBtn" class="icon-btn" title="Zoom out" aria-label="Zoom out">-</button>
        <button id="zoomInBtn" class="icon-btn" title="Zoom in" aria-label="Zoom in">+</button>
        <input id="searchInput" placeholder="Filter nodes">
      </div>
    </header>
    <aside class="filter-panel">
      <div class="section-title">Filters</div>
      <label class="filter-toggle">
        <input id="showCommonInput" type="checkbox">
        <span>Common</span>
      </label>
      <div class="filter-tools">
        <button id="showAllBtn">All</button>
        <button id="hideAllBtn">None</button>
      </div>
      <div class="section-title">Compare</div>
      <div class="compare-controls">
        <input id="baseRefInput" value="HEAD~1" aria-label="Base commit">
        <input id="headRefInput" value="HEAD" aria-label="Head commit">
        <button id="runCompareBtn">Compare</button>
        <div id="compareStatus" class="compare-status"></div>
      </div>
      <input id="componentFilterInput" class="filter-search" placeholder="Find component">
      <div id="filterSummary" class="filter-summary">0 of 0 visible</div>
      <div class="section-title">Components</div>
      <div id="componentFilters" class="filter-list"></div>
    </aside>
    <main>
      <canvas id="graphCanvas"></canvas>
      <div class="legend">
        <span>Blue: component</span>
        <span>Green: developer</span>
        <span>Amber: commit</span>
        <span>Violet: external</span>
        <span>Red: changed</span>
        <span>Node and edge details</span>
      </div>
    </main>
    <aside class="detail-panel">
      <div class="stats" id="stats"></div>
      <div class="section-title">Selection</div>
      <h2 id="selectionTitle">Nothing selected</h2>
      <div id="selectionMeta" class="details"></div>
      <div class="section-title">Ask</div>
      <div class="chat-box">
        <textarea id="chatQuestion" placeholder="Ask about code or commits"></textarea>
        <div class="chat-actions">
          <button id="askBtn">Ask</button>
          <span id="chatStatus" class="chat-status"></span>
        </div>
        <div id="chatAnswer" class="chat-answer"></div>
        <div id="chatSources" class="chat-section"></div>
      </div>
      <div class="section-title">Top Connections</div>
      <div id="topEdges"></div>
    </aside>
  </div>
  <script>
    const canvas = document.getElementById('graphCanvas');
    const ctx = canvas.getContext('2d');
    const COMMON_NODE_IDS = new Set([
      '.gitignore', '__init__.py', 'abc', 'ast', 'collections', 'dataclasses',
      'datetime', 'dateutil', 'docs', 'enum', 'hashlib', 'http', 'json', 'mcp',
      'networkx', 'operator', 'os', 'pathlib', 'pyproject.toml', 're', 'readme.md',
      'rich', 'shutil', 'socket', 'sqlite3', 'subprocess', 'tempfile', 'textwrap', 'threading',
      'time', 'tree_sitter', 'tree_sitter_language_pack', 'typer', 'typing',
      'unittest', 'urllib', 'watchdog', 'webbrowser'
    ]);
    const state = {
      raw: null,
      view: 'architecture',
      compare: null,
      commitOptions: [],
      zoom: 1,
      panX: 0,
      panY: 0,
      isPanning: false,
      panStartX: 0,
      panStartY: 0,
      panBaseX: 0,
      panBaseY: 0,
      suppressClick: false,
      allNodes: [],
      allEdges: [],
      nodes: [],
      edges: [],
      selected: null,
      search: '',
      componentFilter: '',
      hiddenNodeIds: new Set(),
      showCommon: false
    };

    function setActiveViewButton() {
      document.getElementById('architectureBtn').classList.toggle('active', state.view === 'architecture');
      document.getElementById('commitsBtn').classList.toggle('active', state.view === 'commits');
      document.getElementById('compareViewBtn').classList.toggle('active', state.view === 'compare');
      document.getElementById('topCompareControls').classList.toggle('active', state.view === 'compare');
    }

    loadGraph();

    document.getElementById('refreshBtn').onclick = () => refreshGraph();
    document.getElementById('architectureBtn').onclick = () => setGraph('architecture');
    document.getElementById('commitsBtn').onclick = () => setGraph('commits');
    document.getElementById('compareViewBtn').onclick = () => setGraph('compare');
    document.getElementById('runCompareBtn').onclick = () => runCompare();
    document.getElementById('runTopCompareBtn').onclick = () => runCompare();
    document.getElementById('baseCommitSelect').onchange = event => syncCommitSelectToInput(event.target, 'baseRefInput');
    document.getElementById('headCommitSelect').onchange = event => syncCommitSelectToInput(event.target, 'headRefInput');
    document.getElementById('askBtn').onclick = () => askQuestion();
    document.getElementById('chatQuestion').addEventListener('keydown', event => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') askQuestion();
    });
    document.getElementById('zoomOutBtn').onclick = () => setZoom(state.zoom / 1.2);
    document.getElementById('zoomInBtn').onclick = () => setZoom(state.zoom * 1.2);
    canvas.addEventListener('wheel', handleGraphWheel, { passive: false });
    canvas.addEventListener('gesturestart', handleGraphGestureStart, { passive: false });
    canvas.addEventListener('gesturechange', handleGraphGestureChange, { passive: false });
    canvas.addEventListener('pointerdown', handleGraphPointerDown);
    window.addEventListener('pointermove', handleGraphPointerMove);
    window.addEventListener('pointerup', handleGraphPointerEnd);
    window.addEventListener('pointercancel', handleGraphPointerEnd);
    document.getElementById('searchInput').oninput = event => {
      state.search = event.target.value.toLowerCase();
    };
    document.getElementById('showCommonInput').onchange = event => {
      state.showCommon = event.target.checked;
      applyFilters();
      renderFilterControls();
    };
    document.getElementById('componentFilterInput').oninput = event => {
      state.componentFilter = event.target.value.toLowerCase();
      renderFilterControls();
    };
    document.getElementById('showAllBtn').onclick = () => {
      state.hiddenNodeIds.clear();
      applyFilters();
      renderFilterControls();
    };
    document.getElementById('hideAllBtn').onclick = () => {
      for (const node of state.allNodes) {
        if (!isCommonNode(node)) state.hiddenNodeIds.add(node.id);
      }
      applyFilters();
      renderFilterControls();
    };
    canvas.addEventListener('click', event => {
      if (state.suppressClick) {
        state.suppressClick = false;
        return;
      }
      if (!state.nodes.length) {
        state.selected = null;
        renderSelection(null);
        return;
      }
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      if (state.view === 'compare' && state.compare) {
        handleCompareClick(x, y);
        return;
      }
      const transform = graphTransform();
      const nodesById = new Map(state.nodes.map(node => [node.id, node]));
      let bestNode = null;
      let bestNodeDist = Infinity;
      for (const node of state.nodes) {
        const p = project(node, transform);
        const dx = p.x - x;
        const dy = p.y - y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < p.r + 8 && dist < bestNodeDist) {
          bestNode = node;
          bestNodeDist = dist;
        }
      }
      if (bestNode) {
        state.selected = { kind: 'node', node: bestNode };
        renderSelection(state.selected);
        return;
      }
      let bestEdge = null;
      let bestEdgeDist = Infinity;
      for (const edge of state.edges) {
        const a = nodesById.get(edge.source);
        const b = nodesById.get(edge.target);
        if (!a || !b) continue;
        const pa = project(a, transform);
        const pb = project(b, transform);
        const dist = distanceToSegment(x, y, pa.x, pa.y, pb.x, pb.y);
        if (dist < 11 && dist < bestEdgeDist) {
          bestEdge = edge;
          bestEdgeDist = dist;
        }
      }
      state.selected = bestEdge ? { kind: 'edge', edge: bestEdge } : null;
      renderSelection(state.selected);
    });

    function handleCompareClick(x, y) {
      const rects = compareRects(canvas.clientWidth, canvas.clientHeight);
      const baseHit = hitGraphPanel(x, y, state.compare.base, rects.base, 'base');
      const headHit = hitGraphPanel(x, y, state.compare.head, rects.head, 'head');
      const hits = [baseHit, headHit].filter(Boolean).sort((a, b) => a.distance - b.distance);
      state.selected = hits.length ? hits[0].selection : null;
      renderSelection(state.selected);
    }

    function hitGraphPanel(x, y, side, rect, sideName) {
      if (x < rect.x || x > rect.x + rect.w || y < rect.y || y > rect.y + rect.h) return null;
      if (!side.nodes.length) return null;
      const transform = graphTransformFor(side.nodes, rect);
      const nodesById = new Map(side.nodes.map(node => [node.id, node]));
      let bestNode = null;
      let bestNodeDist = Infinity;
      for (const node of side.nodes) {
        const p = projectInRect(node, transform, rect);
        const dist = Math.hypot(p.x - x, p.y - y);
        if (dist < p.r + 8 && dist < bestNodeDist) {
          bestNode = node;
          bestNodeDist = dist;
        }
      }
      if (bestNode) {
        return { distance: bestNodeDist, selection: { kind: 'node', node: bestNode, side: sideName } };
      }
      let bestEdge = null;
      let bestEdgeDist = Infinity;
      for (const edge of side.edges) {
        const a = nodesById.get(edge.source);
        const b = nodesById.get(edge.target);
        if (!a || !b) continue;
        const pa = projectInRect(a, transform, rect);
        const pb = projectInRect(b, transform, rect);
        const dist = distanceToSegment(x, y, pa.x, pa.y, pb.x, pb.y);
        if (dist < 11 && dist < bestEdgeDist) {
          bestEdge = edge;
          bestEdgeDist = dist;
        }
      }
      return bestEdge
        ? { distance: bestEdgeDist, selection: { kind: 'edge', edge: bestEdge, side: sideName } }
        : null;
    }

    function loadGraph() {
      fetch('/api/graph')
        .then(r => r.json())
        .then(data => {
          applyGraphPayload(data, 'architecture');
          requestAnimationFrame(tick);
        })
        .catch(err => {
          document.getElementById('repoMeta').textContent = 'Failed to load graph: ' + err.message;
        });
    }

    function refreshGraph() {
      const button = document.getElementById('refreshBtn');
      const previousText = button.textContent;
      const nextView = state.view === 'commits' ? 'commits' : 'architecture';
      button.disabled = true;
      button.textContent = 'Refreshing...';
      setRepoStatus('Refreshing index and graph...');
      fetch('/api/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_commits: 1000 })
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Refresh failed');
          return payload;
        })
        .then(payload => {
          state.compare = null;
          document.getElementById('compareStatus').textContent = '';
          applyGraphPayload(payload, nextView);
        })
        .catch(err => {
          setRepoStatus('Refresh failed: ' + err.message);
        })
        .finally(() => {
          button.disabled = false;
          button.textContent = previousText;
        });
    }

    function applyGraphPayload(payload, view) {
      state.raw = payload;
      populateCommitSelectors(payload);
      setRepoStatus(payload.repo.name + ' - ' + payload.repo.path);
      renderStats(payload.stats);
      setGraph(view || state.view || 'architecture');
    }

    function setRepoStatus(text) {
      document.getElementById('repoMeta').textContent = text;
    }

    function populateCommitSelectors(payload) {
      const commits = commitOptionsFromPayload(payload);
      state.commitOptions = commits;
      const baseSelect = document.getElementById('baseCommitSelect');
      const headSelect = document.getElementById('headCommitSelect');
      fillCommitSelect(baseSelect, commits);
      fillCommitSelect(headSelect, commits);

      const baseInput = document.getElementById('baseRefInput');
      const headInput = document.getElementById('headRefInput');
      const defaultBase = commits[1] ? commits[1].ref : commits[0] ? commits[0].ref : 'HEAD~1';
      const defaultHead = commits[0] ? commits[0].ref : 'HEAD';
      baseSelect.value = matchingCommitRef(baseInput.value, commits) || defaultBase;
      headSelect.value = matchingCommitRef(headInput.value, commits) || defaultHead;
      syncCommitSelectToInput(baseSelect, 'baseRefInput');
      syncCommitSelectToInput(headSelect, 'headRefInput');
    }

    function commitOptionsFromPayload(payload) {
      return (payload.commit_graph.nodes || [])
        .filter(node => node.type === 'commit')
        .map(node => ({
          ref: node.id.replace(/^commit:/, ''),
          title: node.label || 'Untitled commit',
          timestamp: node.timestamp || '',
          files: node.metrics ? node.metrics.files || 0 : 0
        }))
        .sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)));
    }

    function fillCommitSelect(select, commits) {
      select.innerHTML = '';
      for (const commit of commits) {
        const option = document.createElement('option');
        option.value = commit.ref;
        option.textContent = commitOptionLabel(commit);
        select.appendChild(option);
      }
      select.disabled = commits.length === 0;
    }

    function commitOptionLabel(commit) {
      const date = commit.timestamp ? commit.timestamp.slice(0, 10) + ' ' : '';
      return date + commit.ref + ' ' + truncateText(commit.title, 54);
    }

    function matchingCommitRef(value, commits) {
      const clean = String(value || '').trim();
      if (!clean) return '';
      const match = commits.find(commit => clean === commit.ref || clean.startsWith(commit.ref) || commit.ref.startsWith(clean));
      return match ? match.ref : '';
    }

    function syncCommitSelectToInput(select, inputId) {
      const input = document.getElementById(inputId);
      if (!select.disabled && select.value) input.value = select.value;
    }

    function truncateText(value, length) {
      const text = String(value || '');
      return text.length > length ? text.slice(0, length - 1) + '...' : text;
    }

    function runCompare() {
      const base = document.getElementById('baseRefInput').value.trim() || 'HEAD~1';
      const head = document.getElementById('headRefInput').value.trim() || 'HEAD';
      const status = document.getElementById('compareStatus');
      status.textContent = 'Building snapshots...';
      status.classList.remove('error-text');
      fetch('/api/compare?base=' + encodeURIComponent(base) + '&head=' + encodeURIComponent(head))
        .then(async response => {
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || 'Compare failed');
          return payload;
        })
        .then(payload => {
          state.compare = normalizeComparePayload(payload);
          status.textContent = compareSummaryText(payload.summary);
          setGraph('compare');
        })
        .catch(err => {
          status.textContent = err.message;
          status.classList.add('error-text');
        });
    }

    function askQuestion() {
      const question = document.getElementById('chatQuestion').value.trim();
      const status = document.getElementById('chatStatus');
      const answerRoot = document.getElementById('chatAnswer');
      const sourcesRoot = document.getElementById('chatSources');
      if (!question) return;
      status.textContent = 'Thinking...';
      status.classList.remove('error-text');
      answerRoot.textContent = '';
      sourcesRoot.innerHTML = '';
      fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, max_tokens: 3000 })
      })
        .then(async response => {
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Ask failed');
          return payload;
        })
        .then(payload => {
          status.textContent = payload.estimated_context_tokens + ' context tokens';
          answerRoot.textContent = payload.answer;
          renderChatSources(payload);
        })
        .catch(err => {
          status.textContent = err.message;
          status.classList.add('error-text');
        });
    }

    function renderChatSources(payload) {
      const root = document.getElementById('chatSources');
      root.innerHTML = '';
      appendChatGroup(root, 'Code', payload.code || [], item =>
        '<strong>' + escapeHtml(item.symbol) + '</strong><br>' +
        escapeHtml(item.file_path + ':' + item.lines + ' - ' + item.reason)
      );
      appendChatGroup(root, 'Commits / Docs', payload.evidence || [], item =>
        '<strong>' + escapeHtml(item.title || item.source_id || 'Evidence') + '</strong><br>' +
        escapeHtml((item.source_type || 'evidence') + ' ' + (item.path || item.source_id || '')) + '<br>' +
        escapeHtml(item.snippet || '')
      );
      appendChatGroup(root, 'Owners', payload.ownership || [], item =>
        '<strong>' + escapeHtml(item.developer || 'Unknown') + '</strong><br>' +
        escapeHtml((item.commits || 0) + ' commits, ' + (item.files_touched || 0) + ' files')
      );
    }

    function appendChatGroup(root, title, items, renderItem) {
      if (!items.length) return;
      const label = document.createElement('div');
      label.className = 'section-title';
      label.textContent = title;
      root.appendChild(label);
      for (const item of items.slice(0, 5)) {
        const div = document.createElement('div');
        div.className = 'chat-item';
        div.innerHTML = renderItem(item);
        root.appendChild(div);
      }
    }

    function escapeHtml(value) {
      return String(value || '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }

    function setZoom(value, anchor) {
      const previous = state.zoom;
      const next = clamp(value, 0.35, 3.2);
      if (anchor && previous > 0 && next !== previous) {
        const center = zoomAnchorCenter(anchor);
        const localX = anchor.x - center.x;
        const localY = anchor.y - center.y;
        const ratio = next / previous;
        state.panX = localX - (localX - state.panX) * ratio;
        state.panY = localY - (localY - state.panY) * ratio;
      }
      state.zoom = next;
    }

    function handleGraphWheel(event) {
      event.preventDefault();
      event.stopPropagation();
      const intensity = event.ctrlKey || event.metaKey ? 0.01 : 0.0025;
      const delta = clamp(event.deltaY, -140, 140);
      setZoom(state.zoom * Math.exp(-delta * intensity), canvasPoint(event));
    }

    let gestureStartZoom = 1;
    let gestureStartPoint = null;

    function handleGraphGestureStart(event) {
      event.preventDefault();
      event.stopPropagation();
      gestureStartZoom = state.zoom;
      gestureStartPoint = canvasPoint(event);
    }

    function handleGraphGestureChange(event) {
      event.preventDefault();
      event.stopPropagation();
      setZoom(gestureStartZoom * event.scale, gestureStartPoint || canvasPoint(event));
    }

    function handleGraphPointerDown(event) {
      if (event.button !== 0) return;
      state.isPanning = true;
      state.panStartX = event.clientX;
      state.panStartY = event.clientY;
      state.panBaseX = state.panX;
      state.panBaseY = state.panY;
      state.suppressClick = false;
      canvas.classList.add('panning');
      try {
        canvas.setPointerCapture(event.pointerId);
      } catch (err) {
        // Some browsers skip pointer capture for synthetic events.
      }
    }

    function handleGraphPointerMove(event) {
      if (!state.isPanning) return;
      event.preventDefault();
      const dx = event.clientX - state.panStartX;
      const dy = event.clientY - state.panStartY;
      if (Math.hypot(dx, dy) > 3) state.suppressClick = true;
      state.panX = state.panBaseX + dx;
      state.panY = state.panBaseY + dy;
    }

    function handleGraphPointerEnd(event) {
      if (!state.isPanning) return;
      state.isPanning = false;
      canvas.classList.remove('panning');
      try {
        canvas.releasePointerCapture(event.pointerId);
      } catch (err) {
        // Pointer capture may already be released.
      }
    }

    function canvasPoint(event) {
      const rect = canvas.getBoundingClientRect();
      return {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top
      };
    }

    function zoomAnchorCenter(anchor) {
      if (state.view === 'compare' && state.compare) {
        const rects = compareRects(canvas.clientWidth, canvas.clientHeight);
        for (const rect of [rects.base, rects.head]) {
          if (anchor.x >= rect.x && anchor.x <= rect.x + rect.w && anchor.y >= rect.y && anchor.y <= rect.y + rect.h) {
            return { x: rect.x + rect.w / 2, y: rect.y + rect.h / 2 };
          }
        }
      }
      return { x: canvas.clientWidth / 2, y: canvas.clientHeight / 2 };
    }

    function setGraph(view) {
      state.view = view;
      setActiveViewButton();
      if (view === 'compare') {
        if (!state.compare) {
          runCompare();
          return;
        }
        setCompareGraph();
        return;
      }
      const source = view === 'architecture' ? state.raw.component_graph : state.raw.commit_graph;
      state.allNodes = source.nodes.map((node, index) => positionedNode(node, index));
      state.allEdges = source.edges;
      state.hiddenNodeIds = new Set();
      state.showCommon = false;
      document.getElementById('showCommonInput').checked = state.showCommon;
      state.selected = null;
      renderStats(state.raw.stats);
      applyFilters();
      renderFilterControls();
    }

    function normalizeComparePayload(payload) {
      const baseNodes = payload.base.graph.nodes.map((node, index) => positionedNode(node, index));
      const headNodes = payload.head.graph.nodes.map((node, index) => positionedNode(node, index));
      return {
        raw: payload,
        base: {
          ref: payload.base.ref,
          sha: payload.base.sha,
          allNodes: baseNodes,
          allEdges: payload.base.graph.edges,
          nodes: [],
          edges: []
        },
        head: {
          ref: payload.head.ref,
          sha: payload.head.sha,
          allNodes: headNodes,
          allEdges: payload.head.graph.edges,
          nodes: [],
          edges: []
        },
        summary: payload.summary
      };
    }

    function positionedNode(node, index) {
      return {
        ...node,
        x: Math.cos(index * 2.399) * (140 + (index % 7) * 28),
        y: Math.sin(index * 2.399) * (140 + (index % 7) * 28),
        vx: 0,
        vy: 0
      };
    }

    function setCompareGraph() {
      state.allNodes = mergeCompareNodes();
      state.allEdges = [];
      state.hiddenNodeIds = new Set();
      state.showCommon = false;
      document.getElementById('showCommonInput').checked = state.showCommon;
      state.selected = null;
      renderCompareStats();
      applyFilters();
      renderFilterControls();
    }

    function mergeCompareNodes() {
      const merged = new Map();
      for (const node of [...state.compare.base.allNodes, ...state.compare.head.allNodes]) {
        const existing = merged.get(node.id);
        if (!existing || changeRank(node.change) > changeRank(existing.change)) {
          merged.set(node.id, node);
        }
      }
      return [...merged.values()].sort((a, b) => a.label.localeCompare(b.label));
    }

    function applyFilters() {
      if (state.view === 'compare') {
        applyCompareFilters();
        return;
      }
      const visibleIds = new Set();
      state.nodes = [];
      for (const node of state.allNodes) {
        if (!state.showCommon && isCommonNode(node)) continue;
        if (state.hiddenNodeIds.has(node.id)) continue;
        visibleIds.add(node.id);
        state.nodes.push(node);
      }
      state.edges = state.allEdges.filter(
        edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)
      );
      if (state.selected && state.selected.kind === 'node' && !visibleIds.has(state.selected.node.id)) {
        state.selected = null;
      }
      if (
        state.selected &&
        state.selected.kind === 'edge' &&
        (!visibleIds.has(state.selected.edge.source) || !visibleIds.has(state.selected.edge.target))
      ) {
        state.selected = null;
      }
      renderFilterSummary();
      renderSelection(state.selected);
      renderTopEdges();
    }

    function applyCompareFilters() {
      filterCompareSide(state.compare.base);
      filterCompareSide(state.compare.head);
      if (state.selected && state.selected.kind === 'node') {
        const side = state.compare[state.selected.side];
        if (!side || !side.nodes.some(node => node.id === state.selected.node.id)) state.selected = null;
      }
      if (state.selected && state.selected.kind === 'edge') {
        const side = state.compare[state.selected.side];
        if (!side || !side.edges.some(edge => edge.id === state.selected.edge.id)) state.selected = null;
      }
      state.nodes = [...state.compare.base.nodes, ...state.compare.head.nodes];
      state.edges = [...state.compare.base.edges, ...state.compare.head.edges];
      renderFilterSummary();
      renderSelection(state.selected);
      renderTopEdges();
    }

    function filterCompareSide(side) {
      const visibleIds = new Set();
      side.nodes = [];
      for (const node of side.allNodes) {
        if (!state.showCommon && isCommonNode(node)) continue;
        if (state.hiddenNodeIds.has(node.id)) continue;
        visibleIds.add(node.id);
        side.nodes.push(node);
      }
      side.edges = side.allEdges.filter(
        edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)
      );
    }

    function renderFilterControls() {
      const root = document.getElementById('componentFilters');
      root.innerHTML = '';
      const nodes = [...state.allNodes]
        .sort((a, b) => a.label.localeCompare(b.label))
        .filter(node => !state.componentFilter || node.label.toLowerCase().includes(state.componentFilter));
      for (const node of nodes) {
        const common = isCommonNode(node);
        const disabled = common && !state.showCommon;
        const row = document.createElement('label');
        row.className = 'filter-row' + (disabled ? ' disabled' : '');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = !state.hiddenNodeIds.has(node.id) && !disabled;
        checkbox.disabled = disabled;
        checkbox.onchange = () => {
          if (checkbox.checked) state.hiddenNodeIds.delete(node.id);
          else state.hiddenNodeIds.add(node.id);
          applyFilters();
          renderFilterSummary();
        };
        const swatch = document.createElement('span');
        swatch.className = 'swatch';
        swatch.style.background = nodeColor(node);
        const name = document.createElement('span');
        name.className = 'filter-name';
        name.textContent = node.label;
        const kind = document.createElement('span');
        kind.className = 'filter-kind';
        kind.textContent = common ? 'common' : node.type;
        row.append(checkbox, swatch, name, kind);
        root.appendChild(row);
      }
      renderFilterSummary();
    }

    function renderFilterSummary() {
      const visibleCount = state.view === 'compare'
        ? new Set(state.nodes.map(node => node.id)).size
        : state.nodes.length;
      document.getElementById('filterSummary').textContent =
        visibleCount + ' of ' + state.allNodes.length + ' visible';
    }

    function isCommonNode(node) {
      const id = node.id.replace(/^component:/, '').toLowerCase();
      const label = node.label.toLowerCase();
      return COMMON_NODE_IDS.has(id) || COMMON_NODE_IDS.has(label);
    }

    function renderStats(stats) {
      const root = document.getElementById('stats');
      root.innerHTML = '';
      for (const [label, value] of Object.entries({
        Files: stats.files,
        Symbols: stats.symbols,
        Components: stats.components,
        Edges: stats.component_edges,
        Commits: stats.commits
      })) {
        const div = document.createElement('div');
        div.className = 'stat';
        div.innerHTML = '<span>' + label + '</span><strong>' + value + '</strong>';
        root.appendChild(div);
      }
    }

    function renderCompareStats() {
      const summary = state.compare.summary;
      const root = document.getElementById('stats');
      root.innerHTML = '';
      for (const [label, value] of Object.entries({
        Added: summary.added_nodes + summary.added_edges,
        Removed: summary.removed_nodes + summary.removed_edges,
        Changed: summary.changed_nodes + summary.changed_edges,
        Nodes: summary.added_nodes + summary.removed_nodes + summary.changed_nodes,
        Edges: summary.added_edges + summary.removed_edges + summary.changed_edges
      })) {
        const div = document.createElement('div');
        div.className = 'stat';
        div.innerHTML = '<span>' + label + '</span><strong>' + value + '</strong>';
        root.appendChild(div);
      }
    }

    function compareSummaryText(summary) {
      return [
        summary.added_nodes + summary.added_edges + ' added',
        summary.removed_nodes + summary.removed_edges + ' removed',
        summary.changed_nodes + summary.changed_edges + ' changed'
      ].join(' / ');
    }

    function tick() {
      resize();
      simulate();
      draw();
      requestAnimationFrame(tick);
    }

    function resize() {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
        canvas.width = Math.floor(w * dpr);
        canvas.height = Math.floor(h * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
    }

    function simulate() {
      if (state.view === 'compare' && state.compare) {
        simulateGraph(state.compare.base.nodes, state.compare.base.edges, 165);
        simulateGraph(state.compare.head.nodes, state.compare.head.edges, 165);
        return;
      }
      simulateGraph(state.nodes, state.edges, state.view === 'commits' ? 115 : 165);
    }

    function simulateGraph(nodes, edges, desiredDistance) {
      const nodesById = new Map(nodes.map(node => [node.id, node]));
      for (const node of nodes) {
        node.vx += -node.x * 0.0008;
        node.vy += -node.y * 0.0008;
      }
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          const dx = a.x - b.x, dy = a.y - b.y;
          const distSq = Math.max(120, dx * dx + dy * dy);
          const dist = Math.sqrt(distSq);
          const force = 2400 / distSq;
          a.vx += dx / dist * force; a.vy += dy / dist * force;
          b.vx -= dx / dist * force; b.vy -= dy / dist * force;
        }
      }
      for (const edge of edges) {
        const a = nodesById.get(edge.source), b = nodesById.get(edge.target);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
        const weight = Math.min(3, Math.log2((edge.weight || 1) + 1));
        const force = clamp((dist - desiredDistance) * 0.014 * weight, -4, 4);
        a.vx += dx / dist * force; a.vy += dy / dist * force;
        b.vx -= dx / dist * force; b.vy -= dy / dist * force;
      }
      for (const node of nodes) {
        node.vx = clamp(node.vx * 0.82, -10, 10);
        node.vy = clamp(node.vy * 0.82, -10, 10);
        node.x = clamp(node.x + node.vx, -1200, 1200);
        node.y = clamp(node.y + node.vy, -1200, 1200);
      }
    }

    function draw() {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      if (state.view === 'compare' && state.compare) {
        drawCompare(w, h);
        return;
      }
      if (!state.nodes.length) {
        ctx.fillStyle = '#9ba7b6';
        ctx.font = '14px system-ui';
        ctx.textAlign = 'center';
        ctx.fillText('No graph nodes found. Try refreshing the repository index.', w / 2, h / 2);
        return;
      }
      drawGraphPanel(state.nodes, state.edges, { x: 0, y: 0, w, h }, null);
      ctx.globalAlpha = 1;
    }

    function drawCompare(w, h) {
      const rects = compareRects(w, h);
      const left = rects.base;
      const right = rects.head;
      ctx.fillStyle = 'rgba(239, 123, 123, .09)';
      ctx.fillRect(0, 0, w, 46);
      ctx.strokeStyle = '#343a46';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(rects.dividerX, 0);
      ctx.lineTo(rects.dividerX, h);
      ctx.stroke();
      drawGraphPanel(state.compare.base.nodes, state.compare.base.edges, left, state.compare.base.ref + ' ' + shortSha(state.compare.base.sha), 'base');
      drawGraphPanel(state.compare.head.nodes, state.compare.head.edges, right, state.compare.head.ref + ' ' + shortSha(state.compare.head.sha), 'head');
    }

    function compareRects(w, h) {
      const gap = 18;
      const leftWidth = Math.floor((w - gap) / 2);
      return {
        base: { x: 0, y: 0, w: leftWidth, h },
        head: { x: leftWidth + gap, y: 0, w: w - leftWidth - gap, h },
        dividerX: leftWidth + gap / 2
      };
    }

    function drawGraphPanel(nodes, edges, rect, label, side) {
      if (!nodes.length) {
        ctx.fillStyle = '#9ba7b6';
        ctx.font = '14px system-ui';
        ctx.textAlign = 'center';
        ctx.fillText('No visible nodes', rect.x + rect.w / 2, rect.y + rect.h / 2);
        return;
      }
      if (label) {
        ctx.fillStyle = '#eef1f5';
        ctx.font = '13px system-ui';
        ctx.textAlign = 'left';
        ctx.fillText(label, rect.x + 16, rect.y + 26);
      }
      const nodesById = new Map(nodes.map(node => [node.id, node]));
      const transform = graphTransformFor(nodes, rect);
      ctx.lineCap = 'round';
      for (const edge of edges) {
        const a = nodesById.get(edge.source), b = nodesById.get(edge.target);
        if (!a || !b) continue;
        const pa = projectInRect(a, transform, rect), pb = projectInRect(b, transform, rect);
        ctx.globalAlpha = edgeAlpha(edge, side);
        ctx.strokeStyle = edgeColor(edge.type, edge);
        ctx.lineWidth = isSelectedEdge(edge, side)
          ? 5
          : Math.min(4, 0.7 + Math.log2((edge.weight || 1) + 1) * 0.55);
        ctx.beginPath();
        ctx.moveTo(pa.x, pa.y);
        ctx.lineTo(pb.x, pb.y);
        ctx.stroke();
      }
      const sorted = [...nodes].sort(
        (a, b) => projectInRect(a, transform, rect).scale - projectInRect(b, transform, rect).scale
      );
      for (const node of sorted) {
        const p = projectInRect(node, transform, rect);
        const dim = state.search && !node.label.toLowerCase().includes(state.search);
        ctx.globalAlpha = dim ? 0.18 : 1;
        ctx.fillStyle = nodeColor(node);
        const selected = isSelectedNode(node, side) || isSelectedEdgeEndpoint(node, side);
        ctx.strokeStyle = selected ? '#eef1f5' : '#0f1115';
        ctx.lineWidth = selected ? 3 : 1.5;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        if (p.r > 9 && !dim) {
          ctx.fillStyle = '#eef1f5';
          ctx.font = '12px system-ui';
          ctx.textAlign = 'center';
          ctx.fillText(shortLabel(node.label), p.x, p.y + p.r + 14);
        }
      }
      ctx.globalAlpha = 1;
    }

    function graphTransform() {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      return graphTransformFor(state.nodes, { x: 0, y: 0, w, h });
    }

    function graphTransformFor(nodes, rect) {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const node of nodes) {
        minX = Math.min(minX, node.x);
        minY = Math.min(minY, node.y);
        maxX = Math.max(maxX, node.x);
        maxY = Math.max(maxY, node.y);
      }
      const spanX = Math.max(1, maxX - minX);
      const spanY = Math.max(1, maxY - minY);
      const pad = state.view === 'commits' ? 80 : 110;
      const fitZoom = Math.min(
        1.7,
        Math.max(
          0.22,
          Math.min(
            Math.max(220, rect.w - pad * 2) / spanX,
            Math.max(220, rect.h - pad * 2) / spanY
          )
        )
      );
      return {
        centerX: (minX + maxX) / 2,
        centerY: (minY + maxY) / 2,
        zoom: fitZoom * state.zoom
      };
    }

    function project(node, transform) {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      return projectInRect(node, transform || graphTransform(), { x: 0, y: 0, w, h });
    }

    function projectInRect(node, transform, rect) {
      const fit = transform || graphTransformFor([node], rect);
      const radiusScale = clamp(Math.sqrt(fit.zoom), 0.75, 1.35);
      return {
        x: rect.x + rect.w / 2 + state.panX + (node.x - fit.centerX) * fit.zoom,
        y: rect.y + rect.h / 2 + state.panY + (node.y - fit.centerY) * fit.zoom,
        scale: 1,
        r: Math.max(5, Math.min(34, (node.size || 14) * radiusScale))
      };
    }

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, value));
    }

    function edgeAlpha(edge, side) {
      if (!state.selected) return 0.38;
      if (isSelectedEdge(edge, side)) return 1;
      if (state.selected.kind === 'node') {
        const id = state.selected.node.id;
        if (side && state.selected.side && side !== state.selected.side) return 0.08;
        return edge.source === id || edge.target === id ? 0.9 : 0.08;
      }
      if (state.selected.kind === 'edge') {
        if (side && state.selected.side && side !== state.selected.side) return 0.08;
        const selected = state.selected.edge;
        return edge.source === selected.source || edge.target === selected.target ||
          edge.source === selected.target || edge.target === selected.source ? 0.35 : 0.08;
      }
      return 0.38;
    }

    function isSelectedNode(node, side) {
      return state.selected && state.selected.kind === 'node' &&
        state.selected.node.id === node.id &&
        (!side || !state.selected.side || state.selected.side === side);
    }

    function isSelectedEdge(edge, side) {
      return state.selected && state.selected.kind === 'edge' &&
        state.selected.edge.id === edge.id &&
        (!side || !state.selected.side || state.selected.side === side);
    }

    function isSelectedEdgeEndpoint(node, side) {
      return state.selected && state.selected.kind === 'edge' &&
        (!side || !state.selected.side || state.selected.side === side) &&
        (state.selected.edge.source === node.id || state.selected.edge.target === node.id);
    }

    function edgeColor(type, edge) {
      if (edge && edge.change && edge.change !== 'unchanged') return '#ef7b7b';
      return {
        calls: '#77a7ff',
        imports: '#b69cff',
        cochange: '#e4b363',
        authored: '#71d49b',
        touched: '#e4b363'
      }[type] || '#596274';
    }

    function nodeColor(node) {
      if (node.change && node.change !== 'unchanged') return '#ef7b7b';
      if (node.type === 'developer') return '#71d49b';
      if (node.type === 'commit') return '#e4b363';
      if (node.type === 'external') return '#b69cff';
      if (node.risk === 'high') return '#ef7b7b';
      if (node.risk === 'medium') return '#e4b363';
      return '#77a7ff';
    }

    function renderSelection(selection) {
      const title = document.getElementById('selectionTitle');
      const meta = document.getElementById('selectionMeta');
      if (!selection) {
        title.textContent = 'Nothing selected';
        meta.innerHTML = '';
        return;
      }
      if (selection.kind === 'edge') {
        renderEdgeSelection(selection.edge, title, meta, selection.side);
        return;
      }
      renderNodeSelection(selection, title, meta);
    }

    function renderNodeSelection(selection, title, meta) {
      const node = selection.node;
      title.textContent = node.label;
      const stack = detailStack(meta);
      const overview = ['type: ' + node.type];
      if (selection.side) overview.push('side: ' + selection.side);
      if (node.change) overview.push('change: ' + node.change);
      if (node.risk) overview.push('risk: ' + node.risk);
      if (node.timestamp) overview.push('time: ' + node.timestamp);
      if (node.metrics) {
        for (const [key, value] of Object.entries(node.metrics)) overview.push(key + ': ' + value);
      }
      if (node.tags && node.tags.length) overview.push('tags: ' + node.tags.join(', '));
      if (node.details) overview.push('', node.details);
      appendDetailSection(stack, 'Overview', overview, true);
      renderNodeConnections(stack, node, selection.side);
    }

    function renderNodeConnections(stack, node, side) {
      const edges = nodeEdgesForSelection(node, side)
        .sort((a, b) => edgeDetailRank(b) - edgeDetailRank(a) || (b.weight || 1) - (a.weight || 1));
      if (!edges.length) {
        appendDetailSection(stack, 'Connections', ['connections: none visible with current filters'], true);
        return;
      }
      const codeEdges = edges.filter(edge => !['cochange', 'authored', 'touched'].includes(edge.type));
      const historyEdges = edges.filter(edge => ['cochange', 'authored', 'touched'].includes(edge.type));
      appendEdgeGroupSection(stack, 'Functions / APIs', codeEdges, true);
      appendComponentEdgeSummary(stack, 'Components / Edges', edges, false);
      if (historyEdges.length) {
        appendEdgeGroupSection(stack, 'Commit / Co-change Evidence', historyEdges, false);
      }
    }

    function nodeEdgesForSelection(node, side) {
      let edges = state.edges;
      if (side && state.compare && state.compare[side]) edges = state.compare[side].edges;
      return edges.filter(edge => edge.source === node.id || edge.target === node.id);
    }

    function edgeDetailRank(edge) {
      const typeRank = { calls: 4, references: 3, imports: 2, inherits: 2, cochange: 1 };
      return (typeRank[edge.type] || 0) * 1000 + Math.min(edge.examples ? edge.examples.length : 0, 50);
    }

    function renderEdgeSelection(edge, title, meta, side) {
      const source = labelForNode(edge.source);
      const target = labelForNode(edge.target);
      title.textContent = source + ' -> ' + target;
      const stack = detailStack(meta);
      const overview = [
        'connection: ' + edge.type,
        side ? 'side: ' + side : '',
        edge.change ? 'change: ' + edge.change : '',
        'source: ' + source,
        'target: ' + target,
        'weight: ' + (edge.weight || 1)
      ].filter(Boolean);
      if (edge.file) overview.push('file: ' + edge.file);
      appendDetailSection(stack, 'Overview', overview, true);
      appendExampleSection(stack, 'Function / API Examples', edge.examples || [], true);
      if (edge.reasons && edge.reasons.length) {
        const evidence = [];
        for (const reason of edge.reasons.slice(0, 8)) {
          if (reason) evidence.push('- ' + reason);
        }
        appendDetailSection(stack, 'Summary Evidence', evidence, false);
      }
    }

    function detailStack(root) {
      root.innerHTML = '';
      const stack = document.createElement('div');
      stack.className = 'detail-stack';
      root.appendChild(stack);
      return stack;
    }

    function appendDetailSection(stack, title, lines, open) {
      const section = document.createElement('details');
      section.className = 'detail-card';
      section.open = Boolean(open);
      const summary = document.createElement('summary');
      summary.textContent = title;
      const body = document.createElement('div');
      body.className = 'detail-body';
      if (lines.length) renderDetailLines(body, lines);
      section.append(summary, body);
      stack.appendChild(section);
      return body;
    }

    function appendEdgeGroupSection(stack, title, edges, open) {
      const body = appendDetailSection(
        stack,
        title + ' (' + edges.length + ')',
        edges.length ? [] : ['No visible connections in this group.'],
        open
      );
      if (!edges.length) return;
      for (const edge of edges.slice(0, 10)) {
        appendEdgeDetails(body, edge, false);
      }
      if (edges.length > 10) appendPlainLine(body, '... ' + (edges.length - 10) + ' more visible connections');
    }

    function appendComponentEdgeSummary(stack, title, edges, open) {
      const lines = edges.slice(0, 16).map(edge =>
        '- ' + labelForNode(edge.source) + ' -> ' + labelForNode(edge.target) +
        ' (' + edge.type + ', weight ' + (edge.weight || 1) + ')'
      );
      if (edges.length > 16) lines.push('... ' + (edges.length - 16) + ' more visible edges');
      appendDetailSection(stack, title + ' (' + edges.length + ')', lines, open);
    }

    function appendEdgeDetails(parent, edge, open) {
      const section = document.createElement('details');
      section.className = 'detail-card detail-nested';
      section.open = Boolean(open);
      const summary = document.createElement('summary');
      summary.textContent = edgeSummary(edge);
      const body = document.createElement('div');
      body.className = 'detail-body';
      renderDetailLines(body, [
        'type: ' + edge.type,
        'source component: ' + labelForNode(edge.source),
        'target component: ' + labelForNode(edge.target),
        'weight: ' + (edge.weight || 1)
      ]);
      appendExampleSection(body, 'Examples', edge.examples || [], false);
      section.append(summary, body);
      parent.appendChild(section);
    }

    function appendExampleSection(parent, title, examples, open) {
      const wrapper = document.createElement('details');
      wrapper.className = 'detail-card' + (parent.classList.contains('detail-body') ? ' detail-nested' : '');
      wrapper.open = Boolean(open);
      const wrapperSummary = document.createElement('summary');
      wrapperSummary.textContent = title + ' (' + examples.length + ')';
      const wrapperBody = document.createElement('div');
      wrapperBody.className = 'detail-body';
      wrapper.append(wrapperSummary, wrapperBody);
      parent.appendChild(wrapper);
      if (!examples.length) {
        appendPlainLine(wrapperBody, 'No function/API examples available for this connection.');
        return;
      }
      for (const [index, example] of examples.slice(0, 8).entries()) {
        const section = document.createElement('details');
        section.className = 'detail-card detail-nested';
        section.open = Boolean(open && index === 0);
        const summary = document.createElement('summary');
        summary.textContent = title + ' ' + (index + 1) + ': ' + edgeExampleTitle(example);
        const body = document.createElement('div');
        body.className = 'detail-body';
        renderDetailLines(body, renderEdgeExample(example));
        section.append(summary, body);
        wrapperBody.appendChild(section);
      }
      if (examples.length > 8) appendPlainLine(wrapperBody, '... ' + (examples.length - 8) + ' more examples');
    }

    function appendPlainLine(parent, line) {
      const div = document.createElement('div');
      div.className = 'detail-lines detail-empty';
      div.innerHTML = colorizeDetailLine(line);
      parent.appendChild(div);
    }

    function edgeSummary(edge) {
      return labelForNode(edge.source) + ' -> ' + labelForNode(edge.target) +
        ' (' + edge.type + ', weight ' + (edge.weight || 1) + ')';
    }

    function edgeExampleTitle(example) {
      return formatCall(example) || (formatEndpoint(example.source) + ' -> ' + formatEndpoint(example.target));
    }

    function renderDetailLines(root, lines) {
      root.innerHTML = lines.map(colorizeDetailLine).join('\n');
    }

    function colorizeDetailLine(line) {
      if (!line) return '';
      const trimmed = line.trim();
      if (!trimmed) return '';
      const leading = line.match(/^\s*/)[0];
      const clean = line.slice(leading.length);
      const prefix = escapeHtml(leading);
      if (clean.startsWith('- ')) {
        return prefix + '<span class="detail-edge">' + escapeHtml(clean) + '</span>';
      }
      if (clean.startsWith('... ')) {
        return prefix + '<span class="detail-value">' + escapeHtml(clean) + '</span>';
      }
      if (/connections( \(|:)/.test(clean) || clean === 'summary evidence:') {
        return prefix + '<span class="detail-section">' + escapeHtml(clean) + '</span>';
      }
      const keyMatch = clean.match(/^([^:]{1,40}):\s?(.*)$/);
      if (keyMatch) {
        const key = keyMatch[1];
        const value = keyMatch[2] || '';
        const valueClass = detailValueClass(key);
        return prefix +
          '<span class="detail-key">' + escapeHtml(key) + ':</span>' +
          (value ? ' <span class="' + valueClass + '">' + escapeHtml(value) + '</span>' : '');
      }
      return prefix + '<span class="detail-value">' + escapeHtml(clean) + '</span>';
    }

    function detailValueClass(key) {
      const lower = key.toLowerCase();
      if (lower === 'call') return 'detail-call';
      if (lower === 'parameters') return 'detail-call';
      if (lower.includes('signature')) return 'detail-signature';
      if (lower === 'location' || lower === 'file') return 'detail-path';
      if (lower === 'change') return 'detail-change';
      return 'detail-value';
    }

    function renderEdgeExample(example) {
      const source = formatEndpoint(example.source);
      const target = formatEndpoint(example.target);
      const lines = ['- ' + source + ' -> ' + target + ' (' + example.type + ')'];
      const call = formatCall(example);
      if (call) lines.push('  call: ' + call);
      if (example.arguments && example.arguments.length) lines.push('  parameters: ' + example.arguments.join(', '));
      if (example.source && example.source.signature) lines.push('  source signature: ' + example.source.signature);
      if (example.target && example.target.signature) lines.push('  target signature: ' + example.target.signature);
      const location = formatLocation(example);
      if (location) lines.push('  location: ' + location);
      if (example.commit) lines.push('  commit: ' + example.commit);
      return lines;
    }

    function formatEndpoint(endpoint) {
      if (!endpoint) return 'unknown';
      return endpoint.qualified_name || endpoint.label || endpoint.name || endpoint.key || 'unknown';
    }

    function formatCall(example) {
      if (!example.display) return '';
      const args = example.arguments && example.arguments.length ? '(' + example.arguments.join(', ') + ')' : '';
      return example.display + args;
    }

    function formatLocation(example) {
      const file = example.file_path || (example.source && example.source.path) || '';
      if (!file) return '';
      return example.line ? file + ':' + example.line : file;
    }

    function renderTopEdges() {
      const root = document.getElementById('topEdges');
      root.innerHTML = '';
      const entries = state.view === 'compare' && state.compare
        ? [
            ...state.compare.base.edges.map(edge => ({ edge, side: 'base' })),
            ...state.compare.head.edges.map(edge => ({ edge, side: 'head' }))
          ]
        : state.edges.map(edge => ({ edge, side: null }));
      const top = entries
        .sort((a, b) => {
          const changeDelta = changeRank(b.edge.change) - changeRank(a.edge.change);
          return changeDelta || (b.edge.weight || 1) - (a.edge.weight || 1);
        })
        .slice(0, 10);
      for (const { edge, side } of top) {
        const div = document.createElement('div');
        div.className = 'pill';
        const prefix = side ? side + ': ' : '';
        const change = edge.change && edge.change !== 'unchanged' ? ', ' + edge.change : '';
        div.textContent = prefix + labelForNode(edge.source) + ' -> ' + labelForNode(edge.target) + ' (' + edge.type + change + ')';
        div.onclick = () => {
          state.selected = { kind: 'edge', edge, side };
          renderSelection(state.selected);
        };
        root.appendChild(div);
      }
    }

    function labelForNode(id) {
      const node = state.allNodes.find(item => item.id === id);
      return node ? node.label : id;
    }

    function changeRank(change) {
      return change && change !== 'unchanged' ? 1 : 0;
    }

    function shortSha(sha) {
      return sha ? sha.slice(0, 8) : '';
    }

    function distanceToSegment(px, py, x1, y1, x2, y2) {
      const dx = x2 - x1;
      const dy = y2 - y1;
      if (dx === 0 && dy === 0) return Math.hypot(px - x1, py - y1);
      const t = clamp(((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy), 0, 1);
      const x = x1 + t * dx;
      const y = y1 + t * dy;
      return Math.hypot(px - x, py - y);
    }

    function shortLabel(label) {
      return label.length > 24 ? label.slice(0, 21) + '...' : label;
    }
  </script>
</body>
</html>
"""
