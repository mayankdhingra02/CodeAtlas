from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import textwrap
import unittest
from unittest import mock
from pathlib import Path
from urllib.request import urlopen

from codeatlas.agent_install import install_agent
from codeatlas.analysis import dead_code, http_confidence_summary, route_summary, structural_query
from codeatlas.artifacts import export_graph_artifact, import_graph_artifact
from codeatlas.benchmark import Benchmarker
from codeatlas.config import CodeAtlasPaths
from codeatlas.external_index import import_external_index
from codeatlas.indexer import RepositoryIndexer
from codeatlas.memory import MemoryQueryEngine
from codeatlas.mcp_server import create_tool_handlers
from codeatlas.models import SourceFile, estimate_tokens
from codeatlas.packs import context_pack, render_context_pack
from codeatlas.parsers.javascript import JavaScriptParser
from codeatlas.parsers.python import PythonParser
from codeatlas.project_config import load_project_config, restore_classification_config, update_classification_config
from codeatlas.retrieval import RetrievalEngine
from codeatlas.rules import run_rule_checks
from codeatlas.scanner import iter_source_files
from codeatlas.source import source_outline
from codeatlas.status import index_status
from codeatlas.verification import verification_plan
from codeatlas.visualization import (
    ASSET_DIR,
    HTML_APP,
    VisualizationService,
    create_visualization_server,
    find_available_port,
    render_visualization_app,
)
from codeatlas.workflow_cache import cached_workflow


