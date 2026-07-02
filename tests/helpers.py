from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import textwrap
import unittest
from unittest import mock
from pathlib import Path
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
            (
                "# Memory Repo\n\n"
                "Memory Repo is a local service for authentication and payment workflows.\n\n"
                "Authentication and payments are core repository areas.\n"
            ),
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
