# CodeAtlas

CodeAtlas is a local-first code intelligence platform for AI coding assistants. It indexes a repository once, builds a graph of files, modules, classes, functions, methods, imports, calls, inheritance, and references, then serves focused code context from the persisted index.

The goal is to reduce repeated repository reads, improve warm-start retrieval speed, and keep all analysis offline. CodeAtlas does not call OpenAI, Anthropic, or any cloud API.

## Status

This repository contains a working Python-first implementation:

- Tree-sitter parser plugin for Python
- SQLite graph store under `.codeatlas/index.db`
- Incremental indexing by file hash
- Graph-aware context retrieval with token estimates
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
  -> Retrieval engine
  -> CLI + MCP server
  -> Claude Code / Codex
```

The core design keeps expensive work in the indexing phase. Retrieval uses the persisted SQLite index, graph traversal, and selected snippet reads. It does not re-scan or re-parse the full repository for normal queries.

## Project Structure

```text
src/codeatlas/
  cli.py              # codeatlas command surface
  indexer.py          # repository indexing and incremental updates
  retrieval.py        # context ranking, token reports, dependency explanation
  storage.py          # SQLite schema and graph persistence
  scanner.py          # source file discovery and ignore rules
  graph.py            # graph neighborhood helpers
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
codeatlas context "query" --repo-path /path/to/repo
codeatlas graph "SymbolName" --repo-path /path/to/repo
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
5. CLI and MCP: local user and assistant interfaces.
6. Benchmarks and tests: measured reports and regression coverage.

## Limitations

- Python is the only implemented parser plugin today.
- Pyright/BasedPyright integration is currently an optional diagnostics hook; deeper type-aware reference resolution is a future extension.
- Retrieval reads selected snippets from disk. It does not re-parse the repository, but it expects files to remain present after indexing.
- Call resolution is name-based and prefers same-module matches before shortest qualified names.
- Token counts are estimates, not tokenizer-specific counts.

## Roadmap

- Add JavaScript and TypeScript parser plugins.
- Add Go and Java parser plugins.
- Deepen Pyright/BasedPyright semantic integration for exact cross-file references.
- Store optional snippet cache for fully DB-backed retrieval.
- Add graph export formats such as GraphML and JSON.
- Add richer benchmark suites with labeled expected contexts.
- Add package-aware import resolution for monorepos.

## Development

Run tests without installing Pytest:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Run with Pytest after installing dev dependencies:

```bash
pytest
```
