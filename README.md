# CodeAtlas

CodeAtlas is a local-first repository intelligence platform for AI coding assistants. It indexes a repository once, builds a graph of files, modules, classes, functions, methods, imports, calls, inheritance, and references, then layers repository memory on top of git history and documentation.

The goal is to reduce repeated repository reads, improve warm-start retrieval speed, and explain not only what code exists, but why the repository evolved the way it did. CodeAtlas does not call OpenAI, Anthropic, or any cloud API.

## Status

This repository contains a working Python-first implementation with early JavaScript and TypeScript support:

- Tree-sitter parser plugin for Python
- Regex-backed JavaScript and TypeScript parser plugin for components, functions, imports, tests, and route-like handlers
- SQLite graph store under `.codeatlas/index.db`
- Incremental indexing by file hash
- Graph-aware context retrieval with token estimates
- Repository Memory Engine for git history, README/docs, ADRs, RFCs, release notes, and design docs
- Commit intelligence heuristics for purpose, motivation, impacted components, risk, and architectural impact
- Repository Time Machine, ownership intelligence, decision lookup, architecture findings, and compressed repository context
- Local browser visualization for evidence-first architecture, commit-history, compare, diagnostics, and workflow maps
- Agent context packs, verification plans, built-in rule checks, source outlines, graph artifacts, and external index import
- Typer CLI with Rich output
- Watchdog-based watch mode
- MCP tool handlers and optional FastMCP server
- Benchmark runner using actual repository metrics
- Pytest-compatible tests

## Architecture

```text
Repository
  -> Scanner
  -> Parser plugins
  -> AST extraction
  -> Semantic resolution layer
  -> Graph builder
  -> SQLite graph store
  -> Retrieval engine --------\
  -> Git/docs memory engine ---+-> Context compression
  -> CLI + MCP server
  -> Claude Code / Codex
```

The core design keeps expensive work in the indexing phase. Retrieval uses the persisted SQLite index, graph traversal, memory evidence, and selected snippet reads. It does not re-scan or re-parse the full repository for normal queries.

## Project Structure

```text
src/codeatlas/
  cli.py              # codeatlas command surface
  indexer.py          # repository indexing and incremental updates
  retrieval.py        # context ranking, token reports, dependency explanation
  storage.py          # SQLite schema and graph persistence
  scanner.py          # source file discovery and ignore rules
  graph.py            # graph neighborhood helpers
  memory.py           # repository memory, history, ownership, decisions
  benchmark.py        # measured benchmark report
  watcher.py          # watchdog integration
  mcp_server.py       # MCP tools and FastMCP adapter
  semantic.py         # optional Pyright/BasedPyright hook
  packs.py            # redacted AI context pack generation
  rules.py            # built-in local static rule checks
  verification.py     # changed-file verification plans
  source.py           # source-outline explorer
  external_index.py   # generic/SCIP-style JSON import
  assets/
    visualization.html
    visualization.css
    visualization.js
  parsers/
    base.py
    python.py         # Tree-sitter Python extractor
    javascript.py     # JavaScript/TypeScript extractor
    registry.py
tests/
docs/
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,mcp,semantic]'
```

For a minimal local CLI install:

```bash
pip install -e .
```

Runtime analysis is local. The package dependencies are installed once into your environment; CodeAtlas itself does not need network access to index or query repositories.

## Project Configuration

CodeAtlas reads an optional `.codeatlas.yml` from the repository root. If the file is missing, built-in defaults preserve current behavior.

```yaml
version: 1

languages:
  python: true
  javascript: true

ignore:
  dirs:
    - .tox
  paths:
    - generated/**
    - vendor/**

rules:
  enabled: true
  tests_lower_severity: true
  suppressions:
    - rule: possible-secret
      path: tests/fixtures/**
      reason: fixture data
  severity_overrides:
    fetch-without-abort: low

ui:
  default_lens: overview
  node_budget: 180
  min_edge_weight: 1
  connected_only: true
  edge_contrast: 64

classification:
  owned_prefixes:
    - my_product
  team_prefixes:
    - company_
  company_prefixes:
    - "@company/"
  third_party_packages:
    - requests
  hide_packages:
    - docutils
    - sphinx
  show_packages:
    - company_sdk

cache:
  enabled: true
  ttl_seconds: 300
```

The config controls language indexing, ignored paths, rule suppressions, severity overrides, test-file severity lowering, default map lens/budget, edge contrast, connected-only graph defaults, package classification, and workflow-cache TTL. Classification lets the UI separate owned code, team/company dependencies, third-party packages, docs/config, tests, and generated files. The browser UI shows the active config fingerprint and stale-server warnings in the header so stale-build or stale-config confusion is easier to spot.

## Indexing Workflow

Index a repository:

