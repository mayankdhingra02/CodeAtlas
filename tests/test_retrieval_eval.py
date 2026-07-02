from __future__ import annotations

from pathlib import Path

from codeatlas.retrieval_eval import (
    RetrievalEvalCase,
    RetrievalEvalSuite,
    evaluate_retrieval_manifest,
    evaluate_retrieval_suite,
    load_retrieval_eval_manifest,
)

from tests.helpers import CodeAtlasTestCase


class RetrievalEvalTests(CodeAtlasTestCase):
    def test_retrieval_eval_scores_expected_files_and_symbols(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            suite = RetrievalEvalSuite(
                id="fixture",
                repository_id="fixture",
                k=3,
                min_file_recall=1.0,
                min_symbol_recall=1.0,
                depth=2,
                max_tokens=1000,
                cases=(
                    RetrievalEvalCase(
                        query="create_order",
                        expected_files=("app/orders.py",),
                        expected_symbols=("app.orders.OrderService.create_order",),
                        k=3,
                        depth=2,
                        max_tokens=1000,
                    ),
                ),
            )

            result = evaluate_retrieval_suite(root, suite)

        self.assertTrue(result.passed, result.failure_report())
        self.assertEqual(result.file_recall, 1.0)
        self.assertEqual(result.symbol_recall, 1.0)

    def test_codeatlas_self_retrieval_eval_manifest_meets_recall_thresholds(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = load_retrieval_eval_manifest(repo_root / "evals" / "retrieval" / "default.json")

        run = evaluate_retrieval_manifest(
            manifest,
            repository_ids={"codeatlas-self"},
            repo_overrides={"codeatlas-self": repo_root},
        )

        self.assertTrue(run.passed, run.failure_report())
        self.assertEqual(run.skipped, ())
        self.assertEqual(len(run.results), 1)
        self.assertGreaterEqual(len(run.results[0].case_results), 50)
