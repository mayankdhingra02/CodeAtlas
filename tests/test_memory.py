from __future__ import annotations

import json
import shutil
import tempfile
import threading
import textwrap
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
from codeatlas.external_index import import_external_index
from codeatlas.indexer import RepositoryIndexer
from codeatlas.memory import MemoryQueryEngine
from codeatlas.mcp_server import create_tool_handlers
from codeatlas.models import SourceFile, estimate_tokens, estimate_tokens_for_size
from codeatlas.packs import context_pack, render_context_pack
from codeatlas.parsers.javascript import JavaScriptParser
from codeatlas.parsers.python import PythonParser
from codeatlas.project_config import load_project_config, restore_classification_config, update_classification_config
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
            handlers = create_tool_handlers(root, profile="full")
            history = handlers["get_history"]("auth")
            decisions = handlers["get_decisions"]("Redis")
            context = handlers["get_context"]("auth", max_tokens=1000)

        self.assertIn("get_ownership", handlers)
        self.assertTrue(history["items"])
        self.assertIn("index_age_seconds", history)
        self.assertTrue(decisions["items"][0]["evidence"])
        self.assertEqual(context["query"], "auth")
        self.assertEqual(context["dirty_files_count"], 0)

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
            handlers = create_tool_handlers(root, profile="full")
            impact = handlers["get_impact"]("HEAD")
            hotspots = handlers["get_hotspots"](limit=3)
            nexus = handlers["get_nexus"]("auth")
            status = handlers["get_index_status"]()
            query = handlers["query_code_graph"]("calls:login")
            rules = handlers["run_rules"](limit=3)
            outline = handlers["get_source_outline"]("login")
            plan = handlers["get_verification_plan"]("HEAD")

        self.assertIn("get_impact", handlers)
        self.assertIn("get_index_status", handlers)
        self.assertIn("query_code_graph", handlers)
        self.assertIn("find_dead_code", handlers)
        self.assertIn("get_routes", handlers)
        self.assertIn("get_context_pack", handlers)
        self.assertIn("get_verification_plan", handlers)
        self.assertIn("run_rules", handlers)
        self.assertIn("get_source_outline", handlers)
        self.assertIn("import_code_index", handlers)
        self.assertEqual(impact["changed_files"], ("app/auth.py",))
        self.assertTrue(hotspots["items"])
        self.assertEqual(nexus["component"], "auth")
        self.assertTrue(status["indexed"])
        self.assertGreaterEqual(status["dirty_files_count"], 1)
        self.assertEqual(query["type"], "outgoing")
        self.assertIn("findings", rules)
        self.assertTrue(outline["files"])
        self.assertIn("app/auth.py", plan["changed_files"])
