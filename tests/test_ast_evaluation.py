from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codeatlas.evaluation import (
    RetrievalBenchmarkCase,
    evaluate_retriever,
    load_benchmark_cases,
)
from codeatlas.models import ContextSnippet, RetrievalResult, RetrievalTimings, TokenReport


class FakeRetriever:
    def __init__(
        self,
        result: RetrievalResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error

    def retrieve(
        self,
        repo_path: str | Path,
        query: str,
        *,
        depth: int = 2,
        max_tokens: int = 8000,
    ) -> RetrievalResult:
        del repo_path, query, depth, max_tokens
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result


class RetrievalEvaluationTests(unittest.TestCase):
    def test_load_benchmark_cases_normalizes_paths_and_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            dataset = Path(temp_name) / "cases.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "id": "case-1",
                        "query": "find order creation",
                        "expected_files": ["./app\\orders.py"],
                        "expected_symbols": ["app.orders.create_order"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            cases = load_benchmark_cases(dataset)
            self.assertEqual(cases[0].expected_files, ("app/orders.py",))

            dataset.write_text(dataset.read_text(encoding="utf-8") * 2, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate benchmark id"):
                load_benchmark_cases(dataset)

    def test_load_benchmark_cases_rejects_non_string_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            dataset = Path(temp_name) / "cases.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "id": "case-1",
                        "query": "find order creation",
                        "expected_files": [123],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "target to be a string"):
                load_benchmark_cases(dataset)

    def test_evaluator_computes_recall_rank_tokens_and_latency(self) -> None:
        result = RetrievalResult(
            query="find order creation",
            snippets=(
                snippet("app/orders.py", "app.orders.create_order", "create_order"),
                snippet("docs/orders.md", "docs/orders.md", "orders.md", kind="FILE"),
                snippet("app/payments.py", "app.payments.charge", "charge"),
            ),
            token_report=TokenReport(baseline_tokens=1000, optimized_tokens=250),
            timings=RetrievalTimings(
                symbol_lookup_ms=1.0,
                graph_traversal_ms=2.0,
                ranking_ms=3.0,
                total_ms=6.0,
            ),
        )
        case = RetrievalBenchmarkCase(
            case_id="order-flow",
            query="find order creation",
            expected_files=("app/orders.py", "app/payments.py"),
            expected_symbols=("app.orders.create_order", "app.payments.charge"),
        )

        report = evaluate_retriever(
            ".",
            (case,),
            retriever=FakeRetriever(result=result),
            strategy="fake",
        )

        evaluation = report.cases[0]
        self.assertEqual(evaluation.file_recall, 1.0)
        self.assertEqual(evaluation.symbol_recall, 1.0)
        self.assertEqual(evaluation.file_reciprocal_rank, 1.0)
        self.assertEqual(evaluation.symbol_reciprocal_rank, 1.0)
        self.assertTrue(evaluation.all_targets_found)
        self.assertEqual(evaluation.context_tokens, 250)
        self.assertEqual(evaluation.token_savings_percent, 75.0)
        self.assertEqual(evaluation.latency_ms, 6.0)
        self.assertEqual(report.summary.all_targets_rate, 1.0)

    def test_evaluator_records_retriever_errors_instead_of_aborting_dataset(self) -> None:
        case = RetrievalBenchmarkCase(
            case_id="failure",
            query="find missing symbol",
            expected_files=("app/missing.py",),
        )

        report = evaluate_retriever(
            ".",
            (case,),
            retriever=FakeRetriever(error=RuntimeError("index unavailable")),
            strategy="broken",
        )

        self.assertEqual(report.summary.completed_cases, 0)
        self.assertEqual(report.summary.errored_cases, 1)
        self.assertEqual(report.summary.completion_rate, 0.0)
        self.assertEqual(report.summary.all_targets_rate, 0.0)
        self.assertEqual(report.summary.mean_file_recall, 0.0)
        self.assertIn("index unavailable", report.cases[0].error or "")


def snippet(
    file_path: str,
    qualified_name: str,
    symbol_name: str,
    *,
    kind: str = "FUNCTION",
) -> ContextSnippet:
    return ContextSnippet(
        file_path=file_path,
        symbol_name=symbol_name,
        qualified_name=qualified_name,
        kind=kind,
        line_start=1,
        line_end=2,
        score=1.0,
        reason="fixture",
        code="pass",
    )


if __name__ == "__main__":
    unittest.main()
