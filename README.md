# CodeAtlas

CodeAtlas is a local-first repository intelligence platform for AI coding assistants. It indexes a repository once, builds a graph of files, modules, classes, functions, methods, imports, calls, inheritance, and references, then layers repository memory on top of git history and documentation.

The goal is to reduce repeated repository reads, improve warm-start retrieval speed, and explain not only what code exists, but why the repository evolved the way it did. CodeAtlas does not call OpenAI, Anthropic, or any cloud API.

## Status

This repository contains a working Python-first implementation:

- Tree-sitter parser plugin for Python
- SQLite graph store under `.codeatlas/index.db`
- Incremental indexing by file hash
- Graph-aware context retrieval with token estimates
- Repository Memory Engine for git history, README/docs, ADRs, RFCs, release notes, and design docs
- Commit intelligence heuristics for purpose, motivation, impacted components, risk, and architectural impact
- Repository Time Machine, ownership intelligence, decision lookup, architecture findings, and compressed repository context
- Local browser visualization for 2D/3D architecture and commit-history maps
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
  parsers/
    base.py
    python.py         # Tree-sitter Python extractor
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

## Local Architecture And Commit Map

Open a repository map in your browser:

```bash
codeatlas serve /path/to/repo
```

The command refreshes the code graph and repository memory, starts a local server, and opens a webpage by default. The map has three views:

- Architecture view: major components, internal imports/calls, external modules/services, service-like nodes, and git co-change links.
- Commit view: developers, commits, and the components each commit touched.
- Compare view: two git refs or commits are archived into temporary snapshots, indexed side by side, and annotated with red nodes/edges for added, removed, or changed architecture.

Use the 2D/3D toggle to switch between a flat force map and a lightweight depth view. Use the left filter rail to hide/show components, common library nodes, and compare specific commit refs. For terminal-only workflows:

```bash
codeatlas serve /path/to/repo --no-open --port 8765
```

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

- Python is the only implemented parser plugin today.
- Pyright/BasedPyright integration is currently an optional diagnostics hook; deeper type-aware reference resolution is a future extension.
- Retrieval reads selected snippets from disk. It does not re-parse the repository, but it expects files to remain present after indexing.
- Call resolution is name-based and prefers same-module matches before shortest qualified names.
- Token counts are estimates, not tokenizer-specific counts.
- PR review comments and approvals are not fetched from GitHub yet; PullRequest entities are currently inferred from commit messages.
- Commit intelligence uses deterministic local heuristics, not an LLM. It cites evidence but should be treated as an initial signal.
- API-flow and infrastructure intelligence have conservative MCP surfaces, but deep endpoint/runtime topology extraction is still roadmap work.
- Impact radius is conservative and based on local git diff, file names, ownership, co-change history, and indexed evidence. It is not a substitute for test execution.

## Roadmap

- Add JavaScript and TypeScript parser plugins.
- Add Go and Java parser plugins.
- Deepen Pyright/BasedPyright semantic integration for exact cross-file references.
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
