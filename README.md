# CodeAtlas

CodeAtlas is a local-first repository intelligence platform for AI coding assistants. It indexes a repository once, builds a graph of files, modules, classes, functions, methods, imports, calls, inheritance, and references, then layers repository memory on top of git history and documentation.

The goal is to reduce repeated repository reads, improve warm-start retrieval speed, and explain not only what code exists, but why the repository evolved the way it did. CodeAtlas does not call OpenAI, Anthropic, or any cloud API.

## Status

This repository contains a working local-first implementation with Python, JavaScript, and TypeScript support:

- Tree-sitter parser plugins for Python, JavaScript, and TypeScript/TSX
- SQLite graph store under `.codeatlas/index.db`
- Incremental content parsing with semantic relationship re-resolution
- Graph-aware context retrieval with token estimates
- Repository Memory Engine for git history, README/docs, ADRs, RFCs, release notes, and design docs
- Commit intelligence heuristics for purpose, motivation, impacted components, risk, and architectural impact
- Repository Time Machine, ownership intelligence, decision lookup, architecture findings, and compressed repository context
- Local browser visualization scoped to an evidence-first briefing page and architecture map
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

The core design keeps expensive work in the indexing phase. Retrieval uses the persisted SQLite index, graph traversal, memory evidence, and DB-cached snippets. It does not re-scan or re-parse the full repository for normal queries.

## Project Structure

```text
src/codeatlas/
  cli.py              # codeatlas command surface
  indexer.py          # repository indexing and incremental updates
  retrieval.py        # context ranking, token reports, dependency explanation
  flow_trace.py       # canonical directed static route/call traces
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

`stats.json` includes parse-quality signals for regression checks, including per-file
`symbols_per_kloc` and `unresolved_call_ratio` plus repository-level summaries.

Ignored directories include `.git`, `node_modules`, `build`, `dist`, `.venv`, `venv`, `__pycache__`, `coverage`, and `target`.

Run an incremental update:

```bash
codeatlas index /path/to/repo --incremental
```

Watch for changes:

```bash
codeatlas watch /path/to/repo
```

Incremental indexing separates content parsing from semantic relationship re-resolution. It
compares file hashes, content-parses only added or changed files, and removes deleted files from
the SQLite graph. Before replacing changed graph sections, it snapshots their indexed definitions
and imports; after all changed definitions are installed, it refreshes parser-produced `CALLS`,
`REFERENCES`, `INHERITS`, and `HTTP_CALLS` edges for files whose resolution may have changed.

The affected-file strategy is deliberately conservative:

- If the indexed definition universe changes (file, module, simple name, qualified name, or kind),
  schema v3 cannot identify every unresolved semantic use because those raw uses are not all
  persisted. CodeAtlas therefore reparses and semantically re-resolves every otherwise unchanged
  current source file. The index report sets `conservative_fallback_used=true` and includes a
  `conservative_fallback_reason`.
- If definitions are stable but imports change, CodeAtlas targets unchanged dependents using the
  existing semantic edges, stored import records, and indexed source text. If that targeted lookup
  fails, it falls back to re-resolving every unchanged source file and reports the reason.
- No-op and body-only changes that leave definitions and imports stable do not trigger semantic
  re-resolution of unchanged files.

The CLI and persisted `last_index_report` distinguish the work performed:

- `files_content_parsed` is the number of content-changed files parsed (and matches the legacy
  `files_indexed` count).
- `files_semantically_reresolved` counts unchanged files parsed only to regenerate semantic edges.
- `files_skipped` retains its compatibility meaning of content-unchanged, so that count can include
  files also reported as semantically re-resolved.
- `relationships_removed` counts prior parser-produced semantic edge rows cleared for the files in
  the update, while `relationships_replaced` counts the regenerated semantic edge rows present
  afterward.
- `conservative_fallback_used` and `conservative_fallback_reason` make repository-wide semantic
  fallback explicit. The same fields are exposed by `codeatlas index-status`.

Every incremental run still scans supported files to compare hashes. A no-op or body-only update
adds parsing work only for changed files; an import-only change may reparse a targeted dependent
set; a definition change intentionally can reparse all supported files for semantic consistency.
That worst case approaches a repository-wide parse, but it rebuilds only resolution relationships
for unchanged files rather than rewriting their cached content, definitions, and snippets. Indexed
file text and symbol snippets remain cached in SQLite, so graph artifacts can still return exact
snippets after export/import or source-file moves.

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
- a local browser map that keeps git evidence available in the briefing and architecture workflows

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

Import a precise external code-intelligence index:

```bash
scip-python index --project-root /path/to/repo --output /path/to/repo/.codeatlas/index.scip \
  && codeatlas import-index /path/to/repo/.codeatlas/index.scip \
    --repo-path /path/to/repo --format scip

