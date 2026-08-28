from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codeatlas.evaluation import RetrievalBenchmarkCase, evaluate_retriever
from codeatlas.lexical_baseline import LexicalChunkRetriever, lexical_terms


class LexicalBaselineTests(unittest.TestCase):
    def make_repo(self) -> tempfile.TemporaryDirectory[str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".codeatlas.yml").write_text(
            "languages:\n  python: true\n  javascript: false\n",
            encoding="utf-8",
        )
        (root / "storage.py").write_text(
            "import sqlite3\n\n"
            "class GraphStore:\n"
            "    def persist_edges(self, nodes, edges):\n"
            "        connection = sqlite3.connect('graph.db')\n"
            "        connection.executemany('INSERT INTO edges VALUES (?, ?)', edges)\n"
            "        return len(nodes)\n",
            encoding="utf-8",
        )
        (root / "rendering.py").write_text(
            "def render_dashboard(items):\n"
            "    return '<html>' + ''.join(items) + '</html>'\n",
            encoding="utf-8",
        )
        return temp

    def test_lexical_baseline_ranks_relevant_file_and_respects_budget(self) -> None:
        with self.make_repo() as root_name:
            retriever = LexicalChunkRetriever(chunk_lines=4, stride_lines=2)
            result = retriever.retrieve(
                root_name,
                "Where are graph nodes and edges persisted in SQLite?",
                max_tokens=80,
            )

        self.assertTrue(result.snippets)
        self.assertEqual(result.snippets[0].file_path, "storage.py")
        self.assertLessEqual(result.token_report.optimized_tokens, 80)
        self.assertGreater(
            result.token_report.baseline_tokens,
            result.token_report.optimized_tokens,
        )
        self.assertIn("edge", lexical_terms(result.snippets[0].code))

    def test_lexical_baseline_is_deterministic_and_works_with_evaluator(self) -> None:
        case = RetrievalBenchmarkCase(
            case_id="graph-storage",
            query="persist graph nodes and edges with sqlite",
            expected_files=("storage.py",),
            max_tokens=120,
        )
        with self.make_repo() as root_name:
            retriever = LexicalChunkRetriever(chunk_lines=8, stride_lines=4)
            first = retriever.retrieve(root_name, case.query, max_tokens=case.max_tokens)
            second = retriever.retrieve(root_name, case.query, max_tokens=case.max_tokens)
            report = evaluate_retriever(
                root_name,
                (case,),
                retriever=retriever,
                strategy="lexical-test",
            )

        first_order = [snippet.file_path for snippet in first.snippets]
        second_order = [snippet.file_path for snippet in second.snippets]
        self.assertEqual(first_order, second_order)
        self.assertEqual(report.summary.mean_file_recall, 1.0)
        self.assertEqual(report.summary.all_targets_rate, 1.0)

    def test_lexical_terms_split_identifiers_and_basic_inflections(self) -> None:
        terms = lexical_terms("writeResolutionEdges persists graph_nodes")
        self.assertIn("write", terms)
        self.assertIn("resolution", terms)
        self.assertIn("edge", terms)
        self.assertIn("persist", terms)
        self.assertIn("graph", terms)
        self.assertIn("node", terms)


if __name__ == "__main__":
    unittest.main()
