from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from codeatlas.ast_retrieval import AstSymbolRetriever, _terms_match
from codeatlas.evaluation import RetrievalBenchmarkCase, evaluate_retriever
from codeatlas.indexer import RepositoryIndexer


class AstSymbolRetrieverTests(unittest.TestCase):
    def make_indexed_repo(self) -> tempfile.TemporaryDirectory[str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".codeatlas.yml").write_text(
            "languages:\n  python: true\n  javascript: false\n",
            encoding="utf-8",
        )
        (root / "indexer.py").write_text(
            textwrap.dedent(
                '''
                class RepositoryIndexer:
                    def scan_repository(self, root):
                        return list(root.rglob("*.py"))

                    def _write_resolution_edges(self, store, calls, inheritance, references):
                        """Create cross-symbol call, inheritance, and reference graph edges."""
                        for call in calls:
                            store.insert_edge(call.source, call.target, "CALLS")
                        for parent in inheritance:
                            store.insert_edge(parent.child, parent.base, "INHERITS")
                        for reference in references:
                            store.insert_edge(reference.source, reference.target, "REFERENCES")
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        (root / "storage.py").write_text(
            textwrap.dedent(
                '''
                import sqlite3

                class GraphStore:
                    """Persist graph nodes and edges in SQLite."""

                    def insert_edge(self, source, target, edge_type):
                        connection = sqlite3.connect("graph.db")
                        connection.execute(
                            "INSERT INTO edges(source, target, edge_type) VALUES (?, ?, ?)",
                            (source, target, edge_type),
                        )
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        (root / "reporting.py").write_text(
            textwrap.dedent(
                '''
                def build_token_report(baseline_tokens, optimized_tokens):
                    """Calculate baseline and optimized context token counts."""
                    saved = baseline_tokens - optimized_tokens
                    return {"baseline": baseline_tokens, "optimized": optimized_tokens, "saved": saved}
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        RepositoryIndexer().index(root, incremental=False)
        return temp

    def test_retriever_returns_the_specific_ast_symbol_for_natural_language(self) -> None:
        with self.make_indexed_repo() as root_name:
            result = AstSymbolRetriever().retrieve(
                root_name,
                "How are cross-symbol call, inheritance, and reference edges created?",
                max_tokens=700,
            )

        self.assertTrue(result.snippets)
        self.assertEqual(
            result.snippets[0].qualified_name,
            "indexer.RepositoryIndexer._write_resolution_edges",
        )
        self.assertEqual(result.snippets[0].kind, "METHOD")
        self.assertLessEqual(result.token_report.optimized_tokens, 700)

    def test_retriever_uses_ast_boundaries_instead_of_returning_whole_files(self) -> None:
        with self.make_indexed_repo() as root_name:
            result = AstSymbolRetriever().retrieve(
                root_name,
                "Where are graph nodes and edges persisted in SQLite?",
                max_tokens=500,
            )

        names = [snippet.qualified_name for snippet in result.snippets]
        self.assertIn("storage.GraphStore", names)
        graph_store = next(
            snippet for snippet in result.snippets if snippet.qualified_name == "storage.GraphStore"
        )
        self.assertEqual(graph_store.file_path, "storage.py")
        self.assertEqual(graph_store.kind, "CLASS")
        self.assertNotEqual(graph_store.qualified_name, graph_store.file_path)

    def test_ast_retriever_integrates_with_labeled_evaluator(self) -> None:
        case = RetrievalBenchmarkCase(
            case_id="token-report",
            query="Where are baseline and optimized context token counts calculated?",
            expected_files=("reporting.py",),
            expected_symbols=("reporting.build_token_report",),
            max_tokens=500,
        )
        with self.make_indexed_repo() as root_name:
            report = evaluate_retriever(
                root_name,
                (case,),
                retriever=AstSymbolRetriever(),
                strategy="ast-test",
            )

        self.assertEqual(report.summary.mean_file_recall, 1.0)
        self.assertEqual(report.summary.mean_symbol_recall, 1.0)
        self.assertEqual(report.summary.all_targets_rate, 1.0)

    def test_morphological_matching_is_bounded(self) -> None:
        self.assertTrue(_terms_match("index", "indexing"))
        self.assertTrue(_terms_match("calculate", "calculated"))
        self.assertFalse(_terms_match("call", "class"))
        self.assertFalse(_terms_match("edge", "editor"))


if __name__ == "__main__":
    unittest.main()
