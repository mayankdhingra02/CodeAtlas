from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codeatlas.evaluation import (
    RetrievalBenchmarkCase,
    SymbolLocation,
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
        locations = {
            "app.orders.create_order": SymbolLocation(
                qualified_name="app.orders.create_order",
                file_path="app/orders.py",
                line_start=1,
                line_end=2,
            ),
            "app.payments.charge": SymbolLocation(
                qualified_name="app.payments.charge",
                file_path="app/payments.py",
                line_start=1,
                line_end=2,
            ),
        }

        report = evaluate_retriever(
            ".",
            (case,),
            retriever=FakeRetriever(result=result),
            strategy="fake",
            symbol_locations=locations,
        )

        evaluation = report.cases[0]
        self.assertEqual(evaluation.file_recall, 1.0)
        self.assertEqual(evaluation.symbol_recall, 1.0)
        self.assertEqual(evaluation.symbol_location_recall, 1.0)
        self.assertEqual(evaluation.symbol_body_coverage, 1.0)
        self.assertEqual(evaluation.file_reciprocal_rank, 1.0)
        self.assertEqual(evaluation.symbol_reciprocal_rank, 1.0)
        self.assertEqual(evaluation.symbol_location_reciprocal_rank, 1.0)
        self.assertTrue(evaluation.all_targets_found)
        self.assertTrue(evaluation.all_target_locations_found)
        self.assertEqual(evaluation.context_tokens, 250)
        self.assertEqual(evaluation.token_savings_percent, 75.0)
        self.assertEqual(evaluation.latency_ms, 6.0)
        self.assertEqual(report.summary.all_targets_rate, 1.0)
        self.assertEqual(report.summary.all_target_locations_rate, 1.0)

    def test_file_chunk_gets_location_credit_without_ast_identity(self) -> None:
        result = RetrievalResult(
            query="find order creation",
            snippets=(
                snippet(
                    "app/orders.py",
                    "app/orders.py",
                    "orders.py:8-20",
                    kind="FILE_CHUNK",
                    line_start=8,
                    line_end=20,
                    code="\n".join(f"line {number}" for number in range(8, 21)),
                ),
            ),
            token_report=TokenReport(baseline_tokens=1000, optimized_tokens=100),
            timings=RetrievalTimings(
                symbol_lookup_ms=0.0,
                graph_traversal_ms=0.0,
                ranking_ms=4.0,
                total_ms=4.0,
            ),
        )
        case = RetrievalBenchmarkCase(
            case_id="file-chunk",
            query="find order creation",
            expected_files=("app/orders.py",),
            expected_symbols=("app.orders.create_order",),
        )
        locations = {
            "app.orders.create_order": SymbolLocation(
                qualified_name="app.orders.create_order",
                file_path="app/orders.py",
                line_start=10,
                line_end=15,
            )
        }

        report = evaluate_retriever(
            ".",
            (case,),
            retriever=FakeRetriever(result=result),
            strategy="file-chunk",
            symbol_locations=locations,
        )

        evaluation = report.cases[0]
        self.assertEqual(evaluation.symbol_recall, 0.0)
        self.assertEqual(evaluation.symbol_location_recall, 1.0)
        self.assertEqual(evaluation.symbol_body_coverage, 1.0)
        self.assertEqual(evaluation.symbol_location_reciprocal_rank, 1.0)
        self.assertFalse(evaluation.all_targets_found)
        self.assertTrue(evaluation.all_target_locations_found)
        self.assertEqual(report.summary.mean_symbol_recall, 0.0)
        self.assertEqual(report.summary.mean_symbol_location_recall, 1.0)
        self.assertEqual(report.summary.all_target_locations_rate, 1.0)

    def test_evaluator_reports_unresolved_gold_symbols(self) -> None:
        result = RetrievalResult(
            query="find missing symbol",
            snippets=(),
            token_report=TokenReport(baseline_tokens=100, optimized_tokens=0),
            timings=RetrievalTimings(
                symbol_lookup_ms=0.0,
                graph_traversal_ms=0.0,
                ranking_ms=0.0,
                total_ms=0.0,
            ),
        )
        case = RetrievalBenchmarkCase(
            case_id="missing-label",
            query="find missing symbol",
            expected_symbols=("app.missing.symbol",),
        )

        report = evaluate_retriever(
            ".",
            (case,),
            retriever=FakeRetriever(result=result),
            strategy="missing-label",
            symbol_locations={},
        )

        evaluation = report.cases[0]
        self.assertEqual(
            evaluation.unresolved_expected_symbols,
            ("app.missing.symbol",),
        )
        self.assertEqual(evaluation.symbol_location_recall, 0.0)
        self.assertEqual(evaluation.symbol_body_coverage, 0.0)
        self.assertFalse(evaluation.all_target_locations_found)

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
            symbol_locations={},
        )

        self.assertEqual(report.summary.completed_cases, 0)
        self.assertEqual(report.summary.errored_cases, 1)
        self.assertEqual(report.summary.completion_rate, 0.0)
        self.assertEqual(report.summary.all_targets_rate, 0.0)
        self.assertEqual(report.summary.all_target_locations_rate, 0.0)
        self.assertEqual(report.summary.mean_file_recall, 0.0)
        self.assertIn("index unavailable", report.cases[0].error or "")


def snippet(
    file_path: str,
    qualified_name: str,
    symbol_name: str,
    *,
    kind: str = "FUNCTION",
    line_start: int = 1,
    line_end: int = 2,
    code: str = "pass",
) -> ContextSnippet:
    return ContextSnippet(
        file_path=file_path,
        symbol_name=symbol_name,
        qualified_name=qualified_name,
        kind=kind,
        line_start=line_start,
        line_end=line_end,
        score=1.0,
        reason="fixture",
        code=code,
    )


if __name__ == "__main__":
    unittest.main()
