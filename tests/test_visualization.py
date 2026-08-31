from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

from codeatlas.agent_install import install_agent
from codeatlas.analysis import dead_code, http_confidence_summary, route_summary, structural_query
from codeatlas.artifacts import export_graph_artifact, import_graph_artifact
from codeatlas.benchmark import Benchmarker
from codeatlas.briefing import render_briefing_markdown, repo_briefing
from codeatlas.config import CodeAtlasPaths
from codeatlas.external_index import import_external_index
from codeatlas.indexer import RepositoryIndexer
from codeatlas.mcp_server import create_tool_handlers
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


class VisualizationTests(CodeAtlasTestCase):
    def test_visualization_assets_are_split_and_rendered(self) -> None:
        self.assertTrue((ASSET_DIR / "visualization.html").exists())
        self.assertTrue((ASSET_DIR / "visualization.css").exists())
        self.assertTrue((ASSET_DIR / "visualization.js").exists())
        html = (ASSET_DIR / "visualization.html").read_text(encoding="utf-8")
        css = (ASSET_DIR / "visualization.css").read_text(encoding="utf-8")
        js = (ASSET_DIR / "visualization.js").read_text(encoding="utf-8")
        self.assertIn("{{ CODEATLAS_CSS }}", html)
        self.assertIn("{{ CODEATLAS_JS }}", html)
        self.assertIn(".command-palette-shell", css)
        self.assertIn("function renderStaleBanner", js)
        self.assertIn("legend-dot legend-owned", html)
        self.assertIn('id="legendToggleBtn"', html)
        self.assertIn('id="emptyMapOverlay"', html)
        self.assertIn('id="focusBreadcrumb"', html)
        self.assertIn('id="edgeHoverTooltip"', html)
        self.assertIn('id="detailTabs"', html)
        self.assertIn('id="briefingViewBtn"', html)
        self.assertNotIn('id="fileFlowBtn"', html)
        self.assertNotIn('id="commitsBtn"', html)
        self.assertNotIn('id="compareViewBtn"', html)
        self.assertIn('id="fileFlowLayerControls"', html)
        self.assertIn('id="briefingOverlay"', html)
        self.assertIn('id="flowPlaybackHud"', html)
        self.assertIn('id="flowPlaybackPlayBtn"', html)
        self.assertIn('id="flowPlaybackScrubber"', html)
        self.assertIn('id="globalLoadingBadge"', html)
        self.assertIn('id="filterLoadingBadge"', html)
        self.assertIn('id="loadingOverlay"', html)
        self.assertIn('data-detail-tab="evidence"', html)
        self.assertIn(".legend-dot", css)
        self.assertIn(".loading-chip", css)
        self.assertIn(".loading-overlay", css)
        self.assertIn(".inline-loading", css)
        self.assertIn("button.is-loading", css)
        self.assertIn("@keyframes loading-spin", css)
        self.assertIn(".flow-playback-hud", css)
        self.assertIn(".flow-playback-selection-controls", css)
        self.assertIn(".file-flow-layer-controls", css)
        self.assertIn(".file-flow-layer-control", css)
        self.assertIn(".file-flow-actions", css)
        self.assertIn(".file-flow-example-preview", css)
        self.assertIn(".trace-step.active", css)
        self.assertIn(".briefing-overlay", css)
        self.assertIn(".briefing-dashboard", css)
        self.assertIn(".new-engineer-dashboard", css)
        self.assertIn(".new-engineer-panel", css)
        self.assertIn(".briefing-journey-intro", css)
        self.assertIn(".briefing-evidence-block", css)
        self.assertIn(".briefing-file-action", css)
        self.assertIn(".legend.auto-compact", css)
        self.assertIn(".empty-map-overlay", css)
        self.assertIn(".focus-breadcrumb", css)
        self.assertIn(".edge-hover-tooltip", css)
        self.assertIn(".detail-tabs", css)
        self.assertIn(".detail-card.tab-filtered-out", css)
        self.assertIn("scrollbar-width: thin", css)
        self.assertIn("::-webkit-scrollbar-thumb", css)
        self.assertIn("scrollbar-gutter: stable", css)
        self.assertIn("function applyDetailTabFilter", js)
        self.assertIn("function loadBriefing", js)
        self.assertIn("function setFileFlowGraph", js)
        self.assertIn("function renderFileFlowLayerControls", js)
        self.assertIn("function setFileFlowLayerSelection", js)
        self.assertIn("function fileFlowNodeId", js)
        self.assertIn("function currentGraphStats", js)
        self.assertIn("renderStats(currentGraphStats())", js)
        self.assertIn("function drawFileFlowPanel", js)
        self.assertIn("function buildFileFlowLayers", js)
        self.assertIn("function appendFileFlowEdgeControls", js)
        self.assertIn("function renderBriefing", js)
        self.assertIn("function focusBriefingComponent", js)
        self.assertIn("function setLoadingTask", js)
        self.assertIn("function renderLoadingIndicators", js)
        self.assertIn("function setInlineStatusLoading", js)
        self.assertIn("function setButtonLoading", js)
        self.assertIn("function startFlowPlayback", js)
        self.assertIn("function requestCanonicalFlowTrace", js)
        self.assertIn("function buildCanonicalFlowPlayback", js)
        self.assertIn("function canonicalTracePrimaryPath", js)
        self.assertIn("function flowPlaybackDirectedEdge", js)
        self.assertIn("function renderFlowPlaybackHud", js)
        self.assertIn("function drawFlowPlaybackPulse", js)
        self.assertIn("function drawFlowPlaybackVirtualEdge", js)
        self.assertIn("function flowPlaybackFallbackSteps", js)
        self.assertIn("function restoreUrlFlowPlayback", js)
        self.assertIn("edgeDashPattern", js)
        self.assertNotIn("Blue: owned", HTML_APP)
        self.assertNotIn("Violet: third-party", HTML_APP)
        self.assertEqual(HTML_APP, render_visualization_app())
        self.assertNotIn("{{ CODEATLAS_CSS }}", HTML_APP)
        self.assertNotIn("{{ CODEATLAS_JS }}", HTML_APP)

    def test_canonical_flow_ui_helpers_require_exact_directed_links(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        js = (ASSET_DIR / "visualization.js").read_text(encoding="utf-8")
        entry_start = js.index("    function exactRouteEntrypoint")
        entry_end = js.index("    function requestCanonicalFlowTrace", entry_start)
        path_start = js.index("    function canonicalTracePrimaryPath")
        path_end = js.index("    function buildCanonicalFlowPlayback", path_start)
        node_start = js.index("    function canonicalFlowPlaybackNodeId")
        node_end = js.index("    function flowPlaybackDirectedEdge", node_start)
        edge_start = js.index("    function flowPlaybackDirectedEdge")
        edge_end = js.index("    function canonicalFlowPlaybackDetail", edge_start)
        helpers = "\n".join(
            (
                js[entry_start:entry_end],
                js[path_start:path_end],
                js[node_start:node_end],
                js[edge_start:edge_end],
            )
        )
        script = textwrap.dedent(
            f"""
            {helpers}
            const state = {{
              nodeIndex: new Map([['api', {{}}], ['service', {{}}], ['payments', {{}}]]),
              allEdges: [
                {{ source: 'api', target: 'service', type: 'calls' }},
                {{
                  source: 'service',
                  target: 'payments',
                  type: 'http_calls',
                  examples: [{{
                    source: {{ key: 'symbol:charge_payment' }},
                    target: {{ key: 'route:external:POST:https://payments.example/charge' }}
                  }}]
                }}
              ]
            }};
            function briefingComponentFromPath(path) {{ return String(path || '').split('/')[0]; }}
            function allKnownNodes() {{
              return [{{ id: 'process_payment', label: 'process_payment' }}];
            }}
            const baseTrace = {{
              trace_kind: 'static',
              steps: [
                {{ id: 'route', label: 'POST /orders' }},
                {{ id: 'handler', label: 'post_order' }}
              ],
              links: [{{
                source_step_id: 'route',
                target_step_id: 'handler',
                edge_type: 'HANDLES'
              }}],
              primary_path: ['route', 'handler']
            }};
            const validPath = canonicalTracePrimaryPath(baseTrace);
            const reversePath = canonicalTracePrimaryPath({{
              ...baseTrace,
              primary_path: ['handler', 'route']
            }});
            const importPath = canonicalTracePrimaryPath({{
              ...baseTrace,
              links: [{{
                source_step_id: 'route',
                target_step_id: 'handler',
                edge_type: 'IMPORTS'
              }}]
            }});
            console.log(JSON.stringify({{
              entrypoint: exactRouteEntrypoint('post /orders'),
              nonRoute: exactRouteEntrypoint('orders route'),
              evidenceEntrypoint: briefingFlowEntrypoint({{
                steps: [{{ title: 'Request entrypoint', evidence: [{{ title: 'GET /health' }}] }}]
              }}),
              validIds: validPath && validPath.ids,
              reversePath,
              importPath,
              mappedSink: canonicalFlowPlaybackNodeId({{
                node_key: 'route:external:POST:https://payments.example/charge',
                label: 'POST https://payments.example/charge',
                role: 'external_http'
              }}),
              unknownSink: canonicalFlowPlaybackNodeId({{
                node_key: 'route:external:POST:https://unknown.example/charge',
                label: 'POST https://unknown.example/charge',
                role: 'external_http'
              }}),
              unresolvedProjection: canonicalFlowPlaybackNodeId({{
                node_key: 'route:external:POST:https://payments.example/charge',
                label: 'process_payment',
                role: 'unresolved',
                status: 'unresolved'
              }}),
              directed: Boolean(flowPlaybackDirectedEdge('api', 'service', 'CALLS')),
              reversed: Boolean(flowPlaybackDirectedEdge('service', 'api', 'CALLS')),
              wrongType: Boolean(flowPlaybackDirectedEdge('api', 'service', 'HANDLES'))
            }}));
            """
        )
        result = subprocess.run(
            [node, "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout.strip())
        self.assertEqual(payload["entrypoint"], "POST /orders")
        self.assertEqual(payload["nonRoute"], "")
        self.assertEqual(payload["evidenceEntrypoint"], "GET /health")
        self.assertEqual(payload["validIds"], ["route", "handler"])
        self.assertIsNone(payload["reversePath"])
        self.assertIsNone(payload["importPath"])
        self.assertEqual(payload["mappedSink"], "payments")
        self.assertEqual(payload["unknownSink"], "")
        self.assertEqual(payload["unresolvedProjection"], "")
        self.assertTrue(payload["directed"])
        self.assertFalse(payload["reversed"])
        self.assertFalse(payload["wrongType"])

    def test_graph_worker_filter_matches_expected_small_fixture(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        start = HTML_APP.index("function graphWorkerSource()")
        ret = HTML_APP.index("return `", start)
        end = HTML_APP.index("`;", ret + len("return `"))
        worker_source = HTML_APP[ret + len("return `") : end]
        script = textwrap.dedent(
            f"""
            const {{ Worker }} = require('worker_threads');
            const source = {json.dumps(worker_source)};
            const shimmed = `
              const {{ parentPort }} = require('worker_threads');
              globalThis.self = {{
                onmessage: null,
                postMessage: message => parentPort.postMessage(message)
              }};
              parentPort.on('message', data => globalThis.self.onmessage({{ data }}));
              ${{source}}
            `;
            const worker = new Worker(shimmed, {{ eval: true }});
            const timer = setTimeout(() => {{
              console.error('worker timed out');
              process.exit(1);
            }}, 2000);
            const payload = {{
              requestId: 7,
              nodes: [
                {{ id: 'a', label: 'a', category: 'owned', type: 'component', size: 20, metrics: {{ files: 2 }} }},
                {{ id: 'b', label: 'b', category: 'owned', type: 'component', size: 12, metrics: {{ files: 1 }} }},
                {{ id: 'c', label: 'c', category: 'third_party', type: 'external', size: 10, metrics: {{}} }},
                {{ id: 'd', label: 'd', category: 'team', type: 'component', size: 10, metrics: {{}} }}
              ],
              edges: [
                {{ id: 'e1', source: 'a', target: 'b', type: 'imports', weight: 3, categories: ['component'] }},
                {{ id: 'e2', source: 'b', target: 'c', type: 'imports', weight: 4, categories: ['component'] }},
                {{ id: 'e3', source: 'a', target: 'd', type: 'api_call', weight: 1, categories: ['api'] }}
              ],
              categoryVisibility: {{ owned: true, team: true, third_party: false }},
              connectionVisibility: {{ component: true, api: true, functions: true, projects: true }},
              hidden: [],
              connectedOnly: true,
              minEdgeWeight: 1,
              focusSelection: false,
              focusSeeds: [],
              traceMode: null,
              focusHops: 1,
              nodeBudget: 2
            }};
            worker.on('message', message => {{
              clearTimeout(timer);
              console.log(JSON.stringify(message));
              worker.terminate();
            }});
            worker.on('error', error => {{
              console.error(error.stack || error.message);
              process.exit(1);
            }});
            worker.postMessage(payload);
            """
        )
        result = subprocess.run(
            [node, "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout.strip())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["requestId"], 7)
        self.assertEqual(payload["nodeIds"], ["a", "b"])
        self.assertEqual(payload["edgeIds"], ["e1"])
        self.assertEqual(payload["categoryHidden"], 1)
        self.assertEqual(payload["counts"]["budgetHidden"], 1)

    def test_visualization_map_contains_architecture_and_commit_graphs(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            (root / "app" / "helpers.py").write_text(
                "def charge(total):\n    return total\n",
                encoding="utf-8",
            )
            (root / "app" / "orders.py").write_text(
                textwrap.dedent(
                    '''
                    from app.helpers import charge

                    class OrderService:
                        def create_order(self, total):
                            return charge(total)
                    '''
                ).lstrip(),
                encoding="utf-8",
            )
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root)
            payload = VisualizationService().build_map(root)

        component_ids = {node["id"] for node in payload["component_graph"]["nodes"]}
        commit_types = {node["type"] for node in payload["commit_graph"]["nodes"]}
        self.assertIn("auth.py", component_ids)
        self.assertIn("docs", component_ids)
        self.assertTrue(payload["component_graph"]["edges"])
        example_edges = [
            edge for edge in payload["component_graph"]["edges"] if edge.get("examples")
        ]
        call_examples = [
            example
            for edge in example_edges
            for example in edge["examples"]
            if example["type"] == "calls"
        ]
        self.assertTrue(example_edges)
        self.assertTrue(call_examples)
        self.assertTrue(any(example["arguments"] == ["total"] for example in call_examples))
        self.assertTrue(any(example["target"].get("signature") for example in call_examples))
        self.assertIn("commit", commit_types)
        self.assertIn("developer", commit_types)
        self.assertTrue(payload["file_graph"]["nodes"])
        self.assertTrue(payload["file_graph"]["edges"])
        file_edge = next(edge for edge in payload["file_graph"]["edges"] if edge["source"] == "app/orders.py" and edge["target"] == "app/helpers.py" and edge["type"] == "calls")
        self.assertEqual(file_edge["type"], "calls")
        self.assertTrue(file_edge["examples"])
        self.assertTrue(any(example["source_file"] == "app/orders.py" and example["target_file"] == "app/helpers.py" for example in file_edge["examples"]))
        self.assertEqual(payload["stats"]["files"], len(payload["inventory"]["files"]))
        self.assertEqual(payload["stats"]["symbols"], len(payload["inventory"]["symbols"]))
        self.assertEqual(payload["stats"]["commits"], len(payload["inventory"]["commits"]))
        self.assertTrue(any(item["path"] == "app/auth.py" for item in payload["inventory"]["files"]))
        self.assertTrue(any(item["qualified_name"].endswith("AuthService.login") for item in payload["inventory"]["symbols"]))
        self.assertIn("diagnostics", payload)
        self.assertIn("python", payload["diagnostics"]["supported_languages"])
        self.assertIn("language_counts", payload["diagnostics"])
        self.assertIn("files_skipped", payload["diagnostics"])
        self.assertIn("parser_errors", payload["diagnostics"])
        self.assertIn("external_dependencies", payload["diagnostics"])
        self.assertIn("stale", payload["diagnostics"])

    def test_visualization_compare_marks_architecture_changes(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            service = VisualizationService()
            payload = service.build_compare(
                root,
                base_ref="HEAD~1",
                head_ref="HEAD",
            )
            cached_payload = service.build_compare(
                root,
                base_ref="HEAD~1",
                head_ref="HEAD",
            )

        head_nodes = {node["id"]: node for node in payload["head"]["graph"]["nodes"]}
        self.assertGreaterEqual(payload["summary"]["added_nodes"], 1)
        self.assertEqual(head_nodes["auth.py"]["change"], "added")
        self.assertEqual(payload["base"]["ref"], "HEAD~1")
        self.assertEqual(payload["head"]["ref"], "HEAD")
        self.assertEqual(payload["summary"]["cache"]["base"], "miss")
        self.assertEqual(payload["summary"]["cache"]["head"], "miss")
        self.assertEqual(cached_payload["summary"]["cache"]["base"], "hit")
        self.assertEqual(cached_payload["summary"]["cache"]["head"], "hit")

    def test_visualization_chat_answers_from_code_and_memory(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root)
            payload = VisualizationService().ask(root, "AuthService Redis auth")

        self.assertIn("Question: AuthService Redis auth", payload["answer"])
        self.assertTrue(payload["code"])
        self.assertTrue(payload["evidence"])
        self.assertTrue(any("AuthService" in item["symbol"] for item in payload["code"]))

    def test_visualization_server_serves_graph_json(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root)
            try:
                server = create_visualization_server(root, host="127.0.0.1", port=0)
            except PermissionError as exc:
                raise self.skipTest("local socket binding is blocked in this sandbox") from exc
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                with urlopen(f"http://127.0.0.1:{port}/api/graph", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{port}/api/briefing", timeout=5) as response:
                    briefing_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(payload["repo"]["name"], root.name)
        self.assertGreaterEqual(payload["stats"]["files"], 1)
        self.assertTrue(payload["component_graph"]["nodes"])
        self.assertTrue(briefing_payload["ok"])
        self.assertIn("start_here", briefing_payload)
        self.assertIn("agent_brief", briefing_payload)

    def test_visualization_server_serves_canonical_flow_trace(self) -> None:
        expected = {
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
        trace.to_dict.return_value = expected
        with self.make_repo() as root_name:
            root = Path(root_name)
            try:
                server = create_visualization_server(root, host="127.0.0.1", port=0)
            except PermissionError as exc:
                raise self.skipTest("local socket binding is blocked in this sandbox") from exc
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/flow-trace",
                data=json.dumps({"entrypoint": "POST /orders", "max_hops": 7}).encode(
                    "utf-8"
                ),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with mock.patch(
                    "codeatlas.visualization.trace_flow",
                    return_value=trace,
                ) as trace_mock:
                    with urlopen(request, timeout=5) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        trace_mock.assert_called_once_with(root, "POST /orders", max_hops=7)
        self.assertTrue(payload.pop("ok"))
        self.assertEqual(payload, expected)

    def test_mcp_visual_map_handler_is_available(self) -> None:
        with self.make_memory_repo() as root_name:
            root = Path(root_name)
            RepositoryIndexer().index(root)
            MemoryQueryEngine().index_memory(root)
            handlers = create_tool_handlers(root, profile="full")
            payload = handlers["get_visual_map"]()

        self.assertIn("get_visual_map", handlers)
        self.assertTrue(payload["component_graph"]["nodes"])
        self.assertTrue(payload["commit_graph"]["nodes"])

    def test_visualization_page_exposes_filters_and_edge_selection(self) -> None:
        self.assertIn('id="componentFilters"', HTML_APP)
        self.assertIn('id="categoryFilters"', HTML_APP)
        self.assertIn('id="connectionFilters"', HTML_APP)
        self.assertIn('id="showAllConnectionsBtn"', HTML_APP)
        self.assertIn('id="onlyApiConnectionsBtn"', HTML_APP)
        self.assertIn('id="mapLensSelect"', HTML_APP)
        self.assertIn('value="subway"', HTML_APP)
        self.assertIn('id="applyLensBtn"', HTML_APP)
        self.assertIn('id="smartSimplifyBtn"', HTML_APP)
        self.assertIn('id="minWeightInput"', HTML_APP)
        self.assertIn('id="nodeBudgetSelect"', HTML_APP)
        self.assertIn('id="focusSelectionInput"', HTML_APP)
        self.assertIn('id="focusHopsSelect"', HTML_APP)
        self.assertIn('id="mapStatusPanel"', HTML_APP)
        self.assertIn('id="emptyMapOverlay"', HTML_APP)
        self.assertIn('id="emptyMapResetBtn"', HTML_APP)
        self.assertIn("currentEmptyMapState", HTML_APP)
        self.assertIn("updateEmptyMapOverlay", HTML_APP)
        self.assertIn("resetMapView", HTML_APP)
        self.assertIn("showEveryNode", HTML_APP)
        self.assertIn('id="resetViewBtn"', HTML_APP)
        self.assertIn('id="showEveryNodeBtn"', HTML_APP)
        self.assertIn('id="legendToggleBtn"', HTML_APP)
        self.assertIn("toggleLegendCollapsed", HTML_APP)
        self.assertIn("legendNeedsCompaction", HTML_APP)
        self.assertIn("rectsOverlap", HTML_APP)
        self.assertIn("CATEGORY_FILTERS", HTML_APP)
        self.assertIn("CONNECTION_FILTERS", HTML_APP)
        self.assertIn("LENS_LABELS", HTML_APP)
        self.assertIn("categoryVisibility", HTML_APP)
        self.assertIn("{ id: 'owned'", HTML_APP)
        self.assertIn("{ id: 'team'", HTML_APP)
        self.assertIn("{ id: 'third_party'", HTML_APP)
        self.assertIn("{ id: 'docs_config'", HTML_APP)
        self.assertIn("third_party: false", HTML_APP)
        self.assertIn("docs_config: false", HTML_APP)
        self.assertIn("COMMON_NODE_PATTERNS", HTML_APP)
        self.assertIn("DEFAULT_TEAM_PREFIXES", HTML_APP)
        self.assertIn("applyCategoryPreset", HTML_APP)
        self.assertIn("docutils", HTML_APP)
        self.assertIn("numpy", HTML_APP)
        self.assertIn("pandas", HTML_APP)
        self.assertIn("api[-_]?ref", HTML_APP)
        self.assertIn("requirements?", HTML_APP)
        self.assertIn("team dependencies", HTML_APP)
        self.assertIn("third-party packages", HTML_APP)
        self.assertIn("connectionVisibility", HTML_APP)
        self.assertIn("visibilityStatus", HTML_APP)
        self.assertIn("activeLens", HTML_APP)
        self.assertIn("minEdgeWeight", HTML_APP)
        self.assertIn("nodeBudget", HTML_APP)
        self.assertIn("connectedOnly", HTML_APP)
        self.assertIn('id="connectedOnlyInput"', HTML_APP)
        self.assertIn("connected_only", HTML_APP)
        self.assertIn("Connected only removes isolated nodes", HTML_APP)
        self.assertIn("nodeBudget: 180", HTML_APP)
        self.assertIn("nodeBudget: 12", HTML_APP)
        self.assertIn("focusSelection", HTML_APP)
        self.assertIn("traceMode", HTML_APP)
        self.assertIn("renderCategoryFilters", HTML_APP)
        self.assertIn("renderConnectionFilters", HTML_APP)
        self.assertIn("renderComponentFilterList", HTML_APP)
        self.assertIn("renderComponentFilterWindow", HTML_APP)
        self.assertIn("virtual-filter-list", HTML_APP)
        self.assertIn("COMPONENT_ROW_HEIGHT", HTML_APP)
        self.assertIn("nodeVisibilityReason", HTML_APP)
        self.assertIn("hidden: connected-only", HTML_APP)
        self.assertIn("updateScaleControls", HTML_APP)
        self.assertIn("applyMapLens", HTML_APP)
        self.assertIn("applySmartSimplify", HTML_APP)
        self.assertIn("normalizeClassificationConfig", HTML_APP)
        self.assertIn("owned_prefixes", HTML_APP)
        self.assertIn("hide_packages", HTML_APP)
        self.assertIn("category-muted", HTML_APP)
        self.assertIn("revealSingleNodeFromHiddenCategory", HTML_APP)
        self.assertIn("nodeCategory(other) === category", HTML_APP)
        self.assertIn("state.hiddenNodeIds.add(other.id)", HTML_APP)
        self.assertIn("Check it to show this node", HTML_APP)
        self.assertNotIn("checkbox.disabled = disabled", HTML_APP)
        self.assertIn("nodeCategory", HTML_APP)
        self.assertIn("isServiceNode", HTML_APP)
        self.assertIn("setAllCategoryVisibility", HTML_APP)
        self.assertIn("setAllConnectionVisibility", HTML_APP)
        self.assertIn("setConnectionVisibilitySet", HTML_APP)
        self.assertIn("isCategoryVisible", HTML_APP)
        self.assertIn("isConnectionVisible", HTML_APP)
        self.assertIn("isEdgeConnectionVisible", HTML_APP)
        self.assertIn("edgePassesScale", HTML_APP)
        self.assertIn("renderMapStatus", HTML_APP)
        self.assertIn("appendMapMetric", HTML_APP)
        self.assertIn("visibilityReasonLines", HTML_APP)
        self.assertIn("formatCount", HTML_APP)
        self.assertIn("emptyLimitCounts", HTML_APP)
        self.assertIn("hidden by the node budget", HTML_APP)
        self.assertIn("edgePrimaryConnectionCategories", HTML_APP)
        self.assertIn("edgeCrossesProjectBoundary", HTML_APP)
        self.assertNotIn("primaryVisible && projectVisible", HTML_APP)
        self.assertIn("applyGraphLimit", HTML_APP)
        self.assertIn("nodeIndex: new Map()", HTML_APP)
        self.assertIn("rebuildNodeIndex", HTML_APP)
        self.assertIn("rebuildGraphCache", HTML_APP)
        self.assertIn("nodesByIdForPanel", HTML_APP)
        self.assertIn("visibleNodesById", HTML_APP)
        self.assertIn("categoryByNodeId", HTML_APP)
        self.assertIn("prepareGraphLayoutForCurrentFilters", HTML_APP)
        self.assertIn("nodes.length > 1000 ? 12", HTML_APP)
        self.assertIn("selectedFocusSeeds", HTML_APP)
        self.assertIn("expandNeighborhood", HTML_APP)
        self.assertIn("topNodeIds", HTML_APP)
        self.assertIn("edgeBudgetRank", HTML_APP)
        self.assertIn("const missing = endpoints.filter", HTML_APP)
        self.assertIn("edgeContrastInput", HTML_APP)
        self.assertIn("edgeContrastLabel", HTML_APP)
        self.assertIn("edgeBundlingInput", HTML_APP)
        self.assertIn("edgeContrastRatio", HTML_APP)
        self.assertIn("adjustEdgeAlpha", HTML_APP)
        self.assertIn("edgeWidthScale", HTML_APP)
        self.assertIn("edgeRenderItemsForPanel", HTML_APP)
        self.assertIn("edgeBundlePlan", HTML_APP)
        self.assertIn("LOW_DETAIL_EDGE_ALPHA_FLOOR = 0.18", HTML_APP)
        self.assertIn("drawEdgeRenderItem(item, transform, rect, side, lowDetail)", HTML_APP)
        self.assertIn("edgeBundlePlan(edge, nodesById, edges, lowDetail, side)", HTML_APP)
        self.assertIn("edgeTouchesSelectedNode(edge, side)", HTML_APP)
        self.assertIn("visibleEdgeAlpha", HTML_APP)
        self.assertIn("Math.max(alpha, LOW_DETAIL_EDGE_ALPHA_FLOOR)", HTML_APP)
        self.assertIn("drawFlowPlaybackPathOverlay", HTML_APP)
        self.assertIn("flowPlaybackItemInPath", HTML_APP)
        self.assertIn("flowPlaybackItemRevealed", HTML_APP)
        self.assertIn("flowPlaybackRevealedPathPoints", HTML_APP)
        self.assertIn("points.slice(0, limit)", HTML_APP)
        self.assertIn("flowPlaybackVisitedNodeIds().has(node.id)", HTML_APP)
        self.assertNotIn("const connected = new Set(playback.nodeIds || [])", HTML_APP)
        self.assertIn("state.flowPlayback.edgeKeys.has(edgeKeyForSide", HTML_APP)
        self.assertIn("return 0.024", HTML_APP)
        self.assertIn("state.flowPlayback && state.view === 'architecture'", HTML_APP)
        self.assertIn("(!side && state.flowPlayback) || !state.edgeBundling", HTML_APP)
        self.assertIn('id="commandPalette"', HTML_APP)
        self.assertIn('id="commandPaletteInput"', HTML_APP)
        self.assertIn("openCommandPalette", HTML_APP)
        self.assertIn("handleCommandPaletteKeydown", HTML_APP)
        self.assertIn("commandPaletteActions", HTML_APP)
        self.assertIn("Copy current map link", HTML_APP)
        self.assertIn("Copy compact map link", HTML_APP)
        self.assertIn("Copy clean map link", HTML_APP)
        self.assertIn("Reset shared view state", HTML_APP)
        self.assertIn("Show more nodes", HTML_APP)
        self.assertIn("Reduce map complexity", HTML_APP)
        self.assertIn("Focus evidence search", HTML_APP)
        self.assertIn("Save view preset", HTML_APP)
        self.assertIn("Export view presets", HTML_APP)
        self.assertIn("Import view presets", HTML_APP)
        self.assertIn("Toggle stale UI auto-reload", HTML_APP)
        self.assertIn("Hide third-party", HTML_APP)
        self.assertIn("Trace callers", HTML_APP)
        self.assertIn("Pin current trace", HTML_APP)
        self.assertIn("Clear pinned trace", HTML_APP)
        self.assertIn("Copy agent pack", HTML_APP)
        self.assertIn("Open repo briefing", HTML_APP)
        self.assertNotIn("Open file flow", HTML_APP)
        self.assertIn("First-time brief", HTML_APP)
        self.assertIn("VIEW_CHOICES = ['briefing', 'architecture']", HTML_APP)
        self.assertIn("file_graph", HTML_APP)
        self.assertIn("fileFlowPathIds", HTML_APP)
        self.assertIn("filePath", HTML_APP)
        self.assertIn("Choose connected file", HTML_APP)
        self.assertIn("Pick any indexed file as the first layer", HTML_APP)
        self.assertIn("File Path Explorer", HTML_APP)
        self.assertIn("Start from this file", HTML_APP)
        self.assertIn("Continue from target file", HTML_APP)
        self.assertIn("buildFileFlowLayers", HTML_APP)
        self.assertIn("flowPlaybackRevealedPathPoints", HTML_APP)
        self.assertIn("BRIEFING_SECTION_IDS = ['start', 'concepts', 'runtime', 'core', 'data', 'tests', 'risk', 'agent'", HTML_APP)
        self.assertIn("setGraph('briefing')", HTML_APP)
        self.assertIn("loadBriefing", HTML_APP)
        self.assertIn("renderBriefingFlows", HTML_APP)
        self.assertIn("appendNewEngineerDashboard", HTML_APP)
        self.assertIn("newEngineerPanel", HTML_APP)
        self.assertIn("new_engineer_dashboard", HTML_APP)
        self.assertIn("Read these first", HTML_APP)
        self.assertIn("Understand these flows", HTML_APP)
        self.assertIn("Avoid this noise", HTML_APP)
        self.assertIn("High-risk areas", HTML_APP)
        self.assertIn("briefingEvidenceBlock", HTML_APP)
        self.assertIn("Why?", HTML_APP)
        self.assertIn("Open files", HTML_APP)
        self.assertIn("openBriefingEvidenceFile", HTML_APP)
        self.assertIn("briefingComponentFromPath", HTML_APP)
        self.assertIn("architectureChapterIds", HTML_APP)
        self.assertIn("scheduler-orchestration", HTML_APP)
        self.assertIn("docs-config", HTML_APP)
        self.assertIn("Architecture chapters", HTML_APP)
        self.assertIn("startup-config-flow", HTML_APP)
        self.assertIn("data-model-flow", HTML_APP)
        self.assertIn("test-flow", HTML_APP)
        self.assertIn("git-change-flow", HTML_APP)
        self.assertIn("Play flow", HTML_APP)
        self.assertIn("Play first repo flow", HTML_APP)
        self.assertIn("Flow Playback", HTML_APP)
        self.assertIn("flowStep", HTML_APP)
        self.assertIn("flowPlaybackFocus", HTML_APP)
        self.assertIn("selected.playback.nodeIds", HTML_APP)
        self.assertIn("flowPlaybackFallbackSteps", HTML_APP)
        self.assertIn("drawFlowPlaybackVirtualEdge", HTML_APP)
        self.assertIn("/api/flow-trace", HTML_APP)
        self.assertIn("max_hops", HTML_APP)
        self.assertIn("canonicalTracePrimaryPath", HTML_APP)
        self.assertIn("flowPlaybackDirectedEdge", HTML_APP)
        self.assertIn("mode: 'canonical'", HTML_APP)
        self.assertGreaterEqual(HTML_APP.count("state.flowPlayback.mode === 'canonical'"), 2)
        self.assertIn(
            "Canonical static trace unavailable. Showing inferred reading path.",
            HTML_APP,
        )
        canonical_builder = HTML_APP[
            HTML_APP.index("function buildCanonicalFlowPlayback") :
            HTML_APP.index("function canonicalFlowPlaybackNodeId")
        ]
        self.assertNotIn("flowPlaybackFallbackSteps", canonical_builder)
        self.assertNotIn("normalizeFlowPlaybackSteps", canonical_builder)
        directed_matcher = HTML_APP[
            HTML_APP.index("function flowPlaybackDirectedEdge") :
            HTML_APP.index("function canonicalFlowPlaybackDetail")
        ]
        self.assertNotIn("edge.source === targetId", directed_matcher)
        self.assertIn("playback.steps.length <= 1", HTML_APP)
        self.assertIn("Loading architecture", HTML_APP)
        self.assertIn("Filtering map", HTML_APP)
        self.assertIn("Loading compare", HTML_APP)
        self.assertIn("Building briefing", HTML_APP)
        self.assertIn("setInlineStatusLoading(status, 'Thinking...', true)", HTML_APP)
        self.assertIn("Start here", HTML_APP)
        self.assertIn("Main concepts", HTML_APP)
        self.assertIn("Main runtime flow", HTML_APP)
        self.assertIn("Core components", HTML_APP)
        self.assertIn("Data/state", HTML_APP)
        self.assertIn("label: 'Tests'", HTML_APP)
        self.assertIn("Risk/recent change", HTML_APP)
        self.assertIn("Agent context", HTML_APP)
        self.assertIn("renderBriefingRuntime", HTML_APP)
        self.assertIn("renderBriefingTests", HTML_APP)
        self.assertNotIn("Guided Chapters", HTML_APP)
        self.assertNotIn("Agent Brief", HTML_APP)
        self.assertIn("copyBriefingAgentBrief", HTML_APP)
        self.assertIn('id="briefingRefreshBtn"', HTML_APP)
        self.assertIn('id="briefingCopyBtn"', HTML_APP)
        self.assertIn('id="briefingMapBtn"', HTML_APP)
        self.assertIn("commandActionScore", HTML_APP)
        self.assertIn("commandActionHaystack", HTML_APP)
        self.assertIn("command-group", HTML_APP)
        self.assertIn('id="viewPresetSelect"', HTML_APP)
        self.assertIn("VIEW_PRESETS_KEY", HTML_APP)
        self.assertIn("renderViewPresets", HTML_APP)
        self.assertIn("currentViewPresetPayload", HTML_APP)
        self.assertIn("applyViewPresetPayload", HTML_APP)
        self.assertIn("persistViewPresets", HTML_APP)
        self.assertIn("exportViewPresets", HTML_APP)
        self.assertIn("importViewPresetsFromFile", HTML_APP)
        self.assertIn('id="viewPresetImportInput"', HTML_APP)
        self.assertIn("URL_STATE_KEYS", HTML_APP)
        self.assertIn("readUrlStateFromLocation", HTML_APP)
        self.assertIn("encodeCompactUrlState", HTML_APP)
        self.assertIn("decodeCompactUrlState", HTML_APP)
        self.assertIn("currentUrlStatePayload", HTML_APP)
        self.assertIn("shouldUseCompactUrlState", HTML_APP)
        self.assertIn("applyPendingUrlState", HTML_APP)
        self.assertIn("writeUrlStateToLocation", HTML_APP)
        self.assertIn("clearSharedViewState", HTML_APP)
        self.assertIn("pinnedTraceToUrlValue", HTML_APP)
        self.assertIn("selectedNodeForUrl", HTML_APP)
        self.assertIn("viewportToUrlValue", HTML_APP)
        self.assertIn("window.history.replaceState", HTML_APP)
        self.assertIn("nodeVisibilityReason", HTML_APP)
        self.assertIn("hidden: node budget", HTML_APP)
        self.assertIn("stableKindText", HTML_APP)
        self.assertIn("kind.textContent = stableKindText", HTML_APP)
        self.assertIn("drawMinimapOverlay", HTML_APP)
        self.assertIn("minimapBottomInset", HTML_APP)
        self.assertIn("drawMinimapViewport", HTML_APP)
        self.assertIn("minimapHitAt", HTML_APP)
        self.assertIn("panGraphToMinimapPoint", HTML_APP)
        self.assertIn("activeMinimapNav", HTML_APP)
        self.assertIn("trace-timeline", HTML_APP)
        self.assertIn("traceTimelineEdgesForNode", HTML_APP)
        self.assertIn("detailSearchInput", HTML_APP)
        self.assertIn('id="detailTabs"', HTML_APP)
        self.assertIn("data-detail-tab=\"flow\"", HTML_APP)
        self.assertIn("activeDetailTab", HTML_APP)
        self.assertIn("setDetailTab", HTML_APP)
        self.assertIn("detailTabForTitle", HTML_APP)
        self.assertIn("applyDetailFilters", HTML_APP)
        self.assertIn("detailTab: cleanUrlChoice", HTML_APP)
        self.assertIn("Show Evidence tab", HTML_APP)
        self.assertIn("applyDetailSearchFilter", HTML_APP)
        self.assertIn("applyDetailTabFilter", HTML_APP)
        self.assertIn("nodeDetailCache", HTML_APP)
        self.assertIn("cachedNodeDetail", HTML_APP)
        self.assertIn("invalidateNodeDetailCache", HTML_APP)
        self.assertIn("detail-filter-empty", HTML_APP)
        self.assertIn("detail-tab-empty", HTML_APP)
        self.assertIn("empty-selection-actions", HTML_APP)
        self.assertIn("requestGraphWorkerFilter", HTML_APP)
        self.assertIn("graphWorkerSource", HTML_APP)
        self.assertIn("Graph worker", HTML_APP)
        self.assertIn('id="perfPanel"', HTML_APP)
        self.assertIn("renderPerfPanel", HTML_APP)
        self.assertIn("lastDrawMs", HTML_APP)
        self.assertIn("lastFilterMs", HTML_APP)
        self.assertIn("pinnedTraceSubgraph", HTML_APP)
        self.assertIn("rebuildPinnedTraceCache", HTML_APP)
        self.assertIn("isPinnedTraceEdge", HTML_APP)
        self.assertIn("Large graph guard active", HTML_APP)
        self.assertIn("applyLargeGraphGuard", HTML_APP)
        self.assertIn("hitEdgeRenderItemAt", HTML_APP)
        self.assertIn("renderBundleSelection", HTML_APP)
        self.assertIn("lowDetailMode", HTML_APP)
        self.assertIn("shouldDrawNodeLabel", HTML_APP)
        self.assertIn("Bundled edges show x-counts", HTML_APP)
        self.assertIn("1.15 + Math.log2", HTML_APP)
        self.assertIn("edgeDashPattern", HTML_APP)
        self.assertIn("ctx.setLineDash(edgeDashPattern", HTML_APP)
        self.assertIn("return [10, 5]", HTML_APP)
        self.assertIn("edgeConnectionCategories", HTML_APP)
        self.assertIn("connectionFiltersFocused", HTML_APP)
        self.assertIn("connectionFiltersRestrictNodes", HTML_APP)
        self.assertIn("if (isConnectionVisible('component')) return false;", HTML_APP)
        self.assertIn("endpointIdsForEdges", HTML_APP)
        self.assertIn("connectionHelp", HTML_APP)
        self.assertIn("keeps matching component nodes visible", HTML_APP)
        self.assertIn("it does not delete component/function edges by itself", HTML_APP)
        self.assertIn("['api', 'functions', 'graphql', 'projects']", HTML_APP)
        self.assertIn('id="refreshBtn"', HTML_APP)
        self.assertIn('id="filterPanelToggle"', HTML_APP)
        self.assertIn('id="detailPanelResizer"', HTML_APP)
        self.assertIn('id="nodeContextMenu"', HTML_APP)
        self.assertIn('id="contextEditNodeBtn"', HTML_APP)
        self.assertIn('id="contextServiceNodeBtn"', HTML_APP)
        self.assertIn('id="contextOwnedNodeBtn"', HTML_APP)
        self.assertIn('id="contextTeamNodeBtn"', HTML_APP)
        self.assertIn('id="contextThirdPartyNodeBtn"', HTML_APP)
        self.assertIn('id="contextHideNodeBtn"', HTML_APP)
        self.assertIn("USER_NODE_OVERRIDES_KEY", HTML_APP)
        self.assertIn("EXPECTED_UI_VERSION", HTML_APP)
        self.assertIn("handleGraphContextMenu", HTML_APP)
        self.assertIn("contextNodeAt", HTML_APP)
        self.assertIn("showNodeContextMenu", HTML_APP)
        self.assertIn("hideNodeContextMenu", HTML_APP)
        self.assertIn("openNodeEditor", HTML_APP)
        self.assertIn("renderNodeEditSelection", HTML_APP)
        self.assertIn("saveNodeEditFromForm", HTML_APP)
        self.assertIn("toggleNodeService", HTML_APP)
        self.assertIn("classifyContextNode", HTML_APP)
        self.assertIn("/api/classification", HTML_APP)
        self.assertIn("/api/classification/restore", HTML_APP)
        self.assertIn("showUndoToast", HTML_APP)
        self.assertIn("restoreClassificationFromToast", HTML_APP)
        self.assertIn('id="undoToast"', HTML_APP)
        self.assertIn("previous_classification", HTML_APP)
        self.assertIn("classificationPackageName", HTML_APP)
        self.assertIn("applyConfigUpdate", HTML_APP)
        self.assertIn("resetNodeOverride", HTML_APP)
        self.assertIn("loadNodeOverrides", HTML_APP)
        self.assertIn("persistNodeOverrides", HTML_APP)
        self.assertIn("applyNodeOverride", HTML_APP)
        self.assertIn("Treat as separate service", HTML_APP)
        self.assertIn("Mark as service", HTML_APP)
        self.assertIn("DETAIL_WIDTH_KEY", HTML_APP)
        self.assertIn("loadDetailPanelWidth", HTML_APP)
        self.assertIn("applyDetailPanelWidth", HTML_APP)
        self.assertIn("handleDetailResizeStart", HTML_APP)
        self.assertIn("handleDetailResizeMove", HTML_APP)
        self.assertIn("handleDetailResizeEnd", HTML_APP)
        self.assertIn("resizing-detail", HTML_APP)
        self.assertIn("var(--detail-width)", HTML_APP)
        self.assertIn("filter-header", HTML_APP)
        self.assertIn("filter-panel-body", HTML_APP)
        self.assertIn("filter-sticky", HTML_APP)
        self.assertIn("filter-section", HTML_APP)
        self.assertIn('id="stickyLensLabel"', HTML_APP)
        self.assertIn("toolbar-group", HTML_APP)
        self.assertIn("grid-template-columns: 44px", HTML_APP)
        filter_panel = HTML_APP.split('<aside class="filter-panel">', 1)[1].split("</aside>", 1)[0]
        self.assertIn('id="connectionFilters"', filter_panel)
        self.assertNotIn('id="compareMapControls"', filter_panel)
        self.assertNotIn('id="baseCommitSelect"', filter_panel)
        self.assertNotIn('id="headCommitSelect"', filter_panel)
        self.assertNotIn('id="compareStatus"', filter_panel)
        self.assertIn('id="addConnectionBtn"', HTML_APP)
        self.assertIn('id="saveArchitectureBtn"', HTML_APP)
        self.assertIn('id="savedArchitectures"', HTML_APP)
        self.assertIn("sidebar-collapsed", HTML_APP)
        self.assertIn("toggleFilterPanel", HTML_APP)
        self.assertIn("updateFilterPanelToggle", HTML_APP)
        self.assertIn('id="helpTooltip"', HTML_APP)
        self.assertIn("help-tooltip", HTML_APP)
        self.assertIn("help-icon", HTML_APP)
        self.assertIn(".stat > span:not(.help-icon)", HTML_APP)
        self.assertIn("font-size: 0", HTML_APP)
        self.assertIn("option, .detail-resizer", HTML_APP)
        self.assertIn("data-help", HTML_APP)
        self.assertIn("setHelp", HTML_APP)
        self.assertIn("attachHelpIcon", HTML_APP)
        self.assertIn(".help-icon[data-help]", HTML_APP)
        self.assertIn("handleHelpTooltipOver", HTML_APP)
        self.assertIn("handleHelpTooltipFocus", HTML_APP)
        self.assertIn("positionHelpTooltip", HTML_APP)
        self.assertIn("detailSectionHelp", HTML_APP)
        self.assertIn("edgeHelp", HTML_APP)
        self.assertIn("edgeTypeHelp", HTML_APP)
        self.assertIn("weightHelp", HTML_APP)
        self.assertIn("exampleHelp", HTML_APP)
        self.assertIn("detailLineHelp", HTML_APP)
        self.assertIn("Evidence / Confidence", HTML_APP)
        self.assertIn("appendEdgeEvidenceSection", HTML_APP)
        self.assertIn("edgeEvidencePanel", HTML_APP)
        self.assertIn("edgeConfidenceScore", HTML_APP)
        self.assertIn("edgeProofItems", HTML_APP)
        self.assertIn('id="edgeHoverTooltip"', HTML_APP)
        self.assertIn("updateEdgeHoverFromPointer", HTML_APP)
        self.assertIn("renderEdgeHoverTooltip", HTML_APP)
        self.assertIn("isHoveredEdge", HTML_APP)
        self.assertIn("isHoveredBundle", HTML_APP)
        self.assertIn("traceSelectedEdge", HTML_APP)
        self.assertIn("Trace Flow", HTML_APP)
        self.assertIn("traceNodeMode", HTML_APP)
        self.assertIn("traceModeSubgraph", HTML_APP)
        self.assertIn('id="focusBreadcrumb"', HTML_APP)
        self.assertIn("updateFocusBreadcrumb", HTML_APP)
        self.assertIn("clearFocusBreadcrumb", HTML_APP)
        self.assertIn("directionalTrace", HTML_APP)
        self.assertIn("evidence-panel", HTML_APP)
        self.assertIn("confidence-meter", HTML_APP)
        self.assertIn('id="repoQuestions"', HTML_APP)
        self.assertIn("REPO_QUESTIONS", HTML_APP)
        self.assertIn("renderRepoQuestions", HTML_APP)
        self.assertIn("runStructuralQuery", HTML_APP)
        self.assertIn("/api/query", HTML_APP)
        self.assertIn("Dead code", HTML_APP)
        self.assertIn("Routes", HTML_APP)
        self.assertIn("Context pack", HTML_APP)
        self.assertIn("Verify plan", HTML_APP)
        self.assertIn("Rule checks", HTML_APP)
        self.assertIn("Source outline", HTML_APP)
        self.assertIn("runToolWorkflow", HTML_APP)
        self.assertIn("workflowStatus", HTML_APP)
        self.assertIn("renderWorkflowResult", HTML_APP)
        self.assertIn("renderContextPackResult", HTML_APP)
        self.assertIn("renderVerifyPlanResult", HTML_APP)
        self.assertIn("renderRulesResult", HTML_APP)
        self.assertIn("renderSourceOutlineResult", HTML_APP)
        self.assertIn("renderWorkflowLoading", HTML_APP)
        self.assertIn("renderWorkflowError", HTML_APP)
        self.assertIn("appendWorkflowTabs", HTML_APP)
        self.assertIn("renderRulesFilters", HTML_APP)
        self.assertIn("renderVerificationCommands", HTML_APP)
        self.assertIn("appendWorkflowEmpty", HTML_APP)
        self.assertIn("workflow-mode", HTML_APP)
        self.assertIn("workflow-panel", HTML_APP)
        self.assertIn("workflow-stat-grid", HTML_APP)
        self.assertIn("workflow-progress", HTML_APP)
        self.assertIn("workflow-tabs", HTML_APP)
        self.assertIn("workflow-filter-tabs", HTML_APP)
        self.assertIn("workflow-copy-btn", HTML_APP)
        self.assertIn("workflow-empty", HTML_APP)
        self.assertIn('id="staleBanner"', HTML_APP)
        self.assertIn("renderStaleBanner", HTML_APP)
        self.assertIn("Index may be stale", HTML_APP)
        self.assertIn("Restart CodeAtlas UI server", HTML_APP)
        self.assertIn("expected ' + EXPECTED_UI_VERSION", HTML_APP)
        self.assertIn("running ' + (build.ui_version || 'unknown')", HTML_APP)
        self.assertIn("stale-banner-meta", HTML_APP)
        self.assertIn("Copy command", HTML_APP)
        self.assertIn("Copy restart", HTML_APP)
        self.assertIn("restartCommand", HTML_APP)
        self.assertIn("indexCommand", HTML_APP)
        self.assertIn("AUTO_RELOAD_STALE_KEY", HTML_APP)
        self.assertIn("toggleStaleAutoReload", HTML_APP)
        self.assertIn("staleReloadCountdown", HTML_APP)
        self.assertIn('id="uiErrorPanel"', HTML_APP)
        self.assertIn("reportUiError", HTML_APP)
        self.assertIn("unhandledrejection", HTML_APP)
        self.assertIn('id="buildBadge"', HTML_APP)
        self.assertIn("renderBuildBadge", HTML_APP)
        self.assertIn("applyRuntimeConfig", HTML_APP)
        self.assertIn("server_source_stale", HTML_APP)
        self.assertIn("Server source changed after this CodeAtlas server started", HTML_APP)
        self.assertIn("workflow-actions", HTML_APP)
        self.assertIn("Export JSON", HTML_APP)
        self.assertIn("Export text", HTML_APP)
        self.assertIn("Copy JSON", HTML_APP)
        self.assertIn("downloadWorkflowFile", HTML_APP)
        self.assertIn("cacheLabel", HTML_APP)
        self.assertIn("/api/context-pack", HTML_APP)
        self.assertIn("/api/verify-plan", HTML_APP)
        self.assertIn("/api/rules", HTML_APP)
        self.assertIn("/api/source-outline", HTML_APP)
        self.assertIn("/api/briefing", HTML_APP)
        self.assertIn('id="diagnosticsPanel"', HTML_APP)
        self.assertIn('id="classificationWizard"', HTML_APP)
        self.assertIn("renderClassificationWizard", HTML_APP)
        self.assertIn("saveClassificationFromWizard", HTML_APP)
        self.assertIn("classificationSummaryText", HTML_APP)
        self.assertIn("saveClassificationPackage", HTML_APP)
        self.assertIn("renderDiagnostics", HTML_APP)
        self.assertIn("External deps", HTML_APP)
        self.assertIn("Parser errors", HTML_APP)
        self.assertIn("Stale index", HTML_APP)
        self.assertIn('id="agentContextBtn"', HTML_APP)
        self.assertIn("/api/agent-context", HTML_APP)
        self.assertNotIn('id="zoomInBtn"', HTML_APP)
        self.assertNotIn('id="zoomOutBtn"', HTML_APP)
        self.assertIn('id="baseCommitSelect"', HTML_APP)
        self.assertIn('id="headCommitSelect"', HTML_APP)
        self.assertIn('id="compareMapControls"', HTML_APP)
        self.assertIn('id="compareMapControls" class="compare-map-controls" hidden aria-hidden="true"', HTML_APP)
        self.assertIn("compare-map-controls", HTML_APP)
        self.assertIn("selectedCompareRefs", HTML_APP)
        self.assertIn("compareRefsFromSelectors", HTML_APP)
        self.assertNotIn('id="topCompareControls"', HTML_APP)
        self.assertNotIn('id="baseRefInput"', HTML_APP)
        self.assertNotIn('id="headRefInput"', HTML_APP)
        self.assertIn('id="runCompareBtn"', HTML_APP)
        self.assertNotIn('id="runTopCompareBtn"', HTML_APP)
        self.assertIn('id="diffToggleBtn"', HTML_APP)
        self.assertIn('id="compareChangesOnlyBtn"', HTML_APP)
        self.assertIn('id="compareSyncBtn"', HTML_APP)
        self.assertIn('id="compareExplainBtn"', HTML_APP)
        self.assertIn("compareChangesOnly", HTML_APP)
        self.assertIn("compareSyncViewports", HTML_APP)
        self.assertIn("toggleCompareChangesOnly", HTML_APP)
        self.assertIn("toggleCompareViewportSync", HTML_APP)
        self.assertIn("syncCompareViewport", HTML_APP)
        self.assertIn("compareChangeGate", HTML_APP)
        self.assertIn("compareHasAnyChanges", HTML_APP)
        self.assertIn("compareHidden", HTML_APP)
        self.assertIn("appendCompareNodeDiffSection", HTML_APP)
        self.assertIn("appendCompareEdgeDiffSection", HTML_APP)
        self.assertIn("compare-diff-grid", HTML_APP)
        self.assertIn("Compare Impact", HTML_APP)
        self.assertIn("compareImpactItems", HTML_APP)
        self.assertIn("explainCompareDiff", HTML_APP)
        self.assertIn("compareDiffBriefMarkdown", HTML_APP)
        self.assertIn("drawCompareTimeline", HTML_APP)
        self.assertIn("drawComparePaneHeader", HTML_APP)
        self.assertIn("toggleDiffHighlight", HTML_APP)
        self.assertIn("updateDiffToggle", HTML_APP)
        self.assertIn("shouldHighlightDiff", HTML_APP)
        self.assertIn("compareDiffFocus", HTML_APP)
        self.assertIn("compareDiffNodeAlpha", HTML_APP)
        self.assertIn("hasChange", HTML_APP)
        self.assertIn("bundleAlpha", HTML_APP)
        self.assertIn("isSelectedBundle", HTML_APP)
        self.assertIn('id="askBtn"', HTML_APP)
        self.assertIn("/api/refresh", HTML_APP)
        self.assertIn("/api/compare/warm", HTML_APP)
        self.assertIn("/api/chat", HTML_APP)
        self.assertIn("/api/index-status", HTML_APP)
        self.assertIn("populateCommitSelectors", HTML_APP)
        self.assertIn("commitOptionsFromPayload", HTML_APP)
        self.assertIn("scheduleCompareWarmup", HTML_APP)
        self.assertIn("compareInFlight", HTML_APP)
        self.assertIn("'dateutil'", HTML_APP)
        self.assertIn("'operator'", HTML_APP)
        self.assertIn("setZoom", HTML_APP)
        self.assertIn('id="fitSelectionBtn"', HTML_APP)
        self.assertIn("focusCameraOnSelection", HTML_APP)
        self.assertIn("selectionCameraNodes", HTML_APP)
        self.assertIn("fitCameraToNodes", HTML_APP)
        self.assertIn("animateViewportTo", HTML_APP)
        self.assertIn("graphBaseFitForRect", HTML_APP)
        self.assertIn("CANVAS_ZOOM_MAX = 18", HTML_APP)
        self.assertIn("CANVAS_ZOOM_MIN = 0.12", HTML_APP)
        self.assertIn("handleGraphWheel", HTML_APP)
        self.assertIn("handleGraphPointerDown", HTML_APP)
        self.assertIn("handleGraphPointerMove", HTML_APP)
        self.assertIn("compareViewports", HTML_APP)
        self.assertIn("compareSideForPoint", HTML_APP)
        self.assertIn("graphViewport", HTML_APP)
        self.assertIn("if (side && state.compareViewports[side]) return state.compareViewports[side];", HTML_APP)
        self.assertIn("activePanSide", HTML_APP)
        self.assertIn("resetCompareViewports", HTML_APP)
        self.assertIn("withGraphPanelClip", HTML_APP)
        self.assertIn("drawClippedGraphPanel", HTML_APP)
        self.assertIn("ctx.clip()", HTML_APP)
        self.assertIn("gesturechange", HTML_APP)
        self.assertIn("canvas.panning", HTML_APP)
        self.assertIn("LAYOUT_KEY", HTML_APP)
        self.assertIn("loadLayoutStore", HTML_APP)
        self.assertIn("saveLayoutStore", HTML_APP)
        self.assertIn("layoutStorageKey", HTML_APP)
        self.assertIn("savedLayoutPosition", HTML_APP)
        self.assertIn("deterministicNodePosition", HTML_APP)
        self.assertIn("prepareStableLayout", HTML_APP)
        self.assertIn("prepareCompareStableLayout", HTML_APP)
        self.assertIn("placeNewNodesNearNeighbors", HTML_APP)
        self.assertIn("const nodesById = new Map(nodes.map(node => [node.id, node]));", HTML_APP)
        self.assertNotIn(
            "function placeNewNodesNearNeighbors(nodes, edges) {\n"
            "      const nodesById = nodesByIdForPanel(nodes, side);",
            HTML_APP,
        )
        self.assertIn("rememberLayoutPositions", HTML_APP)
        self.assertIn("layoutFrameNodes", HTML_APP)
        self.assertNotIn("function simulate()", HTML_APP)
        self.assertIn("distanceToSegment", HTML_APP)
        self.assertIn("graphFocus", HTML_APP)
        self.assertIn("nodeFocusAlpha", HTML_APP)
        self.assertIn("selectStat", HTML_APP)
        self.assertIn("renderStatSelection", HTML_APP)
        self.assertIn(".stat > .help-icon", HTML_APP)
        self.assertIn("inventory-row", HTML_APP)
        self.assertIn("Show next", HTML_APP)
        self.assertIn("renderEdgeSelection", HTML_APP)
        self.assertIn("renderNodeConnections", HTML_APP)
        self.assertIn("nodeEdgesForSelection", HTML_APP)
        self.assertIn("appendGroupedEdgeGroupSection", HTML_APP)
        self.assertIn("appendRemainingCounterpartGroups", HTML_APP)
        self.assertIn("renderLazyDetails", HTML_APP)
        self.assertIn("Open to render this list", HTML_APP)
        self.assertIn("more connected components", HTML_APP)
        self.assertIn("detailSectionClass", HTML_APP)
        self.assertIn("section-functions", HTML_APP)
        self.assertIn("section-components", HTML_APP)
        self.assertIn("edge-detail", HTML_APP)
        self.assertIn(".section-functions .edge-detail > summary", HTML_APP)
        self.assertIn(".edge-detail > summary { color: inherit; }", HTML_APP)
        self.assertIn("example-detail", HTML_APP)
        self.assertIn("examples-section", HTML_APP)
        self.assertIn("summary-label", HTML_APP)
        self.assertIn("edgeGroupsByCounterpart", HTML_APP)
        self.assertIn("edgeCounterpartId", HTML_APP)
        self.assertIn("edge-component-group", HTML_APP)
        self.assertIn('id="savedPaths"', HTML_APP)
        self.assertIn("savePath", HTML_APP)
        self.assertIn("selectPath", HTML_APP)
        self.assertIn("renderPathSelection", HTML_APP)
        self.assertIn("symbolLocationsForEndpoint", HTML_APP)
        self.assertIn("isSelectedPathEdge", HTML_APP)
        self.assertIn("Color guide", HTML_APP)
        self.assertIn("Trace path", HTML_APP)
        self.assertIn("USER_ARCH_KEY", HTML_APP)
        self.assertIn("architectureGraphWithOverlay", HTML_APP)
        self.assertIn("openAddConnectionForm", HTML_APP)
        self.assertIn("addUserConnectionFromForm", HTML_APP)
        self.assertIn("saveCurrentArchitecture", HTML_APP)
        self.assertIn("applySavedArchitecture", HTML_APP)
        self.assertIn("customVertex", HTML_APP)
        self.assertIn("api_call", HTML_APP)
        self.assertIn("function_call", HTML_APP)
        self.assertIn("test_covers", HTML_APP)
        self.assertIn("renderDetailLines", HTML_APP)
        self.assertIn("edgeRow", HTML_APP)
        self.assertIn("edge-row", HTML_APP)
        self.assertIn("appendRemainingEdgeDetails", HTML_APP)
        self.assertIn("appendRemainingEdgeRows", HTML_APP)
        self.assertIn("more visible edges", HTML_APP)
        self.assertIn("badgeClass", HTML_APP)
        self.assertIn("appendExampleSection", HTML_APP)
        self.assertIn("appendExampleDetail", HTML_APP)
        self.assertIn("appendRemainingExamples", HTML_APP)
        self.assertIn("remaining-examples", HTML_APP)
        self.assertIn("Expand this to inspect the hidden examples", HTML_APP)
        self.assertIn("Flow / Direction", HTML_APP)
        self.assertIn("appendEdgeFlowSection", HTML_APP)
        self.assertIn("edgeFlowCard", HTML_APP)
        self.assertIn("exampleFlowCard", HTML_APP)
        self.assertIn("flowEndpointCard", HTML_APP)
        self.assertIn("appendParameterChips", HTML_APP)
        self.assertIn("edgeDirectionVerb", HTML_APP)
        self.assertIn("edgeIsDirectional", HTML_APP)
        self.assertIn("compactFlowTitle", HTML_APP)
        self.assertIn("nodeForId", HTML_APP)
        self.assertIn("flow-card", HTML_APP)
        self.assertIn("flow-row", HTML_APP)
        self.assertIn("flow-endpoint", HTML_APP)
        self.assertIn("flow-arrow", HTML_APP)
        self.assertIn("param-chip", HTML_APP)
        self.assertIn("Call expression", HTML_APP)
        self.assertIn("Parameters passed", HTML_APP)
        self.assertIn("Source / starts here", HTML_APP)
        self.assertIn("Target / points here", HTML_APP)
        self.assertIn("segmentedIdentifierHtml", HTML_APP)
        self.assertIn("identifier-segment", HTML_APP)
        self.assertIn("identifier-dot", HTML_APP)
        self.assertIn("detail-card", HTML_APP)
        self.assertIn("detail-call", HTML_APP)
        self.assertIn("detail-type", HTML_APP)
        self.assertIn("detail-component", HTML_APP)
        self.assertIn("detail-weight", HTML_APP)
        self.assertIn("detail-signature", HTML_APP)
        self.assertIn("parameters:", HTML_APP)
        self.assertIn("renderEdgeExample", HTML_APP)
        self.assertIn("target signature", HTML_APP)
        self.assertIn("drawCompare", HTML_APP)

    def test_find_available_port_reports_permission_denied(self) -> None:
        class PermissionDeniedSocket:
            def __enter__(self) -> "PermissionDeniedSocket":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def setsockopt(self, *args: object) -> None:
                return None

            def bind(self, address: tuple[str, int]) -> None:
                raise PermissionError(1, "Operation not permitted")

        with mock.patch("codeatlas.visualization.socket.socket", return_value=PermissionDeniedSocket()):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                find_available_port("127.0.0.1", 8852)
