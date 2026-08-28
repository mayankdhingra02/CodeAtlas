from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from codeatlas_ast.cli import _render
from codeatlas_ast.core import build_index, load_index, retrieve, terms


class AstOnlyTests(unittest.TestCase):
    def make_repo(self) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "app").mkdir()
        (root / "app" / "storage.py").write_text(
            textwrap.dedent(
                '''
                import sqlite3

                class GraphStore:
                    """Persist graph nodes and edges in SQLite."""

                    def insert_edge(self, source, target, edge_type):
                        connection = sqlite3.connect("graph.db")
                        connection.execute(
                            "INSERT INTO edges VALUES (?, ?, ?)",
                            (source, target, edge_type),
                        )
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        (root / "app" / "indexer.py").write_text(
            textwrap.dedent(
                '''
                from app.storage import GraphStore

                class RepositoryIndexer:
                    def write_resolution_edges(self, calls):
                        store = GraphStore()
                        for call in calls:
                            store.insert_edge(call.source, call.target, "CALLS")
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        return temporary

    def test_index_extracts_only_python_ast_data(self) -> None:
        with self.make_repo() as repo:
            report = build_index(repo)
            data = load_index(repo)

        self.assertEqual(report["files_indexed"], 2)
        names = {symbol.qualified_name for symbol in data.symbols}
        self.assertIn("app.storage.GraphStore", names)
        self.assertIn("app.storage.GraphStore.insert_edge", names)
        self.assertIn("app.indexer.RepositoryIndexer.write_resolution_edges", names)
        self.assertTrue(any(call.target == "insert_edge" for call in data.calls))
        self.assertFalse(report["errors"])

    def test_ast_retrieval_returns_relevant_symbol(self) -> None:
        with self.make_repo() as repo:
            build_index(repo)
            result = retrieve(
                repo,
                "Which class stores graph nodes and edges in SQLite?",
                max_tokens=500,
            )

        names = [snippet.qualified_name for snippet in result.snippets]
        self.assertIn("app.storage.GraphStore", names)
        self.assertLessEqual(result.context_tokens, 500)

    def test_dependency_expansion_adds_related_symbol(self) -> None:
        with self.make_repo() as repo:
            build_index(repo)
            result = retrieve(
                repo,
                "Where are resolution edges written?",
                depth=1,
                max_tokens=900,
            )

        names = [snippet.qualified_name for snippet in result.snippets]
        self.assertIn("app.indexer.RepositoryIndexer.write_resolution_edges", names)
        self.assertTrue(any(name.startswith("app.storage.GraphStore") for name in names))

    def test_human_readable_renderer_accepts_retrieval_tuple(self) -> None:
        with self.make_repo() as repo:
            build_index(repo)
            payload = retrieve(
                repo,
                "Which class stores graph nodes and edges in SQLite?",
                max_tokens=500,
            ).to_dict()

        rendered = _render(payload)
        self.assertIn("app.storage.GraphStore", rendered)
        self.assertIn("Retrieval summary", rendered)
        self.assertIn("context_tokens", rendered)

    def test_identifier_tokenization(self) -> None:
        values = terms("writeResolutionEdges graph_nodes persisted")
        self.assertIn("write", values)
        self.assertIn("resolution", values)
        self.assertIn("edge", values)
        self.assertIn("graph", values)
        self.assertIn("node", values)
        self.assertIn("persist", values)


if __name__ == "__main__":
    unittest.main()