class CodeAtlasTestCase(unittest.TestCase):
    def make_repo(self) -> tempfile.TemporaryDirectory[str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "app").mkdir()
        (root / "app" / "__init__.py").write_text("", encoding="utf-8")
        (root / "app" / "payments.py").write_text(
            textwrap.dedent(
                '''
                class PaymentService:
                    """Charges customers."""

                    def charge(self, total):
                        return total
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        (root / "app" / "orders.py").write_text(
            textwrap.dedent(
                '''
                from app.payments import PaymentService

                @service
                class OrderService:
                    def create_order(self, total):
                        processor = PaymentService()
                        return processor.charge(total)
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        (root / "node_modules").mkdir()
        (root / "node_modules" / "ignored.py").write_text("def ignored(): pass\n", encoding="utf-8")
        return temp

    def make_memory_repo(self) -> tempfile.TemporaryDirectory[str]:
        temp = self.make_repo()
        root = Path(temp.name)
        (root / "docs" / "adr").mkdir(parents=True)
        (root / "README.md").write_text(
            "# Memory Repo\n\nAuthentication and payments are core repository areas.\n",
            encoding="utf-8",
        )
        run_git(root, "init", "-b", "main")
        run_git(root, "config", "user.name", "Alice Example")
        run_git(root, "config", "user.email", "alice@example.com")
        run_git(root, "add", ".")
        run_git(
            root,
            "commit",
            "-m",
            "Add payment service",
            env=git_env("Alice Example", "alice@example.com", "2024-01-01T12:00:00+00:00"),
        )

        (root / "app" / "auth.py").write_text(
            textwrap.dedent(
                '''
                class AuthService:
                    def login(self, token):
                        return token
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        (root / "docs" / "adr" / "0001-redis-auth.md").write_text(
            textwrap.dedent(
                '''
                # ADR 0001: Redis cache for authentication retries

                ## Context

                Authentication requests were timing out during transient upstream failures.

                ## Decision

                Introduce Redis as a short-lived cache for authentication retry state.

                ## Alternatives

                We considered local in-process caches, but rejected them because workers
                would not share retry state.
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        run_git(root, "add", ".")
        run_git(
            root,
            "commit",
            "-m",
            "Introduce Redis cache for auth retry timeouts (#12)",
            env=git_env("Bob Reviewer", "bob@example.com", "2025-02-01T12:00:00+00:00"),
        )
        return temp


class ParserTests(CodeAtlasTestCase):
    def test_python_parser_extracts_symbols_imports_calls_and_inheritance(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            source = next(file for file in iter_source_files(root) if file.relative_path == "app/orders.py")
            result = PythonParser().parse(root, source)

        self.assertEqual(result.module_name, "app.orders")
        self.assertEqual(result.imports[0].module, "app.payments")
        self.assertEqual(result.imports[0].name, "PaymentService")
        names = {symbol.qualified_name for symbol in result.symbols}
        self.assertIn("app.orders.OrderService", names)
        self.assertIn("app.orders.OrderService.create_order", names)
        self.assertIn("PaymentService", {call.target_name for call in result.calls})
        self.assertIn("charge", {call.target_name for call in result.calls})
        calls_by_display = {call.display_name: call for call in result.calls}
        self.assertEqual(calls_by_display["processor.charge"].arguments, ("total",))

    def test_scanner_ignores_dependency_directories(self) -> None:
        with self.make_repo() as root_name:
            files = {source.relative_path for source in iter_source_files(Path(root_name))}

        self.assertIn("app/orders.py", files)
        self.assertNotIn("node_modules/ignored.py", files)

    def test_project_config_controls_languages_and_ignored_paths(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "web").mkdir()
            (root / "web" / "client.ts").write_text("export const value = 1;\n", encoding="utf-8")
            (root / "generated").mkdir()
            (root / "generated" / "skip.py").write_text("def ignored(): pass\n", encoding="utf-8")
            (root / ".codeatlas.yml").write_text(
                textwrap.dedent(
                    """
                    languages:
                      python: true
                      javascript: false
                    ignore:
                      paths:
                        - generated/**
                    ui:
                      default_lens: apis
                      node_budget: 80
                      connected_only: false
                      edge_contrast: 72
                    classification:
                      owned_prefixes:
                        - app
                      team_prefixes:
                        - company_
                      third_party_packages:
                        - requests
                      hide_packages:
                        - docutils
                      show_packages:
                        - company_sdk
                    cache:
                      ttl_seconds: 120
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            config = load_project_config(root)
            files = {source.relative_path for source in iter_source_files(root)}

        self.assertFalse(config.languages["javascript"])
        self.assertEqual(config.ui.default_lens, "apis")
        self.assertEqual(config.ui.node_budget, 80)
        self.assertFalse(config.ui.connected_only)
        self.assertEqual(config.ui.edge_contrast, 72)
        self.assertEqual(config.classification.owned_prefixes, ("app",))
        self.assertEqual(config.classification.team_prefixes, ("company_",))
        self.assertEqual(config.classification.third_party_packages, ("requests",))
        self.assertEqual(config.classification.hide_packages, ("docutils",))
        self.assertEqual(config.classification.show_packages, ("company_sdk",))
        self.assertEqual(config.public_payload()["ui"]["edge_contrast"], 72)
        self.assertEqual(config.public_payload()["classification"]["third_party_packages"], ["requests"])
        self.assertEqual(config.public_payload()["classification"]["show_packages"], ["company_sdk"])
        self.assertEqual(config.cache.ttl_seconds, 120)
        self.assertNotIn("web/client.ts", files)
        self.assertNotIn("generated/skip.py", files)

    def test_update_classification_config_persists_and_restores_exact_package_bucket(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / ".codeatlas.yml").write_text(
                textwrap.dedent(
                    """
                    classification:
                      show_packages:
                        - requests
                      hide_packages:
                        - docutils
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            updated = update_classification_config(root, "requests", "third_party")
            restored = restore_classification_config(root, {"show_packages": ["requests"], "hide_packages": ["docutils"]})
            reloaded = load_project_config(root)

        self.assertEqual(updated.classification.third_party_packages, ("requests",))
        self.assertEqual(restored.classification.show_packages, ("requests",))
        self.assertEqual(reloaded.classification.third_party_packages, ())
        self.assertEqual(reloaded.classification.show_packages, ("requests",))
        self.assertIn("third_party_packages", reloaded.public_payload()["classification"])

    def test_javascript_parser_extracts_imports_symbols_and_calls(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "web").mkdir()
            (root / "web" / "client.ts").write_text(
                textwrap.dedent(
                    """
                    import { fetchUser as loadUser } from './api';

                    export class UserClient {
                      async load(id: string) {
                        return loadUser(id);
                      }
                    }

                    export const renderUser = (id: string) => loadUser(id);

                    router.get('/users/:id', renderUser);
                    test('renders user profile', () => renderUser('1'));
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            source = next(file for file in iter_source_files(root) if file.relative_path == "web/client.ts")
            result = JavaScriptParser().parse(root, source)

        self.assertEqual(result.module_name, "web.client")
        self.assertIn("./api", {record.module for record in result.imports})
        names = {symbol.qualified_name for symbol in result.symbols}
        self.assertIn("web.client.UserClient", names)
        self.assertIn("web.client.UserClient.load", names)
        self.assertIn("web.client.renderUser", names)
        self.assertIn("web.client.route_get_users_id", names)
        self.assertIn("web.client.test_renders_user_profile", names)
        self.assertIn("loadUser", {call.target_name for call in result.calls})


class IndexAndRetrievalTests(CodeAtlasTestCase):
    def test_indexer_persists_sqlite_index_and_stats(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            report = RepositoryIndexer().index(root)
            stats = RetrievalEngine().repository_stats(root)
            self.assertTrue(report.database_path.exists())
            self.assertEqual(report.files_scanned, 3)
            self.assertGreaterEqual(report.symbols_indexed, 4)
            self.assertEqual(stats.files_indexed, 3)
            self.assertGreater(stats.graph_edges, 0)

    def test_retrieval_ranks_exact_symbol_and_related_callees(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            result = RetrievalEngine().retrieve(root, "create_order", depth=2, max_tokens=1000)

        self.assertGreaterEqual(len(result.snippets), 2)
        self.assertEqual(result.snippets[0].symbol_name, "create_order")
        self.assertIn("PaymentService", {snippet.symbol_name for snippet in result.snippets})
        self.assertLessEqual(
            result.token_report.optimized_tokens,
            result.token_report.baseline_tokens,
        )

    def test_incremental_indexing_only_processes_changed_files(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            (root / "app" / "orders.py").write_text(
                textwrap.dedent(
                    '''
                    from app.payments import PaymentService

                    class OrderService:
                        def create_order(self, total):
                            return PaymentService().charge(total)

                        def cancel_order(self):
                            return None
                    '''
                ).lstrip(),
                encoding="utf-8",
            )
            report = RepositoryIndexer().index(root, incremental=True)
            symbols = RetrievalEngine().find_symbol(root, "cancel_order")

        self.assertEqual(report.files_indexed, 1)
        self.assertEqual(report.files_skipped, 2)
        self.assertEqual(symbols[0]["name"], "cancel_order")

    def test_token_estimation_uses_four_character_rule(self) -> None:
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("abcde"), 2)
        self.assertEqual(estimate_tokens(""), 0)

    def test_retrieval_falls_back_to_indexed_file_text(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "docs").mkdir()
            (root / "docs" / "notes.py").write_text(
                "# migration playbook\nROLLBACK_TOKEN = 'blue-green receipts'\n",
                encoding="utf-8",
            )
            RepositoryIndexer().index(root)
            result = RetrievalEngine().retrieve(root, "blue green receipts", depth=1, max_tokens=500)

        self.assertTrue(result.snippets)
        self.assertEqual(result.snippets[0].kind, "FILE")
        self.assertIn("blue-green receipts", result.snippets[0].code)
        self.assertIn("SQLite FTS match", result.snippets[0].reason)

    def test_graph_artifact_export_import_and_index_status(self) -> None:
        with self.make_repo() as root_name, tempfile.TemporaryDirectory() as import_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            export_report = export_graph_artifact(root)
            import_root = Path(import_name)
            (import_root / ".codeatlas").mkdir()
            artifact_copy = import_root / ".codeatlas" / "graph.db.gz"
            artifact_copy.write_bytes(export_report.artifact_path.read_bytes())
            import_report = import_graph_artifact(import_root)
            status = index_status(root)
            (root / "app" / "orders.py").write_text("# changed\n", encoding="utf-8")
            dirty_status = index_status(root)
            self.assertTrue(export_report.artifact_path.exists())
            self.assertTrue(import_report.database_path.exists())
            self.assertTrue(status["indexed"])
            self.assertGreaterEqual(dirty_status["dirty_files"], 1)
            self.assertTrue(dirty_status["stale"])

    def test_structural_query_dead_code_routes_and_http_confidence(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "app" / "api.py").write_text(
                textwrap.dedent(
                    '''
                    class App:
                        def get(self, path):
                            return path

                    app = App()

                    @app.get("/health")
                    def health():
                        return {"ok": True}

                    def call_health(client):
                        return client.get("/health")

                    def unused_helper():
                        return "unused"
                    '''
                ).lstrip(),
                encoding="utf-8",
            )
            RepositoryIndexer().index(root)
            callers = structural_query(root, "callers:health")
            routes = route_summary(root)
            http = http_confidence_summary(root)
            dead = dead_code(root)

        self.assertEqual(callers["type"], "incoming")
        self.assertTrue(any(route["metadata"]["path"] == "/health" for route in routes["routes"]))
        self.assertTrue(any(edge["type"] == "HTTP_CALLS" for edge in http["edges"]))
        self.assertIn("app.api.unused_helper", {item["qualified_name"] for item in dead["items"]})

    def test_install_agent_writes_codex_config(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            payload = install_agent(root, "codex")
            mcp_path = Path(payload["mcp_config"])
            instructions_path = Path(payload["instructions"])
            self.assertTrue(mcp_path.exists())
            self.assertTrue(instructions_path.exists())
            self.assertIn("codeatlas", mcp_path.read_text(encoding="utf-8"))
            self.assertIn("Use CodeAtlas", instructions_path.read_text(encoding="utf-8"))

    def test_context_pack_rules_verification_and_source_outline(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "tests").mkdir()
            (root / "app" / "security.py").write_text(
                textwrap.dedent(
                    '''
                    import requests

                    API_TOKEN = "super-secret-token"
                    CHANGE_PASSWORD = "CHANGE_PASSWORD"

                    def fetch_user():
                        return requests.get("https://example.com/users")
                    '''
                ).lstrip(),
                encoding="utf-8",
            )
            (root / "tests" / "test_security.py").write_text(
                "from app.security import fetch_user\n\ndef test_fetch_user_exists():\n    assert fetch_user\n",
                encoding="utf-8",
            )
            run_git(root, "init", "-b", "main")
            run_git(root, "config", "user.name", "Alice Example")
            run_git(root, "config", "user.email", "alice@example.com")
            run_git(root, "add", ".")
            run_git(root, "commit", "-m", "Add security client")
            (root / "app" / "security.py").write_text(
                textwrap.dedent(
                    '''
                    import requests

                    API_TOKEN = "super-secret-token"
                    CHANGE_PASSWORD = "CHANGE_PASSWORD"

                    def fetch_user():
                        response = requests.get("https://example.com/users")
                        return response.json()
                    '''
                ).lstrip(),
                encoding="utf-8",
            )
            RepositoryIndexer().index(root)
            rules = run_rule_checks(root)
            outline = source_outline(root, "fetch_user")
            plan = verification_plan(root, base_ref="HEAD", task="fix user fetch timeout")
            pack = context_pack(root, "fix user fetch timeout", max_tokens=2500)
            rendered = render_context_pack(pack, output_format="markdown")

        self.assertIn("possible-secret", {finding["rule_id"] for finding in rules["findings"]})
        self.assertEqual(
            1,
            len([finding for finding in rules["findings"] if finding["rule_id"] == "possible-secret"]),
        )
        self.assertIn("python-requests-without-timeout", {finding["rule_id"] for finding in rules["findings"]})
        self.assertEqual(outline["files"][0]["file_path"], "app/security.py")
        self.assertIn("tests/test_security.py", plan["test_files"])
        self.assertIn("app/security.py", pack["recommended_files"])
        self.assertIn("# CodeAtlas Context Pack", rendered)
        self.assertNotIn("super-secret-token", rendered)

    def test_rules_respect_config_suppression_and_test_severity(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "tests").mkdir()
            (root / "tests" / "test_dynamic.py").write_text(
                "def test_eval_path():\n    eval('1 + 1')\n",
                encoding="utf-8",
            )
            (root / "app" / "secret.py").write_text(
                "API_TOKEN = 'real-secret-token'\n",
                encoding="utf-8",
            )
            (root / ".codeatlas.yml").write_text(
                textwrap.dedent(
                    """
                    rules:
                      tests_lower_severity: true
                      suppressions:
                        - rule: possible-secret
                          path: app/secret.py
                          reason: test suppression
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            rules = run_rule_checks(root)

        rule_ids = {finding["rule_id"] for finding in rules["findings"]}
        self.assertNotIn("possible-secret", rule_ids)
        dynamic = next(finding for finding in rules["findings"] if finding["rule_id"] == "dynamic-code-execution")
        self.assertEqual(dynamic["severity"], "medium")

    def test_workflow_cache_reuses_result_until_index_or_config_changes(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            calls = {"count": 0}

            def compute() -> dict[str, object]:
                calls["count"] += 1
                return {"value": calls["count"]}

            first = cached_workflow(root, "demo", {"query": "x"}, compute)
            second = cached_workflow(root, "demo", {"query": "x"}, compute)
            (root / ".codeatlas.yml").write_text("cache:\n  ttl_seconds: 300\n", encoding="utf-8")
            third = cached_workflow(root, "demo", {"query": "x"}, compute)

        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertFalse(third["cache"]["hit"])
        self.assertEqual(calls["count"], 2)

    def test_import_external_index_adds_symbols_and_edges(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            external_path = root / "external-index.json"
            external_path.write_text(
                json.dumps(
                    {
                        "symbols": [
                            {
                                "qualified_name": "external.Service.handle",
                                "name": "handle",
                                "kind": "FUNCTION",
                                "file_path": "external/service.go",
                                "line_start": 3,
                                "line_end": 5,
                            }
                        ],
                        "edges": [
                            {
                                "source": "external.Service.handle",
                                "target": "external.Transport.call",
                                "type": "CALLS",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = import_external_index(root, external_path)
            query = structural_query(root, "calls:handle")
            outline = source_outline(root, "handle")

        self.assertEqual(report["format"], "generic-json")
        self.assertEqual(report["symbols"], 1)
        self.assertEqual(query["type"], "outgoing")
        self.assertTrue(query["edges"])
        self.assertEqual(outline["files"][0]["file_path"], "external/service.go")

    def test_import_scip_style_fixture_adds_relationships(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            fixture = Path(__file__).parent / "fixtures" / "scip-index.json"
            report = import_external_index(root, fixture)
            query = structural_query(root, "calls:route_get_health")
            outline = source_outline(root, "callHealth")

        self.assertEqual(report["format"], "scip-json")
        self.assertGreaterEqual(report["symbols"], 2)
        self.assertEqual(query["type"], "outgoing")
        self.assertTrue(query["edges"])
        self.assertEqual(outline["files"][0]["file_path"], "web/router.ts")


class BenchmarkAndMcpTests(CodeAtlasTestCase):
    def test_benchmark_uses_actual_repository_metrics(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            report = Benchmarker().run(root, query="create_order")

        self.assertEqual(report.files_scanned, 3)
        self.assertGreater(report.indexing_time_seconds, 0)
        self.assertGreaterEqual(report.estimated_tokens_before, report.estimated_tokens_after)
        self.assertIn("snippets returned", report.retrieval_accuracy)

    def test_mcp_handlers_return_context_and_stats(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            handlers = create_tool_handlers(root)
            context = handlers["get_code_context"]("create_order", max_tokens=1000, depth=2)
            stats = handlers["repository_stats"]()

        self.assertEqual(context["snippets"][0]["symbol_name"], "create_order")
        self.assertEqual(stats["files_indexed"], 3)


class RepositoryMemoryTests(CodeAtlasTestCase):
    def test_memory_indexer_extracts_git_history_and_documents(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            report = MemoryQueryEngine().index_memory(root)

        self.assertTrue(report.git_available)
        self.assertEqual(report.commits_indexed, 2)
        self.assertEqual(report.documents_indexed, 2)
        self.assertGreaterEqual(report.entities_indexed, 6)
        self.assertGreaterEqual(report.evidence_indexed, 4)

    def test_history_ownership_and_decisions_are_evidence_backed(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            memory = MemoryQueryEngine()
            memory.index_memory(root)
            history = memory.history(root, "auth")
            ownership = memory.ownership(root, "auth")
            decisions = memory.decisions(root, "Why was Redis introduced?")
            context = memory.compressed_context(root, "auth", max_tokens=1000)

        self.assertTrue(any("Redis" in event.title or "Redis" in event.summary for event in history))
        self.assertEqual(ownership[0].developer, "Bob Reviewer")
        self.assertGreater(ownership[0].evidence[0].confidence, 0)
        self.assertNotIn("No evidence-backed", decisions[0].answer)
        self.assertTrue(decisions[0].evidence)
        self.assertTrue(context.evidence)

    def test_mcp_memory_handlers_are_available(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root)
            handlers = create_tool_handlers(root)
            history = handlers["get_history"]("auth")
            decisions = handlers["get_decisions"]("Redis")
            context = handlers["get_context"]("auth", max_tokens=1000)

        self.assertIn("get_ownership", handlers)
        self.assertTrue(history)
        self.assertTrue(decisions[0]["evidence"])
        self.assertEqual(context["query"], "auth")

    def test_git_nexus_related_files_hotspots_and_fts_search(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            memory = MemoryQueryEngine()
            memory.index_memory(root)
            search = memory.search_memory(root, "authentication retry state")
            related = memory.related_files(root, "app/auth.py")
            hotspots = memory.hotspots(root)
            summary = memory.component_summary(root, "auth")

        self.assertTrue(any("Redis" in item["title"] for item in search))
        self.assertIn(
            "docs/adr/0001-redis-auth.md",
            {link.related_file_path for link in related},
        )
        self.assertTrue(hotspots)
        self.assertIn("auth", summary.summary.lower())

    def test_impact_report_uses_changed_files_history_and_token_savings(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            memory = MemoryQueryEngine()
            memory.index_memory(root)
            (root / "app" / "auth.py").write_text(
                textwrap.dedent(
                    '''
                    class AuthService:
                        def login(self, token):
                            if not token:
                                return None
                            return token
                    '''
                ).lstrip(),
                encoding="utf-8",
            )
            report = memory.impact(root, base_ref="HEAD")

        self.assertEqual(report.changed_files, ("app/auth.py",))
        self.assertEqual(report.risk_level, "high")
        self.assertEqual(report.impacted_files[0].owners[0].developer, "Bob Reviewer")
        self.assertGreaterEqual(
            report.token_report.baseline_tokens,
            report.token_report.optimized_tokens,
        )

    def test_mcp_git_nexus_handlers_are_available(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root)
            (root / "app" / "auth.py").write_text("# changed\n", encoding="utf-8")
            handlers = create_tool_handlers(root)
            impact = handlers["get_impact"]("HEAD")
            hotspots = handlers["get_hotspots"](limit=3)
            nexus = handlers["get_nexus"]("auth")
            status = handlers["get_index_status"]()
            query = handlers["query_code_graph"]("calls:login")
            rules = handlers["run_rules"](limit=3)
            outline = handlers["get_source_outline"]("login")
            plan = handlers["get_verification_plan"]("HEAD")

        self.assertIn("get_impact", handlers)
        self.assertIn("get_index_status", handlers)
        self.assertIn("query_code_graph", handlers)
        self.assertIn("find_dead_code", handlers)
        self.assertIn("get_routes", handlers)
        self.assertIn("get_context_pack", handlers)
        self.assertIn("get_verification_plan", handlers)
        self.assertIn("run_rules", handlers)
        self.assertIn("get_source_outline", handlers)
        self.assertIn("import_code_index", handlers)
        self.assertEqual(impact["changed_files"], ("app/auth.py",))
        self.assertTrue(hotspots)
        self.assertEqual(nexus["component"], "auth")
        self.assertTrue(status["indexed"])
        self.assertEqual(query["type"], "outgoing")
        self.assertIn("findings", rules)
        self.assertTrue(outline["files"])
        self.assertIn("app/auth.py", plan["changed_files"])


class VisualizationTests(CodeAtlasTestCase):
    def test_visualization_assets_are_split_and_rendered(self) -> None:
        self.assertTrue((ASSET_DIR / "visualization.html").exists())
        self.assertTrue((ASSET_DIR / "visualization.css").exists())
        self.assertTrue((ASSET_DIR / "visualization.js").exists())
        html = (ASSET_DIR / "visualization.html").read_text(encoding="utf-8")
        css = (ASSET_DIR / "visualization.css").read_text(encoding="utf-8")
        js = (ASSET_DIR / "visualization.js").read_text(encoding="utf-8")
        self.assertIn("{{ CODEATLAS_CSS }}", html)
        self.assertIn("{{ CODEATLAS_JS }}", html)
        self.assertIn(".command-palette-shell", css)
        self.assertIn("function renderStaleBanner", js)
        self.assertIn("legend-dot legend-owned", html)
        self.assertIn('id="legendToggleBtn"', html)
        self.assertIn('id="emptyMapOverlay"', html)
        self.assertIn('id="focusBreadcrumb"', html)
        self.assertIn('id="edgeHoverTooltip"', html)
        self.assertIn('id="detailTabs"', html)
        self.assertIn('data-detail-tab="evidence"', html)
        self.assertIn(".legend-dot", css)
        self.assertIn(".legend.auto-compact", css)
        self.assertIn(".empty-map-overlay", css)
        self.assertIn(".focus-breadcrumb", css)
        self.assertIn(".edge-hover-tooltip", css)
        self.assertIn(".detail-tabs", css)
        self.assertIn(".detail-card.tab-filtered-out", css)
        self.assertIn("scrollbar-width: thin", css)
        self.assertIn("::-webkit-scrollbar-thumb", css)
        self.assertIn("scrollbar-gutter: stable", css)
        self.assertIn("function applyDetailTabFilter", js)
        self.assertIn("edgeDashPattern", js)
        self.assertNotIn("Blue: owned", HTML_APP)
        self.assertNotIn("Violet: third-party", HTML_APP)
        self.assertEqual(HTML_APP, render_visualization_app())
        self.assertNotIn("{{ CODEATLAS_CSS }}", HTML_APP)
        self.assertNotIn("{{ CODEATLAS_JS }}", HTML_APP)

    def test_graph_worker_filter_matches_expected_small_fixture(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        start = HTML_APP.index("function graphWorkerSource()")
        ret = HTML_APP.index("return `", start)
        end = HTML_APP.index("`;", ret + len("return `"))
        worker_source = HTML_APP[ret + len("return `") : end]
        script = textwrap.dedent(
            f"""
            const {{ Worker }} = require('worker_threads');
            const source = {json.dumps(worker_source)};
            const shimmed = `
              const {{ parentPort }} = require('worker_threads');
              globalThis.self = {{
                onmessage: null,
                postMessage: message => parentPort.postMessage(message)
              }};
              parentPort.on('message', data => globalThis.self.onmessage({{ data }}));
              ${{source}}
            `;
            const worker = new Worker(shimmed, {{ eval: true }});
            const timer = setTimeout(() => {{
              console.error('worker timed out');
              process.exit(1);
            }}, 2000);
            const payload = {{
              requestId: 7,
              nodes: [
                {{ id: 'a', label: 'a', category: 'owned', type: 'component', size: 20, metrics: {{ files: 2 }} }},
                {{ id: 'b', label: 'b', category: 'owned', type: 'component', size: 12, metrics: {{ files: 1 }} }},
                {{ id: 'c', label: 'c', category: 'third_party', type: 'external', size: 10, metrics: {{}} }},
                {{ id: 'd', label: 'd', category: 'team', type: 'component', size: 10, metrics: {{}} }}
              ],
              edges: [
                {{ id: 'e1', source: 'a', target: 'b', type: 'imports', weight: 3, categories: ['component'] }},
                {{ id: 'e2', source: 'b', target: 'c', type: 'imports', weight: 4, categories: ['component'] }},
                {{ id: 'e3', source: 'a', target: 'd', type: 'api_call', weight: 1, categories: ['api'] }}
              ],
              categoryVisibility: {{ owned: true, team: true, third_party: false }},
              connectionVisibility: {{ component: true, api: true, functions: true, projects: true }},
              hidden: [],
              connectedOnly: true,
              minEdgeWeight: 1,
              focusSelection: false,
              focusSeeds: [],
              traceMode: null,
              focusHops: 1,
              nodeBudget: 2
            }};
            worker.on('message', message => {{
              clearTimeout(timer);
              console.log(JSON.stringify(message));
              worker.terminate();
            }});
            worker.on('error', error => {{
              console.error(error.stack || error.message);
              process.exit(1);
            }});
            worker.postMessage(payload);
            """
        )
        result = subprocess.run(
            [node, "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout.strip())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["requestId"], 7)
        self.assertEqual(payload["nodeIds"], ["a", "b"])
        self.assertEqual(payload["edgeIds"], ["e1"])
        self.assertEqual(payload["categoryHidden"], 1)
        self.assertEqual(payload["counts"]["budgetHidden"], 1)

    def test_visualization_map_contains_architecture_and_commit_graphs(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root)
            payload = VisualizationService().build_map(root)

        component_ids = {node["id"] for node in payload["component_graph"]["nodes"]}
        commit_types = {node["type"] for node in payload["commit_graph"]["nodes"]}
        self.assertIn("auth.py", component_ids)
        self.assertIn("docs", component_ids)
        self.assertTrue(payload["component_graph"]["edges"])
        example_edges = [
            edge for edge in payload["component_graph"]["edges"] if edge.get("examples")
        ]
        call_examples = [
            example
            for edge in example_edges
            for example in edge["examples"]
            if example["type"] == "calls"
        ]
        self.assertTrue(example_edges)
        self.assertTrue(call_examples)
        self.assertTrue(any(example["arguments"] == ["total"] for example in call_examples))
        self.assertTrue(any(example["target"].get("signature") for example in call_examples))
        self.assertIn("commit", commit_types)
        self.assertIn("developer", commit_types)
        self.assertEqual(payload["stats"]["files"], len(payload["inventory"]["files"]))
        self.assertEqual(payload["stats"]["symbols"], len(payload["inventory"]["symbols"]))
        self.assertEqual(payload["stats"]["commits"], len(payload["inventory"]["commits"]))
        self.assertTrue(any(item["path"] == "app/auth.py" for item in payload["inventory"]["files"]))
        self.assertTrue(any(item["qualified_name"].endswith("AuthService.login") for item in payload["inventory"]["symbols"]))
        self.assertIn("diagnostics", payload)
        self.assertIn("python", payload["diagnostics"]["supported_languages"])
        self.assertIn("language_counts", payload["diagnostics"])
        self.assertIn("files_skipped", payload["diagnostics"])
        self.assertIn("parser_errors", payload["diagnostics"])
        self.assertIn("external_dependencies", payload["diagnostics"])
        self.assertIn("stale", payload["diagnostics"])

    def test_visualization_compare_marks_architecture_changes(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            service = VisualizationService()
            payload = service.build_compare(
                root,
                base_ref="HEAD~1",
                head_ref="HEAD",
            )
            cached_payload = service.build_compare(
                root,
                base_ref="HEAD~1",
                head_ref="HEAD",
            )

        head_nodes = {node["id"]: node for node in payload["head"]["graph"]["nodes"]}
        self.assertGreaterEqual(payload["summary"]["added_nodes"], 1)
        self.assertEqual(head_nodes["auth.py"]["change"], "added")
        self.assertEqual(payload["base"]["ref"], "HEAD~1")
        self.assertEqual(payload["head"]["ref"], "HEAD")
        self.assertEqual(payload["summary"]["cache"]["base"], "miss")
        self.assertEqual(payload["summary"]["cache"]["head"], "miss")
        self.assertEqual(cached_payload["summary"]["cache"]["base"], "hit")
        self.assertEqual(cached_payload["summary"]["cache"]["head"], "hit")

    def test_visualization_chat_answers_from_code_and_memory(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root)
            payload = VisualizationService().ask(root, "AuthService Redis auth")

        self.assertIn("Question: AuthService Redis auth", payload["answer"])
        self.assertTrue(payload["code"])
        self.assertTrue(payload["evidence"])
        self.assertTrue(any("AuthService" in item["symbol"] for item in payload["code"]))

    def test_visualization_server_serves_graph_json(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root)
            try:
                server = create_visualization_server(root, host="127.0.0.1", port=0)
            except PermissionError as exc:
                raise self.skipTest("local socket binding is blocked in this sandbox") from exc
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                with urlopen(f"http://127.0.0.1:{port}/api/graph", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(payload["repo"]["name"], root.name)
        self.assertGreaterEqual(payload["stats"]["files"], 1)
        self.assertTrue(payload["component_graph"]["nodes"])

    def test_mcp_visual_map_handler_is_available(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root)
            handlers = create_tool_handlers(root)
            payload = handlers["get_visual_map"]()

        self.assertIn("get_visual_map", handlers)
        self.assertTrue(payload["component_graph"]["nodes"])
        self.assertTrue(payload["commit_graph"]["nodes"])

    def test_visualization_page_exposes_filters_and_edge_selection(self) -> None:
        self.assertIn('id="componentFilters"', HTML_APP)
        self.assertIn('id="categoryFilters"', HTML_APP)
        self.assertIn('id="connectionFilters"', HTML_APP)
        self.assertIn('id="showAllConnectionsBtn"', HTML_APP)
        self.assertIn('id="onlyApiConnectionsBtn"', HTML_APP)
        self.assertIn('id="mapLensSelect"', HTML_APP)
        self.assertIn('value="subway"', HTML_APP)
        self.assertIn('id="applyLensBtn"', HTML_APP)
        self.assertIn('id="smartSimplifyBtn"', HTML_APP)
        self.assertIn('id="minWeightInput"', HTML_APP)
        self.assertIn('id="nodeBudgetSelect"', HTML_APP)
        self.assertIn('id="focusSelectionInput"', HTML_APP)
        self.assertIn('id="focusHopsSelect"', HTML_APP)
        self.assertIn('id="mapStatusPanel"', HTML_APP)
        self.assertIn('id="emptyMapOverlay"', HTML_APP)
        self.assertIn('id="emptyMapResetBtn"', HTML_APP)
        self.assertIn("currentEmptyMapState", HTML_APP)
        self.assertIn("updateEmptyMapOverlay", HTML_APP)
        self.assertIn("resetMapView", HTML_APP)
        self.assertIn("showEveryNode", HTML_APP)
        self.assertIn('id="resetViewBtn"', HTML_APP)
        self.assertIn('id="showEveryNodeBtn"', HTML_APP)
        self.assertIn('id="legendToggleBtn"', HTML_APP)
        self.assertIn("toggleLegendCollapsed", HTML_APP)
        self.assertIn("legendNeedsCompaction", HTML_APP)
        self.assertIn("rectsOverlap", HTML_APP)
        self.assertIn("CATEGORY_FILTERS", HTML_APP)
        self.assertIn("CONNECTION_FILTERS", HTML_APP)
        self.assertIn("LENS_LABELS", HTML_APP)
        self.assertIn("categoryVisibility", HTML_APP)
        self.assertIn("{ id: 'owned'", HTML_APP)
        self.assertIn("{ id: 'team'", HTML_APP)
        self.assertIn("{ id: 'third_party'", HTML_APP)
        self.assertIn("{ id: 'docs_config'", HTML_APP)
        self.assertIn("third_party: false", HTML_APP)
        self.assertIn("docs_config: false", HTML_APP)
        self.assertIn("COMMON_NODE_PATTERNS", HTML_APP)
        self.assertIn("DEFAULT_TEAM_PREFIXES", HTML_APP)
        self.assertIn("applyCategoryPreset", HTML_APP)
        self.assertIn("docutils", HTML_APP)
        self.assertIn("numpy", HTML_APP)
        self.assertIn("pandas", HTML_APP)
        self.assertIn("api[-_]?ref", HTML_APP)
        self.assertIn("requirements?", HTML_APP)
        self.assertIn("team dependencies", HTML_APP)
        self.assertIn("third-party packages", HTML_APP)
        self.assertIn("connectionVisibility", HTML_APP)
        self.assertIn("visibilityStatus", HTML_APP)
        self.assertIn("activeLens", HTML_APP)
        self.assertIn("minEdgeWeight", HTML_APP)
        self.assertIn("nodeBudget", HTML_APP)
        self.assertIn("connectedOnly", HTML_APP)
        self.assertIn('id="connectedOnlyInput"', HTML_APP)
        self.assertIn("connected_only", HTML_APP)
        self.assertIn("Connected only removes isolated nodes", HTML_APP)
        self.assertIn("nodeBudget: 180", HTML_APP)
        self.assertIn("nodeBudget: 12", HTML_APP)
        self.assertIn("focusSelection", HTML_APP)
        self.assertIn("traceMode", HTML_APP)
        self.assertIn("renderCategoryFilters", HTML_APP)
        self.assertIn("renderConnectionFilters", HTML_APP)
        self.assertIn("renderComponentFilterList", HTML_APP)
        self.assertIn("renderComponentFilterWindow", HTML_APP)
        self.assertIn("virtual-filter-list", HTML_APP)
        self.assertIn("COMPONENT_ROW_HEIGHT", HTML_APP)
        self.assertIn("nodeVisibilityReason", HTML_APP)
        self.assertIn("hidden: connected-only", HTML_APP)
        self.assertIn("updateScaleControls", HTML_APP)
        self.assertIn("applyMapLens", HTML_APP)
        self.assertIn("applySmartSimplify", HTML_APP)
        self.assertIn("normalizeClassificationConfig", HTML_APP)
        self.assertIn("owned_prefixes", HTML_APP)
        self.assertIn("hide_packages", HTML_APP)
        self.assertIn("category-muted", HTML_APP)
        self.assertIn("revealSingleNodeFromHiddenCategory", HTML_APP)
        self.assertIn("nodeCategory(other) === category", HTML_APP)
        self.assertIn("state.hiddenNodeIds.add(other.id)", HTML_APP)
        self.assertIn("Check it to show this node", HTML_APP)
        self.assertNotIn("checkbox.disabled = disabled", HTML_APP)
        self.assertIn("nodeCategory", HTML_APP)
        self.assertIn("isServiceNode", HTML_APP)
        self.assertIn("setAllCategoryVisibility", HTML_APP)
        self.assertIn("setAllConnectionVisibility", HTML_APP)
        self.assertIn("setConnectionVisibilitySet", HTML_APP)
        self.assertIn("isCategoryVisible", HTML_APP)
        self.assertIn("isConnectionVisible", HTML_APP)
        self.assertIn("isEdgeConnectionVisible", HTML_APP)
        self.assertIn("edgePassesScale", HTML_APP)
        self.assertIn("renderMapStatus", HTML_APP)
        self.assertIn("appendMapMetric", HTML_APP)
        self.assertIn("visibilityReasonLines", HTML_APP)
        self.assertIn("formatCount", HTML_APP)
        self.assertIn("emptyLimitCounts", HTML_APP)
        self.assertIn("hidden by the node budget", HTML_APP)
        self.assertIn("edgePrimaryConnectionCategories", HTML_APP)
        self.assertIn("edgeCrossesProjectBoundary", HTML_APP)
        self.assertNotIn("primaryVisible && projectVisible", HTML_APP)
        self.assertIn("applyGraphLimit", HTML_APP)
        self.assertIn("nodeIndex: new Map()", HTML_APP)
        self.assertIn("rebuildNodeIndex", HTML_APP)
        self.assertIn("rebuildGraphCache", HTML_APP)
        self.assertIn("nodesByIdForPanel", HTML_APP)
        self.assertIn("visibleNodesById", HTML_APP)
        self.assertIn("categoryByNodeId", HTML_APP)
        self.assertIn("prepareGraphLayoutForCurrentFilters", HTML_APP)
        self.assertIn("nodes.length > 1000 ? 12", HTML_APP)
        self.assertIn("selectedFocusSeeds", HTML_APP)
        self.assertIn("expandNeighborhood", HTML_APP)
        self.assertIn("topNodeIds", HTML_APP)
        self.assertIn("edgeBudgetRank", HTML_APP)
        self.assertIn("const missing = endpoints.filter", HTML_APP)
        self.assertIn("edgeContrastInput", HTML_APP)
        self.assertIn("edgeContrastLabel", HTML_APP)
        self.assertIn("edgeBundlingInput", HTML_APP)
        self.assertIn("edgeContrastRatio", HTML_APP)
        self.assertIn("adjustEdgeAlpha", HTML_APP)
        self.assertIn("edgeWidthScale", HTML_APP)
        self.assertIn("edgeRenderItemsForPanel", HTML_APP)
        self.assertIn("edgeBundlePlan", HTML_APP)
        self.assertIn("LOW_DETAIL_EDGE_ALPHA_FLOOR = 0.18", HTML_APP)
        self.assertIn("drawEdgeRenderItem(item, transform, rect, side, lowDetail)", HTML_APP)
        self.assertIn("edgeBundlePlan(edge, nodesById, edges, lowDetail, side)", HTML_APP)
        self.assertIn("edgeTouchesSelectedNode(edge, side)", HTML_APP)
        self.assertIn("visibleEdgeAlpha", HTML_APP)
        self.assertIn("Math.max(alpha, LOW_DETAIL_EDGE_ALPHA_FLOOR)", HTML_APP)
        self.assertIn('id="commandPalette"', HTML_APP)
        self.assertIn('id="commandPaletteInput"', HTML_APP)
        self.assertIn("openCommandPalette", HTML_APP)
        self.assertIn("handleCommandPaletteKeydown", HTML_APP)
        self.assertIn("commandPaletteActions", HTML_APP)
        self.assertIn("Copy current map link", HTML_APP)
        self.assertIn("Copy compact map link", HTML_APP)
        self.assertIn("Copy clean map link", HTML_APP)
        self.assertIn("Reset shared view state", HTML_APP)
        self.assertIn("Show more nodes", HTML_APP)
        self.assertIn("Reduce map complexity", HTML_APP)
        self.assertIn("Focus evidence search", HTML_APP)
        self.assertIn("Save view preset", HTML_APP)
        self.assertIn("Export view presets", HTML_APP)
        self.assertIn("Import view presets", HTML_APP)
        self.assertIn("Toggle stale UI auto-reload", HTML_APP)
        self.assertIn("Hide third-party", HTML_APP)
        self.assertIn("Trace callers", HTML_APP)
        self.assertIn("Pin current trace", HTML_APP)
        self.assertIn("Clear pinned trace", HTML_APP)
        self.assertIn("Copy agent pack", HTML_APP)
        self.assertIn("commandActionScore", HTML_APP)
        self.assertIn("commandActionHaystack", HTML_APP)
        self.assertIn("command-group", HTML_APP)
        self.assertIn('id="viewPresetSelect"', HTML_APP)
        self.assertIn("VIEW_PRESETS_KEY", HTML_APP)
        self.assertIn("renderViewPresets", HTML_APP)
        self.assertIn("currentViewPresetPayload", HTML_APP)
        self.assertIn("applyViewPresetPayload", HTML_APP)
        self.assertIn("persistViewPresets", HTML_APP)
        self.assertIn("exportViewPresets", HTML_APP)
        self.assertIn("importViewPresetsFromFile", HTML_APP)
        self.assertIn('id="viewPresetImportInput"', HTML_APP)
        self.assertIn("URL_STATE_KEYS", HTML_APP)
        self.assertIn("readUrlStateFromLocation", HTML_APP)
        self.assertIn("encodeCompactUrlState", HTML_APP)
        self.assertIn("decodeCompactUrlState", HTML_APP)
        self.assertIn("currentUrlStatePayload", HTML_APP)
        self.assertIn("shouldUseCompactUrlState", HTML_APP)
        self.assertIn("applyPendingUrlState", HTML_APP)
        self.assertIn("writeUrlStateToLocation", HTML_APP)
        self.assertIn("clearSharedViewState", HTML_APP)
        self.assertIn("pinnedTraceToUrlValue", HTML_APP)
        self.assertIn("selectedNodeForUrl", HTML_APP)
        self.assertIn("viewportToUrlValue", HTML_APP)
        self.assertIn("window.history.replaceState", HTML_APP)
        self.assertIn("nodeVisibilityReason", HTML_APP)
        self.assertIn("hidden: node budget", HTML_APP)
        self.assertIn("stableKindText", HTML_APP)
        self.assertIn("kind.textContent = stableKindText", HTML_APP)
        self.assertIn("drawMinimapOverlay", HTML_APP)
        self.assertIn("minimapBottomInset", HTML_APP)
        self.assertIn("drawMinimapViewport", HTML_APP)
        self.assertIn("minimapHitAt", HTML_APP)
        self.assertIn("panGraphToMinimapPoint", HTML_APP)
        self.assertIn("activeMinimapNav", HTML_APP)
        self.assertIn("trace-timeline", HTML_APP)
        self.assertIn("traceTimelineEdgesForNode", HTML_APP)
        self.assertIn("detailSearchInput", HTML_APP)
        self.assertIn('id="detailTabs"', HTML_APP)
        self.assertIn("data-detail-tab=\"flow\"", HTML_APP)
        self.assertIn("activeDetailTab", HTML_APP)
        self.assertIn("setDetailTab", HTML_APP)
        self.assertIn("detailTabForTitle", HTML_APP)
        self.assertIn("applyDetailFilters", HTML_APP)
        self.assertIn("detailTab: cleanUrlChoice", HTML_APP)
        self.assertIn("Show Evidence tab", HTML_APP)
        self.assertIn("applyDetailSearchFilter", HTML_APP)
        self.assertIn("applyDetailTabFilter", HTML_APP)
        self.assertIn("nodeDetailCache", HTML_APP)
        self.assertIn("cachedNodeDetail", HTML_APP)
        self.assertIn("invalidateNodeDetailCache", HTML_APP)
        self.assertIn("detail-filter-empty", HTML_APP)
        self.assertIn("detail-tab-empty", HTML_APP)
        self.assertIn("empty-selection-actions", HTML_APP)
        self.assertIn("requestGraphWorkerFilter", HTML_APP)
        self.assertIn("graphWorkerSource", HTML_APP)
        self.assertIn("Graph worker", HTML_APP)
        self.assertIn('id="perfPanel"', HTML_APP)
        self.assertIn("renderPerfPanel", HTML_APP)
        self.assertIn("lastDrawMs", HTML_APP)
        self.assertIn("lastFilterMs", HTML_APP)
        self.assertIn("pinnedTraceSubgraph", HTML_APP)
        self.assertIn("rebuildPinnedTraceCache", HTML_APP)
        self.assertIn("isPinnedTraceEdge", HTML_APP)
        self.assertIn("Large graph guard active", HTML_APP)
        self.assertIn("applyLargeGraphGuard", HTML_APP)
        self.assertIn("hitEdgeRenderItemAt", HTML_APP)
        self.assertIn("renderBundleSelection", HTML_APP)
        self.assertIn("lowDetailMode", HTML_APP)
        self.assertIn("shouldDrawNodeLabel", HTML_APP)
        self.assertIn("Bundled edges show x-counts", HTML_APP)
        self.assertIn("1.15 + Math.log2", HTML_APP)
        self.assertIn("edgeDashPattern", HTML_APP)
        self.assertIn("ctx.setLineDash(edgeDashPattern", HTML_APP)
        self.assertIn("return [10, 5]", HTML_APP)
        self.assertIn("edgeConnectionCategories", HTML_APP)
        self.assertIn("connectionFiltersFocused", HTML_APP)
        self.assertIn("connectionFiltersRestrictNodes", HTML_APP)
        self.assertIn("if (isConnectionVisible('component')) return false;", HTML_APP)
        self.assertIn("endpointIdsForEdges", HTML_APP)
        self.assertIn("connectionHelp", HTML_APP)
        self.assertIn("keeps matching component nodes visible", HTML_APP)
        self.assertIn("it does not delete component/function edges by itself", HTML_APP)
        self.assertIn("['api', 'functions', 'graphql', 'projects']", HTML_APP)
        self.assertIn('id="refreshBtn"', HTML_APP)
        self.assertIn('id="filterPanelToggle"', HTML_APP)
        self.assertIn('id="detailPanelResizer"', HTML_APP)
        self.assertIn('id="nodeContextMenu"', HTML_APP)
        self.assertIn('id="contextEditNodeBtn"', HTML_APP)
        self.assertIn('id="contextServiceNodeBtn"', HTML_APP)
        self.assertIn('id="contextOwnedNodeBtn"', HTML_APP)
        self.assertIn('id="contextTeamNodeBtn"', HTML_APP)
        self.assertIn('id="contextThirdPartyNodeBtn"', HTML_APP)
        self.assertIn('id="contextHideNodeBtn"', HTML_APP)
        self.assertIn("USER_NODE_OVERRIDES_KEY", HTML_APP)
        self.assertIn("EXPECTED_UI_VERSION", HTML_APP)
        self.assertIn("handleGraphContextMenu", HTML_APP)
        self.assertIn("contextNodeAt", HTML_APP)
        self.assertIn("showNodeContextMenu", HTML_APP)
        self.assertIn("hideNodeContextMenu", HTML_APP)
        self.assertIn("openNodeEditor", HTML_APP)
        self.assertIn("renderNodeEditSelection", HTML_APP)
        self.assertIn("saveNodeEditFromForm", HTML_APP)
        self.assertIn("toggleNodeService", HTML_APP)
        self.assertIn("classifyContextNode", HTML_APP)
        self.assertIn("/api/classification", HTML_APP)
        self.assertIn("/api/classification/restore", HTML_APP)
        self.assertIn("showUndoToast", HTML_APP)
        self.assertIn("restoreClassificationFromToast", HTML_APP)
        self.assertIn('id="undoToast"', HTML_APP)
        self.assertIn("previous_classification", HTML_APP)
        self.assertIn("classificationPackageName", HTML_APP)
        self.assertIn("applyConfigUpdate", HTML_APP)
        self.assertIn("resetNodeOverride", HTML_APP)
        self.assertIn("loadNodeOverrides", HTML_APP)
        self.assertIn("persistNodeOverrides", HTML_APP)
        self.assertIn("applyNodeOverride", HTML_APP)
        self.assertIn("Treat as separate service", HTML_APP)
        self.assertIn("Mark as service", HTML_APP)
        self.assertIn("DETAIL_WIDTH_KEY", HTML_APP)
        self.assertIn("loadDetailPanelWidth", HTML_APP)
        self.assertIn("applyDetailPanelWidth", HTML_APP)
        self.assertIn("handleDetailResizeStart", HTML_APP)
        self.assertIn("handleDetailResizeMove", HTML_APP)
        self.assertIn("handleDetailResizeEnd", HTML_APP)
        self.assertIn("resizing-detail", HTML_APP)
        self.assertIn("var(--detail-width)", HTML_APP)
        self.assertIn("filter-header", HTML_APP)
        self.assertIn("filter-panel-body", HTML_APP)
        self.assertIn("filter-sticky", HTML_APP)
        self.assertIn("filter-section", HTML_APP)
        self.assertIn('id="stickyLensLabel"', HTML_APP)
        self.assertIn("toolbar-group", HTML_APP)
        self.assertIn("grid-template-columns: 44px", HTML_APP)
        filter_panel = HTML_APP.split('<aside class="filter-panel">', 1)[1].split("</aside>", 1)[0]
        self.assertIn('id="connectionFilters"', filter_panel)
        self.assertNotIn('id="compareMapControls"', filter_panel)
        self.assertNotIn('id="baseCommitSelect"', filter_panel)
        self.assertNotIn('id="headCommitSelect"', filter_panel)
        self.assertNotIn('id="compareStatus"', filter_panel)
        self.assertIn('id="addConnectionBtn"', HTML_APP)
        self.assertIn('id="saveArchitectureBtn"', HTML_APP)
        self.assertIn('id="savedArchitectures"', HTML_APP)
        self.assertIn("sidebar-collapsed", HTML_APP)
        self.assertIn("toggleFilterPanel", HTML_APP)
        self.assertIn("updateFilterPanelToggle", HTML_APP)
        self.assertIn('id="helpTooltip"', HTML_APP)
        self.assertIn("help-tooltip", HTML_APP)
        self.assertIn("help-icon", HTML_APP)
        self.assertIn(".stat > span:not(.help-icon)", HTML_APP)
        self.assertIn("font-size: 0", HTML_APP)
        self.assertIn("option, .detail-resizer", HTML_APP)
        self.assertIn("data-help", HTML_APP)
        self.assertIn("setHelp", HTML_APP)
        self.assertIn("attachHelpIcon", HTML_APP)
        self.assertIn(".help-icon[data-help]", HTML_APP)
        self.assertIn("handleHelpTooltipOver", HTML_APP)
        self.assertIn("handleHelpTooltipFocus", HTML_APP)
        self.assertIn("positionHelpTooltip", HTML_APP)
        self.assertIn("detailSectionHelp", HTML_APP)
        self.assertIn("edgeHelp", HTML_APP)
        self.assertIn("edgeTypeHelp", HTML_APP)
        self.assertIn("weightHelp", HTML_APP)
        self.assertIn("exampleHelp", HTML_APP)
        self.assertIn("detailLineHelp", HTML_APP)
        self.assertIn("Evidence / Confidence", HTML_APP)
        self.assertIn("appendEdgeEvidenceSection", HTML_APP)
        self.assertIn("edgeEvidencePanel", HTML_APP)
        self.assertIn("edgeConfidenceScore", HTML_APP)
        self.assertIn("edgeProofItems", HTML_APP)
        self.assertIn('id="edgeHoverTooltip"', HTML_APP)
        self.assertIn("updateEdgeHoverFromPointer", HTML_APP)
        self.assertIn("renderEdgeHoverTooltip", HTML_APP)
        self.assertIn("isHoveredEdge", HTML_APP)
        self.assertIn("isHoveredBundle", HTML_APP)
        self.assertIn("traceSelectedEdge", HTML_APP)
        self.assertIn("Trace Flow", HTML_APP)
        self.assertIn("traceNodeMode", HTML_APP)
        self.assertIn("traceModeSubgraph", HTML_APP)
        self.assertIn('id="focusBreadcrumb"', HTML_APP)
        self.assertIn("updateFocusBreadcrumb", HTML_APP)
        self.assertIn("clearFocusBreadcrumb", HTML_APP)
        self.assertIn("directionalTrace", HTML_APP)
        self.assertIn("evidence-panel", HTML_APP)
        self.assertIn("confidence-meter", HTML_APP)
        self.assertIn('id="repoQuestions"', HTML_APP)
        self.assertIn("REPO_QUESTIONS", HTML_APP)
        self.assertIn("renderRepoQuestions", HTML_APP)
        self.assertIn("runStructuralQuery", HTML_APP)
        self.assertIn("/api/query", HTML_APP)
        self.assertIn("Dead code", HTML_APP)
        self.assertIn("Routes", HTML_APP)
        self.assertIn("Context pack", HTML_APP)
        self.assertIn("Verify plan", HTML_APP)
        self.assertIn("Rule checks", HTML_APP)
        self.assertIn("Source outline", HTML_APP)
        self.assertIn("runToolWorkflow", HTML_APP)
        self.assertIn("workflowStatus", HTML_APP)
        self.assertIn("renderWorkflowResult", HTML_APP)
        self.assertIn("renderContextPackResult", HTML_APP)
        self.assertIn("renderVerifyPlanResult", HTML_APP)
        self.assertIn("renderRulesResult", HTML_APP)
        self.assertIn("renderSourceOutlineResult", HTML_APP)
        self.assertIn("renderWorkflowLoading", HTML_APP)
        self.assertIn("renderWorkflowError", HTML_APP)
        self.assertIn("appendWorkflowTabs", HTML_APP)
        self.assertIn("renderRulesFilters", HTML_APP)
        self.assertIn("renderVerificationCommands", HTML_APP)
        self.assertIn("appendWorkflowEmpty", HTML_APP)
        self.assertIn("workflow-mode", HTML_APP)
        self.assertIn("workflow-panel", HTML_APP)
        self.assertIn("workflow-stat-grid", HTML_APP)
        self.assertIn("workflow-progress", HTML_APP)
        self.assertIn("workflow-tabs", HTML_APP)
        self.assertIn("workflow-filter-tabs", HTML_APP)
        self.assertIn("workflow-copy-btn", HTML_APP)
        self.assertIn("workflow-empty", HTML_APP)
        self.assertIn('id="staleBanner"', HTML_APP)
        self.assertIn("renderStaleBanner", HTML_APP)
        self.assertIn("Index may be stale", HTML_APP)
        self.assertIn("Restart CodeAtlas UI server", HTML_APP)
        self.assertIn("expected ' + EXPECTED_UI_VERSION", HTML_APP)
        self.assertIn("running ' + (build.ui_version || 'unknown')", HTML_APP)
        self.assertIn("stale-banner-meta", HTML_APP)
        self.assertIn("Copy command", HTML_APP)
        self.assertIn("Copy restart", HTML_APP)
        self.assertIn("restartCommand", HTML_APP)
        self.assertIn("indexCommand", HTML_APP)
        self.assertIn("AUTO_RELOAD_STALE_KEY", HTML_APP)
        self.assertIn("toggleStaleAutoReload", HTML_APP)
        self.assertIn("staleReloadCountdown", HTML_APP)
        self.assertIn('id="uiErrorPanel"', HTML_APP)
        self.assertIn("reportUiError", HTML_APP)
        self.assertIn("unhandledrejection", HTML_APP)
        self.assertIn('id="buildBadge"', HTML_APP)
        self.assertIn("renderBuildBadge", HTML_APP)
        self.assertIn("applyRuntimeConfig", HTML_APP)
        self.assertIn("server_source_stale", HTML_APP)
        self.assertIn("Server source changed after this CodeAtlas server started", HTML_APP)
        self.assertIn("workflow-actions", HTML_APP)
        self.assertIn("Export JSON", HTML_APP)
        self.assertIn("Export text", HTML_APP)
        self.assertIn("Copy JSON", HTML_APP)
        self.assertIn("downloadWorkflowFile", HTML_APP)
        self.assertIn("cacheLabel", HTML_APP)
        self.assertIn("/api/context-pack", HTML_APP)
        self.assertIn("/api/verify-plan", HTML_APP)
        self.assertIn("/api/rules", HTML_APP)
        self.assertIn("/api/source-outline", HTML_APP)
        self.assertIn('id="diagnosticsPanel"', HTML_APP)
        self.assertIn('id="classificationWizard"', HTML_APP)
        self.assertIn("renderClassificationWizard", HTML_APP)
        self.assertIn("saveClassificationFromWizard", HTML_APP)
        self.assertIn("classificationSummaryText", HTML_APP)
        self.assertIn("saveClassificationPackage", HTML_APP)
        self.assertIn("renderDiagnostics", HTML_APP)
        self.assertIn("External deps", HTML_APP)
        self.assertIn("Parser errors", HTML_APP)
        self.assertIn("Stale index", HTML_APP)
        self.assertIn('id="agentContextBtn"', HTML_APP)
        self.assertIn("/api/agent-context", HTML_APP)
        self.assertNotIn('id="zoomInBtn"', HTML_APP)
        self.assertNotIn('id="zoomOutBtn"', HTML_APP)
        self.assertIn('id="baseCommitSelect"', HTML_APP)
        self.assertIn('id="headCommitSelect"', HTML_APP)
        self.assertIn('id="compareMapControls"', HTML_APP)
        self.assertIn("compare-map-controls", HTML_APP)
        self.assertIn("selectedCompareRefs", HTML_APP)
        self.assertIn("compareRefsFromSelectors", HTML_APP)
        self.assertNotIn('id="topCompareControls"', HTML_APP)
        self.assertNotIn('id="baseRefInput"', HTML_APP)
        self.assertNotIn('id="headRefInput"', HTML_APP)
        self.assertIn('id="runCompareBtn"', HTML_APP)
        self.assertNotIn('id="runTopCompareBtn"', HTML_APP)
        self.assertIn('id="diffToggleBtn"', HTML_APP)
        self.assertIn('id="compareChangesOnlyBtn"', HTML_APP)
        self.assertIn('id="compareSyncBtn"', HTML_APP)
        self.assertIn('id="compareExplainBtn"', HTML_APP)
        self.assertIn("compareChangesOnly", HTML_APP)
        self.assertIn("compareSyncViewports", HTML_APP)
        self.assertIn("toggleCompareChangesOnly", HTML_APP)
        self.assertIn("toggleCompareViewportSync", HTML_APP)
        self.assertIn("syncCompareViewport", HTML_APP)
        self.assertIn("compareChangeGate", HTML_APP)
        self.assertIn("compareHasAnyChanges", HTML_APP)
        self.assertIn("compareHidden", HTML_APP)
        self.assertIn("appendCompareNodeDiffSection", HTML_APP)
        self.assertIn("appendCompareEdgeDiffSection", HTML_APP)
        self.assertIn("compare-diff-grid", HTML_APP)
        self.assertIn("Compare Impact", HTML_APP)
        self.assertIn("compareImpactItems", HTML_APP)
        self.assertIn("explainCompareDiff", HTML_APP)
        self.assertIn("compareDiffBriefMarkdown", HTML_APP)
        self.assertIn("drawCompareTimeline", HTML_APP)
        self.assertIn("drawComparePaneHeader", HTML_APP)
        self.assertIn("toggleDiffHighlight", HTML_APP)
        self.assertIn("updateDiffToggle", HTML_APP)
        self.assertIn("shouldHighlightDiff", HTML_APP)
        self.assertIn("compareDiffFocus", HTML_APP)
        self.assertIn("compareDiffNodeAlpha", HTML_APP)
        self.assertIn("hasChange", HTML_APP)
        self.assertIn("bundleAlpha", HTML_APP)
        self.assertIn("isSelectedBundle", HTML_APP)
        self.assertIn('id="askBtn"', HTML_APP)
        self.assertIn("/api/refresh", HTML_APP)
        self.assertIn("/api/compare/warm", HTML_APP)
        self.assertIn("/api/chat", HTML_APP)
        self.assertIn("/api/index-status", HTML_APP)
        self.assertIn("populateCommitSelectors", HTML_APP)
        self.assertIn("commitOptionsFromPayload", HTML_APP)
        self.assertIn("scheduleCompareWarmup", HTML_APP)
        self.assertIn("compareInFlight", HTML_APP)
        self.assertIn("'dateutil'", HTML_APP)
        self.assertIn("'operator'", HTML_APP)
        self.assertIn("setZoom", HTML_APP)
        self.assertIn('id="fitSelectionBtn"', HTML_APP)
        self.assertIn("focusCameraOnSelection", HTML_APP)
        self.assertIn("selectionCameraNodes", HTML_APP)
        self.assertIn("fitCameraToNodes", HTML_APP)
        self.assertIn("animateViewportTo", HTML_APP)
        self.assertIn("graphBaseFitForRect", HTML_APP)
        self.assertIn("CANVAS_ZOOM_MAX = 18", HTML_APP)
        self.assertIn("CANVAS_ZOOM_MIN = 0.12", HTML_APP)
        self.assertIn("handleGraphWheel", HTML_APP)
        self.assertIn("handleGraphPointerDown", HTML_APP)
        self.assertIn("handleGraphPointerMove", HTML_APP)
        self.assertIn("compareViewports", HTML_APP)
        self.assertIn("compareSideForPoint", HTML_APP)
        self.assertIn("graphViewport", HTML_APP)
        self.assertIn("if (side && state.compareViewports[side]) return state.compareViewports[side];", HTML_APP)
        self.assertIn("activePanSide", HTML_APP)
        self.assertIn("resetCompareViewports", HTML_APP)
        self.assertIn("withGraphPanelClip", HTML_APP)
        self.assertIn("drawClippedGraphPanel", HTML_APP)
        self.assertIn("ctx.clip()", HTML_APP)
        self.assertIn("gesturechange", HTML_APP)
        self.assertIn("canvas.panning", HTML_APP)
        self.assertIn("LAYOUT_KEY", HTML_APP)
        self.assertIn("loadLayoutStore", HTML_APP)
        self.assertIn("saveLayoutStore", HTML_APP)
        self.assertIn("layoutStorageKey", HTML_APP)
        self.assertIn("savedLayoutPosition", HTML_APP)
        self.assertIn("deterministicNodePosition", HTML_APP)
        self.assertIn("prepareStableLayout", HTML_APP)
        self.assertIn("prepareCompareStableLayout", HTML_APP)
        self.assertIn("placeNewNodesNearNeighbors", HTML_APP)
        self.assertIn("const nodesById = new Map(nodes.map(node => [node.id, node]));", HTML_APP)
        self.assertNotIn(
            "function placeNewNodesNearNeighbors(nodes, edges) {\n"
            "      const nodesById = nodesByIdForPanel(nodes, side);",
            HTML_APP,
        )
        self.assertIn("rememberLayoutPositions", HTML_APP)
        self.assertIn("layoutFrameNodes", HTML_APP)
        self.assertNotIn("function simulate()", HTML_APP)
        self.assertIn("distanceToSegment", HTML_APP)
        self.assertIn("graphFocus", HTML_APP)
        self.assertIn("nodeFocusAlpha", HTML_APP)
        self.assertIn("selectStat", HTML_APP)
        self.assertIn("renderStatSelection", HTML_APP)
        self.assertIn(".stat > .help-icon", HTML_APP)
        self.assertIn("inventory-row", HTML_APP)
        self.assertIn("Show next", HTML_APP)
        self.assertIn("renderEdgeSelection", HTML_APP)
        self.assertIn("renderNodeConnections", HTML_APP)
        self.assertIn("nodeEdgesForSelection", HTML_APP)
        self.assertIn("appendGroupedEdgeGroupSection", HTML_APP)
        self.assertIn("appendRemainingCounterpartGroups", HTML_APP)
        self.assertIn("renderLazyDetails", HTML_APP)
        self.assertIn("Open to render this list", HTML_APP)
        self.assertIn("more connected components", HTML_APP)
        self.assertIn("detailSectionClass", HTML_APP)
        self.assertIn("section-functions", HTML_APP)
        self.assertIn("section-components", HTML_APP)
        self.assertIn("edge-detail", HTML_APP)
        self.assertIn(".section-functions .edge-detail > summary", HTML_APP)
        self.assertIn(".edge-detail > summary { color: inherit; }", HTML_APP)
        self.assertIn("example-detail", HTML_APP)
        self.assertIn("examples-section", HTML_APP)
        self.assertIn("summary-label", HTML_APP)
        self.assertIn("edgeGroupsByCounterpart", HTML_APP)
        self.assertIn("edgeCounterpartId", HTML_APP)
        self.assertIn("edge-component-group", HTML_APP)
        self.assertIn('id="savedPaths"', HTML_APP)
        self.assertIn("savePath", HTML_APP)
        self.assertIn("selectPath", HTML_APP)
        self.assertIn("renderPathSelection", HTML_APP)
        self.assertIn("symbolLocationsForEndpoint", HTML_APP)
        self.assertIn("isSelectedPathEdge", HTML_APP)
        self.assertIn("Color guide", HTML_APP)
        self.assertIn("Trace path", HTML_APP)
        self.assertIn("USER_ARCH_KEY", HTML_APP)
        self.assertIn("architectureGraphWithOverlay", HTML_APP)
        self.assertIn("openAddConnectionForm", HTML_APP)
        self.assertIn("addUserConnectionFromForm", HTML_APP)
        self.assertIn("saveCurrentArchitecture", HTML_APP)
        self.assertIn("applySavedArchitecture", HTML_APP)
        self.assertIn("customVertex", HTML_APP)
        self.assertIn("api_call", HTML_APP)
        self.assertIn("function_call", HTML_APP)
        self.assertIn("test_covers", HTML_APP)
        self.assertIn("renderDetailLines", HTML_APP)
        self.assertIn("edgeRow", HTML_APP)
        self.assertIn("edge-row", HTML_APP)
        self.assertIn("appendRemainingEdgeDetails", HTML_APP)
        self.assertIn("appendRemainingEdgeRows", HTML_APP)
        self.assertIn("more visible edges", HTML_APP)
        self.assertIn("badgeClass", HTML_APP)
        self.assertIn("appendExampleSection", HTML_APP)
        self.assertIn("appendExampleDetail", HTML_APP)
        self.assertIn("appendRemainingExamples", HTML_APP)
        self.assertIn("remaining-examples", HTML_APP)
        self.assertIn("Expand this to inspect the hidden examples", HTML_APP)
        self.assertIn("Flow / Direction", HTML_APP)
        self.assertIn("appendEdgeFlowSection", HTML_APP)
        self.assertIn("edgeFlowCard", HTML_APP)
        self.assertIn("exampleFlowCard", HTML_APP)
        self.assertIn("flowEndpointCard", HTML_APP)
        self.assertIn("appendParameterChips", HTML_APP)
        self.assertIn("edgeDirectionVerb", HTML_APP)
        self.assertIn("edgeIsDirectional", HTML_APP)
        self.assertIn("compactFlowTitle", HTML_APP)
        self.assertIn("nodeForId", HTML_APP)
        self.assertIn("flow-card", HTML_APP)
        self.assertIn("flow-row", HTML_APP)
        self.assertIn("flow-endpoint", HTML_APP)
        self.assertIn("flow-arrow", HTML_APP)
        self.assertIn("param-chip", HTML_APP)
        self.assertIn("Call expression", HTML_APP)
        self.assertIn("Parameters passed", HTML_APP)
        self.assertIn("Source / starts here", HTML_APP)
        self.assertIn("Target / points here", HTML_APP)
        self.assertIn("segmentedIdentifierHtml", HTML_APP)
        self.assertIn("identifier-segment", HTML_APP)
        self.assertIn("identifier-dot", HTML_APP)
        self.assertIn("detail-card", HTML_APP)
        self.assertIn("detail-call", HTML_APP)
        self.assertIn("detail-type", HTML_APP)
        self.assertIn("detail-component", HTML_APP)
        self.assertIn("detail-weight", HTML_APP)
        self.assertIn("detail-signature", HTML_APP)
        self.assertIn("parameters:", HTML_APP)
        self.assertIn("renderEdgeExample", HTML_APP)
        self.assertIn("target signature", HTML_APP)
        self.assertIn("drawCompare", HTML_APP)

    def test_find_available_port_reports_permission_denied(self) -> None:
        class PermissionDeniedSocket:
            def __enter__(self) -> "PermissionDeniedSocket":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def setsockopt(self, *args: object) -> None:
                return None

            def bind(self, address: tuple[str, int]) -> None:
                raise PermissionError(1, "Operation not permitted")

        with mock.patch("codeatlas.visualization.socket.socket", return_value=PermissionDeniedSocket()):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                find_available_port("127.0.0.1", 8852)


class SourceFileTests(unittest.TestCase):
    def test_source_file_model_can_be_constructed_for_parser_plugins(self) -> None:
        source_file = SourceFile(
            path=Path("example.py"),
            relative_path="example.py",
            language="python",
            size_bytes=10,
            mtime_ns=1,
            sha256="abc",
            line_count=1,
        )

        self.assertEqual(source_file.language, "python")


def run_git(
    root: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=merged_env,
    )


def git_env(name: str, email: str, timestamp: str) -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_AUTHOR_DATE": timestamp,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_COMMITTER_DATE": timestamp,
    }


if __name__ == "__main__":
    unittest.main()