scip-typescript index --project-root /path/to/repo --output /path/to/repo/.codeatlas/index.scip \
  && codeatlas import-index /path/to/repo/.codeatlas/index.scip \
    --repo-path /path/to/repo --format scip
```

SCIP protobuf import is the precision layer: imported edges are tagged `resolution_tier=scip`
with high confidence and can upgrade duplicate fallback edges. The built-in parser still
creates local fallback edges tagged by provenance such as `exact_qualified`,
`import_scoped`, `same_module`, `unique_name`, or `unresolved`; these remain useful when no
external index is available.

The importer also accepts a simple generic JSON shape with `symbols` and `edges`, plus
SCIP-style JSON with `documents`, document-level `symbols`, relationships, and definition
occurrences.

## Local Architecture And Commit Map

Open a repository map in your browser:

```bash
codeatlas serve /path/to/repo
```

The command refreshes the code graph and repository memory, starts a local server, and opens a webpage by default. The browser UI is intentionally scoped to two first-class views:

- Briefing view: a first-time-reader guide that uses README/docs/package metadata when available, but falls back to code-inferred purpose and start-here anchors when a repo has no docs. It turns the index into a start-here path, readable architecture chapters (API, services, scheduler/orchestration, data/model, integrations, tests, docs/config), API/request, startup/config, data/model, test, and git/change flows, glossary-like concepts, and an agent-ready orientation brief. Each item cites repo-owned prose, indexed files, symbols, routes, component edges, or commits.
- Architecture view: major components, internal imports/calls, external modules/services, service-like nodes, and git co-change links.

The CLI and MCP surfaces remain the primary interface for deeper workflows such as impact review, context packs, verification plans, rule checks, history, ownership, and external-index import. Use the left filter rail to hide/show components, common library nodes, and connection types. For terminal-only workflows:

```bash
codeatlas serve /path/to/repo --no-open --port 8765
```

The right rail exposes product workflows for "First-time brief", "Where start?", recent changes, risky code, API/data flow, routes, dead code, owners, context packs, rule checks, source outlines, and verification plans. Workflow results render as compact evidence cards so the graph stays a navigation layer instead of the only source of truth. Selecting a node, edge, saved path, or briefing item centers the camera on that evidence. Right-click classification changes show an Undo toast before the config edit becomes old news. Dense maps use level-of-detail rendering: noisy import/reference/dependency edges are bundled with x-count labels while exact edges remain available from the bundle detail panel.

The Briefing start screen includes a New Engineer Dashboard with four first-read blocks: "Read these first," "Understand these flows," "Avoid this noise," and "High-risk areas." Briefing cards expose explicit "Why?", "Evidence", and "Open files" sections. Flow cards also include Play flow, which switches to the architecture map, keeps the flow nodes visible through large-repo filters, animates the current edge/node, and syncs each step with evidence in the details panel. File buttons copy the exact path/line and jump back to the owning architecture component when the map can match it.

Workflow results include export buttons for JSON and rendered text/Markdown. Slow workflows are cached under `.codeatlas/cache/workflows/`; the cache is invalidated when the index DB mtime or `.codeatlas.yml` fingerprint changes.

Generate the same first-time-reader briefing in the terminal:

```bash
codeatlas briefing /path/to/repo
codeatlas briefing /path/to/repo --json
```

## Canonical Static Flow Traces

Trace one indexed route through persisted directed relationships:

```bash
codeatlas trace-flow /path/to/repo --entrypoint "POST /orders"
codeatlas trace-flow /path/to/repo --entrypoint "POST /orders" --json
```

The local visualization server exposes the same canonical fields at `POST /api/flow-trace`:

```json
{
  "entrypoint": "POST /orders",
  "max_hops": 12
}
```

`max_hops` is bounded consistently across Python, CLI, API, MCP, and UI callers:
`1 <= max_hops <= 64`.

Version 1 follows only `ROUTE -> HANDLES -> CALLS -> HTTP_CALLS`. Every returned link maps
to a persisted graph edge and carries its source line, arguments, confidence, resolution tier,
display name, endpoint keys, file paths, signatures, and HTTP target metadata. Unresolved calls
become explicit trace steps and gaps; traversal never substitutes a convenient component or
walks a call edge backward.

Aggregated links expose an `occurrences` array containing each persisted occurrence's source line,
arguments, and display name. The existing `source_line`, `source_lines`, and `arguments` fields
remain for compatibility; `arguments` represents the first occurrence, while `occurrences` is the
authoritative per-occurrence evidence.

These are evidence-backed static traces, not verified runtime execution paths. The payload marks
`trace_kind`, `ordering_basis`, `complete`, `gaps`, and `warnings` explicitly. Branches are
retained in `links`, while `primary_path` is only a deterministic sink-reaching graph path.
Briefing playback requests this canonical payload when it has an exact route entrypoint and labels
the older inferred reading path when a canonical trace is unavailable.

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
4. Reads only selected DB-cached snippets, with live disk reads only as a compatibility fallback.
5. Stops at the approximate token budget.
6. Prints baseline, optimized, and savings estimates.

Token estimation uses:

```text
1 token ~= 4 characters
```

The baseline is calculated from the full indexed files that contain the returned snippets. The optimized count is calculated from the snippets actually returned.

## Graph Design

Node types:

- `FILE`
- `MODULE`
- `CLASS`
- `FUNCTION`
- `METHOD`
- `SYMBOL`
- `ROUTE`

Edge types:

- `CONTAINS`
- `IMPORTS`
- `CALLS`
- `HANDLES`
- `HTTP_CALLS`
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
codeatlas doctor /path/to/repo
codeatlas trace-flow /path/to/repo --entrypoint "POST /orders"
codeatlas query "callers:symbol" --repo-path /path/to/repo
codeatlas dead-code /path/to/repo
codeatlas routes /path/to/repo
codeatlas http-confidence /path/to/repo
codeatlas install-agent /path/to/repo --agent all
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
codeatlas mcp --repo-path /path/to/repo --profile agent
```

