from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import textwrap
import unittest
from pathlib import Path
from urllib.request import urlopen

from codeatlas.benchmark import Benchmarker
from codeatlas.indexer import RepositoryIndexer
from codeatlas.memory import MemoryQueryEngine
from codeatlas.mcp_server import create_tool_handlers
from codeatlas.models import SourceFile, estimate_tokens
from codeatlas.parsers.python import PythonParser
from codeatlas.retrieval import RetrievalEngine
from codeatlas.scanner import iter_source_files
from codeatlas.visualization import HTML_APP, VisualizationService, create_visualization_server


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

    def test_scanner_ignores_dependency_directories(self) -> None:
        with self.make_repo() as root_name:
            files = {source.relative_path for source in iter_source_files(Path(root_name))}

        self.assertIn("app/orders.py", files)
        self.assertNotIn("node_modules/ignored.py", files)


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

        self.assertIn("get_impact", handlers)
        self.assertEqual(impact["changed_files"], ("app/auth.py",))
        self.assertTrue(hotspots)
        self.assertEqual(nexus["component"], "auth")


class VisualizationTests(CodeAtlasTestCase):
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
        self.assertIn("commit", commit_types)
        self.assertIn("developer", commit_types)

    def test_visualization_compare_marks_architecture_changes(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            payload = VisualizationService().build_compare(
                root,
                base_ref="HEAD~1",
                head_ref="HEAD",
            )

        head_nodes = {node["id"]: node for node in payload["head"]["graph"]["nodes"]}
        self.assertGreaterEqual(payload["summary"]["added_nodes"], 1)
        self.assertEqual(head_nodes["auth.py"]["change"], "added")
        self.assertEqual(payload["base"]["ref"], "HEAD~1")
        self.assertEqual(payload["head"]["ref"], "HEAD")

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
        self.assertIn('id="showCommonInput"', HTML_APP)
        self.assertIn('id="runCompareBtn"', HTML_APP)
        self.assertIn('id="askBtn"', HTML_APP)
        self.assertIn("/api/chat", HTML_APP)
        self.assertIn("distanceToSegment", HTML_APP)
        self.assertIn("renderEdgeSelection", HTML_APP)
        self.assertIn("drawCompare", HTML_APP)


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
