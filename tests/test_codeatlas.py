from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from codeatlas.benchmark import Benchmarker
from codeatlas.indexer import RepositoryIndexer
from codeatlas.mcp_server import create_tool_handlers
from codeatlas.models import SourceFile, estimate_tokens
from codeatlas.parsers.python import PythonParser
from codeatlas.retrieval import RetrievalEngine
from codeatlas.scanner import iter_source_files


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


if __name__ == "__main__":
    unittest.main()
