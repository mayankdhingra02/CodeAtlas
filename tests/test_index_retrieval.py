from __future__ import annotations

import json
import shutil
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import urlopen

from codeatlas.agent_install import install_agent
from codeatlas.analysis import dead_code, http_confidence_summary, route_summary, structural_query
from codeatlas.artifacts import export_graph_artifact, import_graph_artifact
from codeatlas.benchmark import Benchmarker
from codeatlas.briefing import render_briefing_markdown, repo_briefing
from codeatlas.config import CodeAtlasPaths
from codeatlas.doctor import doctor_report
from codeatlas.external_index import import_external_index
from codeatlas.indexer import RepositoryIndexer
from codeatlas.mcp_server import create_tool_handlers
from codeatlas.memory import MemoryQueryEngine
from codeatlas.models import SourceFile, estimate_tokens, estimate_tokens_for_size
from codeatlas.packs import context_pack, render_context_pack
from codeatlas.parsers.javascript import JavaScriptParser
from codeatlas.parsers.python import PythonParser
from codeatlas.project_config import (
    load_project_config,
    restore_classification_config,
    update_classification_config,
)
from codeatlas.retrieval import RetrievalEngine
from codeatlas.rules import run_rule_checks
from codeatlas.scanner import iter_source_files
from codeatlas.source import source_outline
from codeatlas.status import index_status
from codeatlas.storage import GraphStore, symbol_node_key
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
from tests.helpers import CodeAtlasTestCase, run_git


