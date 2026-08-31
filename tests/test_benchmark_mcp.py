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

from typer.testing import CliRunner

from codeatlas.agent_install import install_agent
from codeatlas.analysis import dead_code, http_confidence_summary, route_summary, structural_query
from codeatlas.artifacts import export_graph_artifact, import_graph_artifact
from codeatlas.benchmark import Benchmarker
from codeatlas.briefing import render_briefing_markdown, repo_briefing
from codeatlas.cli import app
from codeatlas.config import CodeAtlasPaths
from codeatlas.external_index import import_external_index
from codeatlas.flow_trace import MAX_FLOW_TRACE_HOPS
from codeatlas.indexer import RepositoryIndexer
from codeatlas.mcp_server import (
    attach_agent_staleness,
    clear_agent_staleness_cache,
    create_tool_handlers,
)
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

        self.assertEqual(
            set(handlers),
            {
                "get_code_context",
                "get_context_pack",
                "query_code_graph",
                "get_index_status",
                "get_verification_plan",
                "run_rules",
                "get_source_outline",
                "get_flow_trace",
                "repository_stats",
            },
        )
        self.assertEqual(context["snippets"][0]["symbol_name"], "create_order")
        self.assertIn("warm_retrieval_ms", context)
        self.assertEqual(context["warm_retrieval_budget_ms"], 1000)
        self.assertIn(context["warm_retrieval_status"], {"ok", "slow"})
        self.assertIn("index_age_seconds", context)
        self.assertEqual(context["dirty_files_count"], 0)
        self.assertEqual(stats["files_indexed"], 3)
        self.assertFalse(stats["index_stale"])

    def test_trace_flow_cli_and_reduced_mcp_return_canonical_payload(self) -> None:
        payload = {
            "schema_version": 1,
            "entrypoint": "POST /orders",
            "trace_kind": "static",
            "ordering_basis": "graph_path",
            "steps": [],
            "links": [],
            "primary_path": [],
            "complete": False,
            "gaps": ["fixture gap"],
            "warnings": [],
        }
        trace = mock.Mock()
        trace.to_dict.return_value = payload
        runner = CliRunner()

        with self.make_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            with (
                mock.patch("codeatlas.cli.trace_flow", return_value=trace) as cli_trace,
                mock.patch("codeatlas.cli.console.print_json") as print_json,
            ):
                result = runner.invoke(
                    app,
                    [
                        "trace-flow",
                        str(root),
                        "--entrypoint",
                        "POST /orders",
                        "--max-hops",
                        "7",
                        "--json",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            cli_trace.assert_called_once_with(root, "POST /orders", max_hops=7)
            self.assertEqual(json.loads(print_json.call_args.args[0]), payload)

            with mock.patch("codeatlas.mcp_server.trace_flow", return_value=trace) as mcp_trace:
                handlers = create_tool_handlers(root)
                result_payload = handlers["get_flow_trace"]("POST /orders", max_hops=7)

        mcp_trace.assert_called_once_with(root, "POST /orders", max_hops=7)
        self.assertEqual(
            {key: result_payload[key] for key in payload},
            payload,
        )
        self.assertIn("index_stale", result_payload)

    def test_trace_flow_cli_enforces_lower_and_upper_hop_bounds(self) -> None:
        runner = CliRunner()
        trace = mock.Mock()
        trace.to_dict.return_value = {
            "entrypoint": "POST /orders",
            "steps": [],
            "links": [],
            "complete": False,
            "gaps": [],
            "warnings": [],
        }

        for max_hops in (0, MAX_FLOW_TRACE_HOPS + 1):
            with self.subTest(max_hops=max_hops):
                with mock.patch("codeatlas.cli.trace_flow") as trace_mock:
                    result = runner.invoke(
                        app,
                        [
                            "trace-flow",
                            ".",
                            "--entrypoint",
                            "POST /orders",
                            "--max-hops",
                            str(max_hops),
                            "--json",
                        ],
                    )
                self.assertEqual(result.exit_code, 2, result.output)
                trace_mock.assert_not_called()

        for max_hops in (1, MAX_FLOW_TRACE_HOPS):
            with self.subTest(max_hops=max_hops):
                with (
                    mock.patch("codeatlas.cli.trace_flow", return_value=trace) as trace_mock,
                    mock.patch("codeatlas.cli.console.print_json"),
                ):
                    result = runner.invoke(
                        app,
                        [
                            "trace-flow",
                            ".",
                            "--entrypoint",
                            "POST /orders",
                            "--max-hops",
                            str(max_hops),
                            "--json",
                        ],
                    )
                self.assertEqual(result.exit_code, 0, result.output)
                trace_mock.assert_called_once_with(
                    Path("."),
                    "POST /orders",
                    max_hops=max_hops,
                )

    def test_reduced_mcp_flow_trace_enforces_lower_and_upper_hop_bounds(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            handler = create_tool_handlers(root)["get_flow_trace"]

            for max_hops in (1, MAX_FLOW_TRACE_HOPS):
                with self.subTest(max_hops=max_hops):
                    payload = handler("POST /orders", max_hops=max_hops)
                    self.assertEqual(payload["entrypoint"], "POST /orders")

            for max_hops in (0, MAX_FLOW_TRACE_HOPS + 1):
                with self.subTest(max_hops=max_hops):
                    with self.assertRaisesRegex(
                        ValueError,
                        rf"between 1 and {MAX_FLOW_TRACE_HOPS}",
                    ):
                        handler("POST /orders", max_hops=max_hops)

    def test_mcp_staleness_payload_is_cached_per_index_mtime(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            clear_agent_staleness_cache()
            with mock.patch(
                "codeatlas.mcp_server.index_status",
                return_value={
                    "index_age_seconds": 3,
                    "dirty_files_count": 0,
                    "stale": False,
                    "checked_at": "now",
                },
            ) as status_mock:
                first = attach_agent_staleness(root, {"ok": True})
                second = attach_agent_staleness(root, {"ok": True})

        self.assertFalse(first["index_stale"])
        self.assertFalse(second["index_stale"])
        self.assertEqual(status_mock.call_count, 1)