```bash
codeatlas index /path/to/repo
```

Artifacts are written into the target repository:

```text
.codeatlas/
  index.db
  metadata.json
  stats.json
  cache/
```

Ignored directories include `.git`, `node_modules`, `build`, `dist`, `.venv`, `venv`, `__pycache__`, `coverage`, and `target`.

Run an incremental update:

```bash
codeatlas index /path/to/repo --incremental
```

Watch for changes:

```bash
codeatlas watch /path/to/repo
```

Incremental indexing compares file hashes and only reparses changed files. Deleted files are removed from the SQLite graph.

## Repository Memory Workflow

Index repository memory:

```bash
codeatlas memory /path/to/repo
```

This adds memory tables to `.codeatlas/index.db` beside the existing code graph. The memory engine currently mines:

- git commits, authors, timestamps, commit subjects, and changed files
- README files
- docs folders
- ADR/RFC/design folders
- changelogs and release notes

It creates repository-memory entities such as `Repository`, `Module`, `Feature`, `Developer`, `Commit`, `PullRequest`, `ArchitectureDecision`, `RepositoryEvent`, `Incident`, and `Release`. Relationships include `introduced_by`, `modified_by`, `reviewed_by`, `caused_by`, `related_to`, `superseded_by`, `depends_on`, and `contributes_to`.

Ask historical and reasoning questions:

```bash
codeatlas history auth --repo-path /path/to/repo
codeatlas ownership payments --repo-path /path/to/repo
codeatlas decisions "Why was Redis introduced?" --repo-path /path/to/repo
codeatlas architecture "cache" --repo-path /path/to/repo
codeatlas repo-context "authentication" --repo-path /path/to/repo
codeatlas nexus auth --repo-path /path/to/repo
```

Every memory answer returns evidence from commits or documents. If CodeAtlas does not have evidence, it says so instead of inventing an answer.

## Git Nexus And Impact Review

CodeAtlas also builds a lightweight git nexus from commit co-change history:

- file memory nodes for files touched by commits
- file-to-file co-change links
- component hotspots from churn, authors, and files touched
- ownership links from authors to files/components
- FTS5-backed evidence search over commit and document memory
- a local browser map with architecture and commit-history views

Review local changes against historical context:

```bash
codeatlas impact /path/to/repo --base-ref HEAD
```

This produces an impact-radius panel with changed files, risk levels, historical owners, co-change neighbors, related commits, and a token-savings estimate comparing raw changed-file context with compressed impact context.

Find active or risky areas:

```bash
codeatlas hotspots /path/to/repo
```

Summarize a component or path from git memory:

```bash
codeatlas nexus auth --repo-path /path/to/repo
```

## Agent Workflows

CodeAtlas includes first-class workflows for AI coding assistants and human review.

Generate a redacted context pack for Codex, Claude, or another local agent:

```bash
codeatlas context-pack "fix checkout timeout handling" --repo-path /path/to/repo
codeatlas context-pack --task-file issue.md --repo-path /path/to/repo --format json
codeatlas context-pack "explain auth retries" --repo-path /path/to/repo --format xml --output context.xml
```

The pack includes relevant files, exact snippets, evidence, likely owners, built-in rule findings, a source outline, and suggested verification commands. Secret-like assignments are redacted before rendering.

Build a verification plan from local git changes:

```bash
codeatlas verify-plan /path/to/repo --base-ref HEAD --task "edit auth retry logic"
```

Run built-in static checks:

```bash
codeatlas rules /path/to/repo
codeatlas rules /path/to/repo --severity high
```

The built-in checks are intentionally conservative and local. They currently flag common review smells such as hard-coded secret-like assignments, `requests` calls without timeouts, `shell=True`, dynamic code execution, interpolated SQL, and uncancelled `fetch` calls.

Explore source outlines by symbol or path:

```bash
codeatlas outline /path/to/repo --query "PaymentService"
```

Share or hydrate an index artifact:

```bash
codeatlas export-graph /path/to/repo
codeatlas import-graph /path/to/repo --overwrite
```

Import an external code-intelligence JSON index:

```bash
codeatlas import-index scip-index.json --repo-path /path/to/repo --format scip-json
```

The importer accepts a simple generic JSON shape with `symbols` and `edges`, plus SCIP-style JSON with `documents`, document-level `symbols`, relationships, and definition occurrences.

## Local Architecture And Commit Map

Open a repository map in your browser:

```bash
codeatlas serve /path/to/repo
```

The command refreshes the code graph and repository memory, starts a local server, and opens a webpage by default. The map has three views:

- Architecture view: major components, internal imports/calls, external modules/services, service-like nodes, and git co-change links.
- Commit view: developers, commits, and the components each commit touched.
- Compare view: two git refs or commits are archived into temporary snapshots, indexed side by side, and annotated with added, removed, or changed architecture. Compare defaults to a change-first view, can reveal unchanged context on demand, can sync or unlock before/after pan and zoom, and includes before/after evidence cards plus a ranked Compare Impact queue.

Use the 2D/3D toggle to switch between a flat force map and a lightweight depth view. Use the left filter rail to hide/show components, common library nodes, and compare specific commit refs. For terminal-only workflows:

```bash
codeatlas serve /path/to/repo --no-open --port 8765
```

The right rail exposes product workflows for "Where start?", recent changes, risky code, API/data flow, routes, dead code, owners, context packs, rule checks, source outlines, and verification plans. Workflow results render as compact evidence cards so the graph stays a navigation layer instead of the only source of truth. Selecting a node, edge, saved path, or compare impact item centers the camera on that evidence. In compare mode, the Explain action writes a concise diff brief with the most important changed nodes/edges and suggested verification focus. Right-click classification changes show an Undo toast before the config edit becomes old news. Dense maps use level-of-detail rendering: noisy import/reference/dependency edges are bundled with x-count labels while exact edges remain available from the bundle detail panel.

Workflow results include export buttons for JSON and rendered text/Markdown. Slow workflows are cached under `.codeatlas/cache/workflows/`; the cache is invalidated when the index DB mtime or `.codeatlas.yml` fingerprint changes.

## Retrieval Flow

Retrieve context:

```bash
codeatlas context "create_order" --repo-path /path/to/repo
```

Tune graph depth and token budget:

```bash
codeatlas context "create_order" --repo-path /path/to/repo --depth 2 --max-tokens 8000
```

Retrieval:

1. Finds matching symbols in SQLite.
2. Traverses nearby graph nodes.
3. Scores exact matches, callers, callees, inheritance, references, and containment.
4. Reads only selected snippets.
5. Stops at the approximate token budget.
6. Prints baseline, optimized, and savings estimates.

Token estimation uses:

```text
1 token ~= 4 characters
```

The baseline is calculated from indexed files in the same related directories as the returned snippets. The optimized count is calculated from the snippets actually returned.

## Graph Design

Node types:

- `FILE`
- `MODULE`
- `CLASS`
- `FUNCTION`
- `METHOD`
- `SYMBOL`

Edge types:

- `CONTAINS`
- `IMPORTS`
- `CALLS`
- `REFERENCES`
- `DEFINES`
- `INHERITS`

SQLite tables include:

- `files`
- `symbols`
- `imports`
- `nodes`
- `edges`
- `metadata`

Convenience views are created for `classes`, `functions`, and `methods`.

## CLI Commands

```bash
codeatlas index /path/to/repo
codeatlas memory /path/to/repo
codeatlas context "query" --repo-path /path/to/repo
codeatlas history "topic" --repo-path /path/to/repo
codeatlas ownership "topic" --repo-path /path/to/repo
codeatlas decisions "question" --repo-path /path/to/repo
codeatlas architecture "topic" --repo-path /path/to/repo
codeatlas repo-context "query" --repo-path /path/to/repo
codeatlas agent-context "task" --repo-path /path/to/repo
codeatlas context-pack "task" --repo-path /path/to/repo --format markdown
codeatlas export-graph /path/to/repo
codeatlas import-graph /path/to/repo
codeatlas index-status /path/to/repo
codeatlas query "callers:symbol" --repo-path /path/to/repo
codeatlas dead-code /path/to/repo
codeatlas routes /path/to/repo
codeatlas http-confidence /path/to/repo
codeatlas install-agent /path/to/repo
codeatlas rules /path/to/repo
codeatlas verify-plan /path/to/repo --base-ref HEAD
codeatlas outline /path/to/repo --query "query"
codeatlas import-index scip-index.json --repo-path /path/to/repo
codeatlas impact /path/to/repo --base-ref HEAD
codeatlas hotspots /path/to/repo
codeatlas nexus "topic" --repo-path /path/to/repo
codeatlas graph "SymbolName" --repo-path /path/to/repo
codeatlas serve /path/to/repo
codeatlas benchmark /path/to/repo --query "query"
codeatlas watch /path/to/repo
codeatlas stats /path/to/repo
codeatlas mcp --repo-path /path/to/repo
```

## MCP Integration

Run:

```bash
codeatlas mcp --repo-path /path/to/repo
```

Exposed tools:

- `get_context(query, max_tokens)`
- `get_history(topic, limit)`
- `get_architecture(topic, limit)`
- `get_ownership(topic, limit)`
- `get_dependencies(symbol_name)`
- `get_api_flow(query)`
- `get_decisions(question, limit)`
- `search_memory(query, limit)`
- `get_impact(base_ref)`
- `get_hotspots(limit)`
- `get_nexus(topic)`
- `get_visual_map()`
- `get_index_status()`
- `query_code_graph(expression, limit)`
- `find_dead_code(limit)`
- `get_routes(limit)`
- `get_http_confidence(limit)`
- `export_graph()`
- `import_graph(overwrite)`
- `get_context_pack(task, max_tokens, output_format)`
- `get_verification_plan(base_ref, task)`
- `run_rules(limit, severity)`
- `get_source_outline(query, limit)`
- `import_code_index(input_path, index_format)`
- `get_code_context(query, max_tokens, depth)`
- `find_symbol(symbol_name)`
- `explain_dependencies(symbol_name)`
- `token_report(query)`
- `repository_stats()`

The MCP adapter uses FastMCP when installed. The underlying tool handlers are plain Python functions, which keeps them easy to test and reuse.

## Benchmarking

Run:

```bash
codeatlas benchmark /path/to/repo --query "create_order"
```

Measured values include:

- indexing duration
- warm retrieval latency
- files scanned
- files returned
- estimated baseline tokens
- estimated optimized tokens
- token reduction percentage
- graph traversal time
- retrieval accuracy label

CodeAtlas does not hardcode performance claims. Benchmark output is calculated from the repository being measured.

## Implementation Plan

The implementation is organized in layers:

1. Storage layer: durable SQLite schema and graph operations.
2. Parser layer: Tree-sitter plugin interface, currently Python.
3. Indexer: scan, parse, persist, and update graph sections.
4. Retrieval engine: lookup, traversal, ranking, snippets, token reporting.
5. Memory engine: git/document evidence extraction, memory entities, relationships, and history queries.
6. Context compression: architecture, history, decisions, ownership, dependencies, files, and related changes.
7. CLI and MCP: local user and assistant interfaces.
8. Benchmarks and tests: measured reports and regression coverage.

## Limitations

- Python has the strongest parser support today. JavaScript and TypeScript support exists, but it is currently regex-backed and less precise than a full Tree-sitter/LSP implementation.
- Pyright/BasedPyright integration is currently an optional diagnostics hook; deeper type-aware reference resolution is a future extension.
- Retrieval reads selected snippets from disk. It does not re-parse the repository, but it expects files to remain present after indexing.
- Call resolution is name-based and prefers same-module matches before shortest qualified names.
- Token counts are estimates, not tokenizer-specific counts.
- PR review comments and approvals are not fetched from GitHub yet; PullRequest entities are currently inferred from commit messages.
- Commit intelligence uses deterministic local heuristics, not an LLM. It cites evidence but should be treated as an initial signal.
- API-flow and infrastructure intelligence have conservative MCP surfaces, but deep endpoint/runtime topology extraction is still roadmap work.
- Impact radius is conservative and based on local git diff, file names, ownership, co-change history, and indexed evidence. It is not a substitute for test execution.
- Built-in rule checks are review aids, not a full security scanner or CodeQL/Semgrep replacement.
- External index import currently supports generic JSON and SCIP-like JSON shapes; exact SCIP protobuf ingestion is not implemented yet.

## Roadmap

- Replace regex-backed JavaScript and TypeScript extraction with Tree-sitter/LSP-backed symbol import.
- Add Go and Java parser plugins.
- Deepen Pyright/BasedPyright semantic integration for exact cross-file references.
- Add exact SCIP protobuf import and richer external index validation.
- Add configurable rule packs and project-owned suppressions.
- Render issue/PR context directly into context packs when GitHub/GitLab ingestion is enabled.
- Store optional snippet cache for fully DB-backed retrieval.
- Add graph export formats such as GraphML and JSON.
- Add richer benchmark suites with labeled expected contexts.
- Add package-aware import resolution for monorepos.
- Add GitHub/GitLab PR ingestion for review comments, approvals, and requested changes.
- Add exact architecture evolution diffing between graph snapshots.
- Add API route/event/infra parsers for end-to-end flow intelligence.
- Add confidence calibration and larger evidence-backed benchmark suites.

## Development

Run tests without installing Pytest:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Run with Pytest after installing dev dependencies:

```bash
pytest
```

Optional browser smoke test:

```bash
pip install -e '.[ui]'
python -m playwright install chromium
codeatlas ui-smoke http://127.0.0.1:8852/ --screenshot-dir /tmp/codeatlas-ui
```

This requires Playwright and a running CodeAtlas server. Restart the server after frontend asset edits so stale browser/server UI warnings clear. The smoke wrapper sets `CODEATLAS_UI_URL`; `--screenshot-dir` keeps map and command-palette screenshots from the visual smoke. It verifies that the graph canvas, build badge, edge contrast and bundling controls, compare mode controls, fit/undo controls, workflow buttons, workflow result cards, export buttons, and visual-regression surfaces render.