class IndexAndRetrievalTests(CodeAtlasTestCase):
    def test_indexer_persists_sqlite_index_and_stats(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            report = RepositoryIndexer().index(root)
            stats = RetrievalEngine().repository_stats(root)
            store = GraphStore(CodeAtlasPaths(root).database_path)
            try:
                store.initialize()
                snippet_count = store.connection.execute(
                    "SELECT COUNT(*) AS count FROM snippets"
                ).fetchone()
            finally:
                store.close()
            self.assertTrue(report.database_path.exists())
            self.assertEqual(report.files_scanned, 3)
            self.assertGreaterEqual(report.symbols_indexed, 4)
            self.assertEqual(stats.files_indexed, 3)
            self.assertGreater(stats.graph_edges, 0)
            self.assertGreater(int(snippet_count["count"]), stats.files_indexed)

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

    def test_exact_class_and_method_survive_high_degree_graph_with_tight_budget(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            large_get_body = "x" * 1200
            methods = [
                "    def target_method(self, registry):\n"
                "        return registry.get('target')\n"
            ]
            methods.extend(
                f"    def operation_{index}(self, registry):\n"
                f"        return registry.get({index})\n"
                for index in range(180)
            )
            (root / "app" / "large_graph.py").write_text(
                (
                    "class Registry:\n"
                    "    def get(self, value):\n"
                    f"        payload = {large_get_body!r}\n"
                    "        return value\n\n"
                    "class OversizedService:\n"
                    + "\n".join(methods)
                ),
                encoding="utf-8",
            )
            RepositoryIndexer().index(root)
            engine = RetrievalEngine()
            token_budget = 80

            class_result = engine.retrieve(
                root,
                "OversizedService",
                depth=2,
                max_tokens=token_budget,
            )
            method_result = engine.retrieve(
                root,
                "target_method",
                depth=2,
                max_tokens=token_budget,
            )

        self.assertEqual(
            class_result.snippets[0].qualified_name,
            "app.large_graph.OversizedService",
        )
        self.assertLessEqual(class_result.snippets[0].estimated_tokens, token_budget)
        self.assertEqual(
            method_result.snippets[0].qualified_name,
            "app.large_graph.OversizedService.target_method",
        )
        self.assertLessEqual(method_result.token_report.optimized_tokens, token_budget)

    def test_retrieval_uses_cached_symbol_snippets_when_source_file_moves(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            (root / "app" / "orders.py").rename(root / "app" / "orders.moved")
            result = RetrievalEngine().retrieve(root, "create_order", depth=0, max_tokens=1000)

        self.assertTrue(result.snippets)
        self.assertEqual(result.snippets[0].symbol_name, "create_order")
        self.assertIn("def create_order", result.snippets[0].code)
        self.assertIn("PaymentService()", result.snippets[0].code)

    def test_token_baseline_uses_returned_snippet_files_not_directory_siblings(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "app" / "huge_sibling.py").write_text(
                "FILLER = '" + ("x" * 20_000) + "'\n",
                encoding="utf-8",
            )
            RepositoryIndexer().index(root)
            result = RetrievalEngine().retrieve(root, "PaymentService", depth=0, max_tokens=1000)

        snippet_files = {snippet.file_path for snippet in result.snippets}
        self.assertEqual(snippet_files, {"app/payments.py"})
        self.assertLess(
            result.token_report.baseline_tokens,
            estimate_tokens_for_size(20_000),
        )

    def test_indexer_resolves_import_aliases_and_leaves_ambiguous_calls_unresolved(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "app" / "repo.py").write_text(
                "def save():\n    return 'repo'\n",
                encoding="utf-8",
            )
            (root / "app" / "models.py").write_text(
                "def save():\n    return 'model'\n",
                encoding="utf-8",
            )
            (root / "app" / "consumer.py").write_text(
                textwrap.dedent(
                    """
                    from app.models import save as save_model

                    def run():
                        return save_model()

                    def ambiguous():
                        return save()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            RepositoryIndexer().index(root)
            store = GraphStore(CodeAtlasPaths(root).database_path)
            try:
                store.initialize()
                rows = store.connection.execute(
                    """
                    SELECT source_key, target_key
                    FROM edges
                    WHERE edge_type = 'CALLS'
                    """
                ).fetchall()
            finally:
                store.close()

        edges = {(str(row["source_key"]), str(row["target_key"])) for row in rows}
        self.assertIn(
            (symbol_node_key("app.consumer.run"), symbol_node_key("app.models.save")),
            edges,
        )
        self.assertIn((symbol_node_key("app.consumer.ambiguous"), "symbol_ref:save"), edges)
        dead = dead_code(root)
        self.assertNotIn("app.repo.save", {item["qualified_name"] for item in dead["items"]})

    def test_indexer_aggregates_repeated_edges_with_lines_and_count(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "app" / "helpers.py").write_text(
                "def charge(total):\n    return total\n",
                encoding="utf-8",
            )
            (root / "app" / "orders.py").write_text(
                textwrap.dedent(
                    '''
                    from app.helpers import charge

                    class OrderService:
                        def create_order(self, total):
                            first = charge(total)
                            second = charge(total)
                            return first + second
                    '''
                ).lstrip(),
                encoding="utf-8",
            )
            RepositoryIndexer().index(root)
            store = GraphStore(CodeAtlasPaths(root).database_path)
            try:
                store.initialize()
                rows = store.connection.execute(
                    """
                    SELECT metadata_json
                    FROM edges
                    WHERE source_key = ?
                      AND target_key = ?
                      AND edge_type = 'CALLS'
                    """,
                    (
                        symbol_node_key("app.orders.OrderService.create_order"),
                        symbol_node_key("app.helpers.charge"),
                    ),
                ).fetchall()
            finally:
                store.close()

        self.assertEqual(len(rows), 1)
        metadata = json.loads(str(rows[0]["metadata_json"]))
        self.assertEqual(metadata["count"], 2)
        self.assertEqual(len(metadata["lines"]), 2)
        self.assertEqual(len(metadata["examples"]), 2)
        self.assertEqual(metadata["resolution_tier"], "import_scoped")
        self.assertGreater(metadata["confidence"], 0.8)

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

    def test_stale_schema_reports_clean_error_and_index_rebuilds(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            store = GraphStore(CodeAtlasPaths(root).database_path)
            try:
                store.initialize(validate_schema=False)
                store.set_metadata("schema_version", 0)
                store.commit()
            finally:
                store.close()

            with self.assertRaisesRegex(RuntimeError, "Run `codeatlas index <repo>`"):
                RetrievalEngine().repository_stats(root)

            report = RepositoryIndexer().index(root, incremental=True)
            stats = RetrievalEngine().repository_stats(root)

        self.assertTrue(report.full_rebuild)
        self.assertIn("Index rebuilt because schema changed from 0", report.warnings[0])
        self.assertEqual(report.files_indexed, 3)
        self.assertEqual(stats.files_indexed, 3)

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
            (root / "docs" / "notes.py").unlink()
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
            imported_result = RetrievalEngine().retrieve(
                import_root,
                "create_order",
                depth=0,
                max_tokens=1000,
            )
            status = index_status(root)
            (root / "app" / "orders.py").write_text("# changed\n", encoding="utf-8")
            dirty_status = index_status(root)
            stats_payload = json.loads(CodeAtlasPaths(root).stats_path.read_text(encoding="utf-8"))
            doctor = doctor_report(root)
            self.assertTrue(export_report.artifact_path.exists())
            self.assertTrue(import_report.database_path.exists())
            self.assertTrue(imported_result.snippets)
            self.assertIn("def create_order", imported_result.snippets[0].code)
            self.assertTrue(status["indexed"])
            self.assertIn("index_age_seconds", status)
            self.assertEqual(status["dirty_files_count"], status["dirty_files"])
            self.assertGreaterEqual(dirty_status["dirty_files"], 1)
            self.assertTrue(dirty_status["stale"])
            self.assertIn("parse_quality", stats_payload)
            self.assertIn("unresolved_call_ratio", stats_payload["parse_quality"]["summary"])
            self.assertTrue(any(check["name"] == "schema version" for check in doctor["checks"]))

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

    def test_install_agent_writes_claude_guidance_and_skill(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            payload = install_agent(root, "claude")
            instructions_path = Path(payload["claude_instructions"])
            skill_path = Path(payload["claude_skill"])
            install_agent(root, "claude")

            self.assertTrue(instructions_path.exists())
            self.assertTrue(skill_path.exists())
            instructions = instructions_path.read_text(encoding="utf-8")
            skill = skill_path.read_text(encoding="utf-8")

        self.assertEqual(instructions.count("<!-- CODEATLAS:START -->"), 1)
        self.assertIn("Prefer CodeAtlas over grep", instructions)
        self.assertIn("warm retrieval should normally stay under about 1 second", instructions)
        self.assertIn("name: codeatlas", skill)

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

    def test_context_pack_surfaces_memory_failures_as_warnings(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            with mock.patch(
                "codeatlas.packs.MemoryQueryEngine.compressed_context",
                side_effect=RuntimeError("memory unavailable"),
            ):
                pack = context_pack(root, "create order", max_tokens=1200)
                rendered = render_context_pack(pack, output_format="markdown")

        self.assertIn("Repository memory context unavailable: memory unavailable", pack["warnings"])
        self.assertEqual(pack["memory_unavailable"], {"reason": "memory unavailable"})
        self.assertIn("Warning: Repository memory context unavailable", rendered)

    def test_repo_briefing_builds_first_time_reader_payload(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root, max_commits=10)
            payload = repo_briefing(root)
            markdown = render_briefing_markdown(payload)

        self.assertEqual(payload["repo"]["name"], root.name)
        self.assertIn("identity", payload)
        self.assertIn("authentication", payload["identity"]["purpose"].lower())
        self.assertTrue(payload["identity"]["evidence"])
        self.assertIn("summary", payload)
        self.assertIn("new_engineer_dashboard", payload)
        dashboard_sections = {section["title"] for section in payload["new_engineer_dashboard"]["sections"]}
        self.assertTrue({"Read these first", "Understand these flows", "Avoid this noise", "High-risk areas"} <= dashboard_sections)
        self.assertTrue(payload["start_here"])
        self.assertEqual(payload["start_here"][0]["kind"], "document")
        self.assertTrue(payload["chapters"])
        chapter_ids = {chapter["id"] for chapter in payload["chapters"]}
        self.assertTrue({"api", "services", "scheduler-orchestration", "data-model", "integrations", "tests", "docs-config"} <= chapter_ids)
        chapter_titles = {chapter["title"] for chapter in payload["chapters"]}
        self.assertTrue({"API", "Services", "Scheduler/orchestration", "Data/model", "Integrations", "Tests", "Docs/config"} <= chapter_titles)
        self.assertTrue(payload["flows"])
        flow_ids = {flow["id"] for flow in payload["flows"]}
        self.assertTrue({"request-flow", "startup-config-flow", "data-model-flow", "test-flow", "git-change-flow"} <= flow_ids)
        flow_titles = {flow["title"] for flow in payload["flows"]}
        self.assertIn("Startup/Config Flow", flow_titles)
        self.assertIn("Data/Model Flow", flow_titles)
        self.assertIn("agent_brief", payload)
        self.assertTrue(any(item["evidence"] for item in payload["start_here"]))
        self.assertIn("# CodeAtlas Repo Briefing", markdown)
        self.assertIn("Purpose", markdown)
        self.assertIn("New engineer dashboard", markdown)
        self.assertIn("Start here", markdown)

    def test_repo_briefing_works_without_readme_or_docs(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            payload = repo_briefing(root)
            markdown = render_briefing_markdown(payload)

        self.assertEqual(payload["identity"]["basis"], "code")
        self.assertFalse(payload["identity"]["has_self_description"])
        self.assertEqual(payload["identity"]["source"], "code structure")
        self.assertIn("No README or project description was found", payload["identity"]["purpose"])
        self.assertTrue(payload["identity"]["evidence"])
        self.assertTrue(payload["start_here"])
        self.assertEqual(payload["start_here"][0]["kind"], "component")
        self.assertIn("no README/docs purpose", payload["start_here"][0]["reason"])
        self.assertIn("Purpose evidence: inferred from indexed code structure", payload["summary"]["bullets"][0])
        dashboard_sections = {section["title"] for section in payload["new_engineer_dashboard"]["sections"]}
        self.assertTrue({"Read these first", "Understand these flows", "Avoid this noise", "High-risk areas"} <= dashboard_sections)
        self.assertIn("No README or project description was found", markdown)

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

    def test_import_scip_protobuf_adds_precise_edges_with_scip_tier(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "pkg").mkdir()
            (root / "pkg" / "service.py").write_text(
                textwrap.dedent(
                    '''
                    def source():
                        return target()

                    def target():
                        return 1
                    '''
                ).lstrip(),
                encoding="utf-8",
            )
            source_symbol = "scip-python python fixture 1.0.0 `pkg/service.py`/source()."
            target_symbol = "scip-python python fixture 1.0.0 `pkg/service.py`/target()."
            scip_path = root / "index.scip"
            scip_path.write_bytes(
                make_scip_index(
                    "pkg/service.py",
                    [
                        make_scip_occurrence([0, 4, 0, 10], source_symbol, role=1),
                        make_scip_occurrence([1, 11, 1, 17], target_symbol, syntax_kind=15),
                        make_scip_occurrence([3, 4, 3, 10], target_symbol, role=1),
                    ],
                    [
                        make_scip_symbol(source_symbol, "source"),
                        make_scip_symbol(target_symbol, "target"),
                    ],
                )
            )
            report = import_external_index(root, scip_path, index_format="scip")
            query = structural_query(root, "calls:source")
            store = GraphStore(CodeAtlasPaths(root).database_path)
            try:
                store.initialize()
                row = store.connection.execute(
                    """
                    SELECT metadata_json
                    FROM edges
                    WHERE source_key = ?
                      AND target_key = ?
                      AND edge_type = 'CALLS'
                    """,
                    (symbol_node_key(source_symbol), symbol_node_key(target_symbol)),
                ).fetchone()
            finally:
                store.close()

        self.assertEqual(report["format"], "scip-protobuf")
        self.assertEqual(report["resolution_tier"], "scip")
        self.assertEqual(query["type"], "outgoing")
        self.assertTrue(query["edges"])
        metadata = json.loads(str(row["metadata_json"]))
        self.assertEqual(metadata["resolution_tier"], "scip")
        self.assertGreaterEqual(metadata["confidence"], 0.98)


def make_scip_index(
    relative_path: str,
    occurrences: list[bytes],
    symbols: list[bytes],
) -> bytes:
    document = field_string(1, relative_path)
    for occurrence in occurrences:
        document += field_bytes(2, occurrence)
    for symbol in symbols:
        document += field_bytes(3, symbol)
    return field_bytes(2, document)


def make_scip_occurrence(
    range_values: list[int],
    symbol: str,
    *,
    role: int = 0,
    syntax_kind: int = 0,
) -> bytes:
    payload = field_bytes(1, b"".join(varint(value) for value in range_values))
    payload += field_string(2, symbol)
    if role:
        payload += field_varint(3, role)
    if syntax_kind:
        payload += field_varint(5, syntax_kind)
    return payload


def make_scip_symbol(symbol: str, display_name: str) -> bytes:
    return field_string(1, symbol) + field_string(6, display_name)


def field_string(field_number: int, value: str) -> bytes:
    return field_bytes(field_number, value.encode("utf-8"))


def field_bytes(field_number: int, value: bytes) -> bytes:
    return varint((field_number << 3) | 2) + varint(len(value)) + value


def field_varint(field_number: int, value: int) -> bytes:
    return varint((field_number << 3) | 0) + varint(value)


def varint(value: int) -> bytes:
    chunks = []
    remaining = value
    while True:
        byte = remaining & 0x7F
        remaining >>= 7
        if remaining:
            chunks.append(byte | 0x80)
        else:
            chunks.append(byte)
            return bytes(chunks)