## MCP Integration

Run the reduced agent-facing surface:

```bash
codeatlas mcp --repo-path /path/to/repo --profile agent
```

Exposed tools:

- `get_code_context(query, max_tokens, depth)`
- `get_context_pack(task, max_tokens, output_format)`
- `get_flow_trace(entrypoint, max_hops)`
- `query_code_graph(expression, limit)`
- `get_index_status()`
- `get_verification_plan(base_ref, task)`
- `run_rules(limit, severity)`
- `get_source_outline(query, limit)`
- `repository_stats()`

Every MCP tool response carries agent staleness fields:

- `index_age_seconds`
- `dirty_files_count`
- `index_stale`

Tools that naturally return a list use an envelope: `{ "items": [...], ...staleness }`.
This lets agents decide whether to trust context, warn, or trigger `codeatlas index`.
`get_code_context` also reports `warm_retrieval_ms`, `warm_retrieval_budget_ms`, and `warm_retrieval_status` so agents can fall back to targeted grep when retrieval is slow.

Use `--profile full` for the legacy broad tool set while debugging or migrating older agent prompts. The MCP adapter uses FastMCP when installed. The underlying tool handlers are plain Python functions, which keeps them easy to test and reuse.

Install prompt-side guidance:

```bash
codeatlas install-agent /path/to/repo --agent codex
codeatlas install-agent /path/to/repo --agent claude
codeatlas install-agent /path/to/repo --agent all
```

The Codex installer writes `.codex/mcp.json` and a CodeAtlas section in `.codex/AGENTS.md`. The Claude installer writes a CodeAtlas section in `CLAUDE.md` plus `.claude/skills/codeatlas/SKILL.md`. Both tell the agent when to call CodeAtlas instead of grep, how to handle stale indexes, and to treat warm retrieval over roughly 1 second as a reason to narrow the lookup.

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

## Retrieval Evals

Run labeled retrieval expectations:

```bash
codeatlas eval-retrieval evals/retrieval/default.json --repo-id codeatlas-self
```

The default manifest contains a CodeAtlas self-eval with 50+ hand-labeled queries and expected files/symbols. It also declares optional pinned OSS suites for Requests, Click, and Rich under `.codeatlas/eval-repos/`; check those repositories out at the manifest refs and omit `--repo-id` to score every available suite. The evaluator indexes each repo, retrieves top-k snippets, and asserts recall@k instead of reporting only latency.

