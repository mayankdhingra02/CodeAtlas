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
        calls_by_display = {call.display_name: call for call in result.calls}
        self.assertEqual(calls_by_display["processor.charge"].arguments, ("total",))

    def test_scanner_ignores_dependency_directories(self) -> None:
        with self.make_repo() as root_name:
            files = {source.relative_path for source in iter_source_files(Path(root_name))}

        self.assertIn("app/orders.py", files)
        self.assertNotIn("node_modules/ignored.py", files)

    def test_scanner_skips_minified_javascript_bundles(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "web").mkdir()
            (root / "web" / "bundle.js").write_text("const value=1;" * 5000, encoding="utf-8")
            files = {source.relative_path for source in iter_source_files(root)}

        self.assertNotIn("web/bundle.js", files)

    def test_project_config_controls_languages_and_ignored_paths(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "web").mkdir()
            (root / "web" / "client.ts").write_text("export const value = 1;\n", encoding="utf-8")
            (root / "generated").mkdir()
            (root / "generated" / "skip.py").write_text("def ignored(): pass\n", encoding="utf-8")
            (root / ".codeatlas.yml").write_text(
                textwrap.dedent(
                    """
                    languages:
                      python: true
                      javascript: false
                    ignore:
                      paths:
                        - generated/**
                    ui:
                      default_lens: apis
                      node_budget: 80
                      connected_only: false
                      edge_contrast: 72
                    classification:
                      owned_prefixes:
                        - app
                      team_prefixes:
                        - company_
                      third_party_packages:
                        - requests
                      hide_packages:
                        - docutils
                      show_packages:
                        - company_sdk
                    cache:
                      ttl_seconds: 120
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            config = load_project_config(root)
            files = {source.relative_path for source in iter_source_files(root)}

        self.assertFalse(config.languages["javascript"])
        self.assertEqual(config.ui.default_lens, "apis")
        self.assertEqual(config.ui.node_budget, 80)
        self.assertFalse(config.ui.connected_only)
        self.assertEqual(config.ui.edge_contrast, 72)
        self.assertEqual(config.classification.owned_prefixes, ("app",))
        self.assertEqual(config.classification.team_prefixes, ("company_",))
        self.assertEqual(config.classification.third_party_packages, ("requests",))
        self.assertEqual(config.classification.hide_packages, ("docutils",))
        self.assertEqual(config.classification.show_packages, ("company_sdk",))
        self.assertEqual(config.public_payload()["ui"]["edge_contrast"], 72)
        self.assertEqual(config.public_payload()["classification"]["third_party_packages"], ["requests"])
        self.assertEqual(config.public_payload()["classification"]["show_packages"], ["company_sdk"])
        self.assertEqual(config.cache.ttl_seconds, 120)
        self.assertNotIn("web/client.ts", files)
        self.assertNotIn("generated/skip.py", files)

    def test_update_classification_config_persists_and_restores_exact_package_bucket(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / ".codeatlas.yml").write_text(
                textwrap.dedent(
                    """
                    classification:
                      show_packages:
                        - requests
                      hide_packages:
                        - docutils
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            updated = update_classification_config(root, "requests", "third_party")
            restored = restore_classification_config(root, {"show_packages": ["requests"], "hide_packages": ["docutils"]})
            reloaded = load_project_config(root)

        self.assertEqual(updated.classification.third_party_packages, ("requests",))
        self.assertEqual(restored.classification.show_packages, ("requests",))
        self.assertEqual(reloaded.classification.third_party_packages, ())
        self.assertEqual(reloaded.classification.show_packages, ("requests",))
        self.assertIn("third_party_packages", reloaded.public_payload()["classification"])

    def test_javascript_parser_extracts_imports_symbols_and_calls(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "web").mkdir()
            (root / "web" / "client.ts").write_text(
                textwrap.dedent(
                    """
                    import { fetchUser as loadUser } from './api';

                    export class UserClient {
                      async load(id: string) {
                        return loadUser(id);
                      }
                    }

                    export const renderUser = (id: string) => loadUser(id);

                    router.get('/users/:id', renderUser);
                    test('renders user profile', () => renderUser('1'));
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            source = next(file for file in iter_source_files(root) if file.relative_path == "web/client.ts")
            result = JavaScriptParser().parse(root, source)

        self.assertEqual(result.module_name, "web.client")
        self.assertIn("./api", {record.module for record in result.imports})
        names = {symbol.qualified_name for symbol in result.symbols}
        self.assertIn("web.client.UserClient", names)
        self.assertIn("web.client.UserClient.load", names)
        self.assertIn("web.client.renderUser", names)
        self.assertIn("web.client.route_get_users_id", names)
        self.assertIn("web.client.test_renders_user_profile", names)
        self.assertIn("loadUser", {call.target_name for call in result.calls})

    def test_javascript_parser_handles_tsx_generics_and_template_braces(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "web").mkdir()
            (root / "web" / "profile.tsx").write_text(
                textwrap.dedent(
                    """
                    import React from 'react';
                    import { formatUser as format } from './format';

                    type User = { id: string; name: string };

                    export class ProfileCard<T extends User> extends React.Component<{ user: T }> {
                      render() {
                        const label = `{${format(this.props.user)}}`;
                        return <button onClick={() => format(this.props.user)}>{label}</button>;
                      }
                    }

                    export const renderProfile = <T extends User>(user: T) => {
                      return <ProfileCard user={user} />;
                    };
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            source = next(file for file in iter_source_files(root) if file.relative_path == "web/profile.tsx")
            result = JavaScriptParser().parse(root, source)

        names = {symbol.qualified_name for symbol in result.symbols}
        self.assertIn("web.profile.ProfileCard", names)
        self.assertIn("web.profile.ProfileCard.render", names)
        self.assertIn("web.profile.renderProfile", names)
        self.assertIn("format", {call.target_name for call in result.calls})
        self.assertIn("format", {record.alias for record in result.imports})
        render_symbol = next(
            symbol for symbol in result.symbols if symbol.qualified_name == "web.profile.ProfileCard.render"
        )
        self.assertGreater(render_symbol.line_end, render_symbol.line_start + 2)

    def test_javascript_parser_does_not_treat_control_flow_as_methods(self) -> None:
        with self.make_repo() as root_name:
            root = Path(root_name)
            (root / "web").mkdir()
            (root / "web" / "client.js").write_text(
                textwrap.dedent(
                    """
                    export class UserClient {
                      load(id) {
                        if (id) {
                          return `{not a block}`;
                        }
                        for (const item of [id]) {
                          console.log(item);
                        }
                        return id;
                      }
                    }
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            source = next(file for file in iter_source_files(root) if file.relative_path == "web/client.js")
            result = JavaScriptParser().parse(root, source)

        names = {symbol.qualified_name for symbol in result.symbols}
        self.assertIn("web.client.UserClient.load", names)
        self.assertNotIn("web.client.UserClient.if", names)
        self.assertNotIn("web.client.UserClient.for", names)