## Implementation Plan

The implementation is organized in layers:

1. Storage layer: durable SQLite schema and graph operations.
2. Parser layer: Tree-sitter plugin interface for Python, JavaScript, and TypeScript/TSX.
3. Indexer: scan, parse, persist, and update graph sections.
4. Retrieval engine: lookup, traversal, ranking, snippets, token reporting.
5. Memory engine: git/document evidence extraction, memory entities, relationships, and history queries.
6. Context compression: architecture, history, decisions, ownership, dependencies, files, and related changes.
7. CLI and MCP: local user and assistant interfaces.
8. Benchmarks and tests: measured reports and regression coverage.

## Limitations

- JavaScript and TypeScript parsing is Tree-sitter-backed, but cross-file reference resolution is still intentionally conservative and not type-aware.
- Incremental semantic fallback is sound only for the static symbols and uses extracted by the built-in parsers. Dynamic `import()`/non-literal `require()`, Python import hooks, reflection, monkey-patching, computed calls, runtime dependency injection, and configuration-selected targets are not modeled.
- Python named static import chains receive limited re-export resolution. JavaScript/TypeScript
  named, default, star, and conditional re-exports and complete barrel-file semantics are not
  supported.
- Built-in package resolution does not implement `package.json` `exports`, Node directory/index and extension precedence, workspace/package-manager linking, `tsconfig` path aliases, bundler aliases, Python namespace-package behavior, or custom module loaders. Use SCIP/external-index evidence when those rules determine symbol identity.
- Pyright/BasedPyright integration is currently an optional diagnostics hook; deeper type-aware reference resolution is a future extension.
- Retrieval prefers DB-cached snippets and falls back to disk only for legacy or partial indexes.
- Built-in call resolution is name-based, but its edges distinguish exact-qualified, import-scoped, same-module, unique-name, and unresolved matches. Ambiguous names remain unresolved. SCIP imports are the preferred precision tier when a language-specific indexer is available.
- Token counts are estimates, not tokenizer-specific counts.
- PR review comments and approvals are not fetched from GitHub yet; PullRequest entities are currently inferred from commit messages.
- Commit intelligence uses deterministic local heuristics, not an LLM. It cites evidence but should be treated as an initial signal.
- Canonical flow traces currently cover static `HANDLES`, `CALLS`, and `HTTP_CALLS` edges only. They do not prove runtime order across conditionals, callbacks, dependency injection, dynamic dispatch, reflection, or configuration.
- Impact radius is conservative and based on local git diff, file names, ownership, co-change history, and indexed evidence. It is not a substitute for test execution.
- Built-in rule checks are review aids, not a full security scanner or CodeQL/Semgrep replacement.
- External index import supports generic JSON, SCIP-style JSON, and binary SCIP protobuf indexes.
- If a source file that owns imported SCIP/external symbols changes content, re-import the external
  index after incremental indexing to restore that file-owned precise evidence. Semantic-only
  re-resolution of unchanged files preserves precise edges.

## Roadmap

- Deepen JavaScript and TypeScript semantic import with optional LSP/external-index evidence.
- Add Go and Java parser plugins.
- Deepen Pyright/BasedPyright diagnostics integration without replacing SCIP as the precision edge layer.
- Add richer SCIP/external-index validation and generator-specific diagnostics.
- Add configurable rule packs and project-owned suppressions.
- Render issue/PR context directly into context packs when GitHub/GitLab ingestion is enabled.
- Add graph export formats such as GraphML and JSON.
- Expand retrieval eval labels across more pinned repositories and task-shaped queries.
- Replace headline token-savings claims with labeled-eval metrics that compare files an agent would open with plain grep versus CodeAtlas.
- Add package-aware import resolution for monorepos.
- Add GitHub/GitLab PR ingestion for review comments, approvals, and requested changes.
- Add exact architecture evolution diffing between graph snapshots.
- Extend canonical traces with database, event, queue, async, configuration, and runtime-observed evidence after the static contract is calibrated.
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

This requires Playwright and a running CodeAtlas server. Restart the server after frontend asset edits so stale browser/server UI warnings clear. The smoke wrapper sets `CODEATLAS_UI_URL`; `--screenshot-dir` keeps map and command-palette screenshots from the visual smoke. It verifies that the graph canvas, build badge, edge contrast and bundling controls, fit/undo controls, workflow buttons, workflow result cards, export buttons, and visual-regression surfaces render.
