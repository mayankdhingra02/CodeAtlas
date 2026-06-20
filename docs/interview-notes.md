# CodeAtlas Interview Notes

## What Problem Does CodeAtlas Solve?

AI coding assistants often spend a lot of tokens repeatedly reading broad parts of a repository to understand how code fits together. CodeAtlas precomputes repository structure and dependencies once, stores that knowledge locally, and returns only the most relevant snippets for a query.

The newer direction is repository intelligence: CodeAtlas also mines git history and documentation so it can explain why code exists, how architecture changed, and which evidence supports an answer. This reduces context size, improves warm retrieval speed, and avoids cloud APIs.

## Concise Project Story

I built CodeAtlas because I was using AI coding assistants on personal and academic projects and noticed they were spending a large number of tokens repeatedly reading unrelated files. I wanted a local system that could precompute repository structure, dependencies, and historical repository memory so the AI could retrieve only the most relevant code and reasoning context. In testing, the system significantly reduced context size and improved retrieval speed.

## What Is An AST?

An AST, or Abstract Syntax Tree, is a structured representation of source code. Instead of treating code as plain text, an AST represents language constructs such as imports, classes, functions, decorators, calls, and expressions as tree nodes.

For example, a Python function becomes a `function_definition` node with child nodes for its name, parameters, return type, and body.

## What Is Tree-sitter?

Tree-sitter is an incremental parsing library. It provides fast parsers for many programming languages and returns concrete syntax trees that tools can inspect. CodeAtlas uses Tree-sitter so symbol extraction is based on real syntax instead of regex matching.

That matters because code syntax can be nested and ambiguous. A parser knows the difference between a function definition, a function call, a string, and a comment.

## Why Use Semantic Analysis?

AST parsing tells us what the code looks like syntactically. Semantic analysis helps answer what names mean.

For example, an AST can tell us that code calls `charge(total)`. Semantic analysis can help determine which `charge` method is actually being called, what type owns it, and where it is defined.

The current implementation has an optional Pyright/BasedPyright diagnostics hook and a name-based resolver. A deeper next step would use Pyright data to resolve exact definitions and references across files.

## Why Use A Graph Model?

Code relationships are naturally graph-shaped:

- files contain modules
- modules contain classes and functions
- classes contain methods
- functions call other functions
- classes inherit from other classes
- files import modules

A graph model makes traversal easy. If a user asks about `create_order`, CodeAtlas can return that method, the class that contains it, the service it calls, and nearby related code without reading the whole repository.

The memory graph uses the same idea for repository history. Developers, commits, modules, features, architecture decisions, releases, incidents, and repository events are nodes. Relationships such as `introduced_by`, `modified_by`, `related_to`, and `contributes_to` connect them.

## What Is The Repository Memory Engine?

The Repository Memory Engine indexes evidence that explains why a codebase changed:

- git commits
- commit authors and timestamps
- changed files
- README and documentation files
- ADRs, RFCs, design docs, changelogs, and release notes

It stores memory entities and evidence in SQLite beside the existing code graph. The goal is to answer historical and reasoning questions with citations.

## What Is Commit Intelligence?

Commit intelligence turns raw commit data into structured signals:

- purpose of the change
- likely motivation
- impacted components
- risk level
- architectural impact
- related files and features

The current implementation uses deterministic local heuristics. It does not claim certainty; it stores confidence scores and cites the commit as evidence.

## How Does The Repository Time Machine Work?

For a topic like `auth`, CodeAtlas searches indexed memory evidence and orders matching commits and documents by time. The result is a timeline of repository events with evidence references.

This lets a developer ask questions like "How did authentication evolve?" and get a compact timeline rather than manually reading git logs, PRs, and docs.

## How Is Ownership Intelligence Calculated?

Ownership intelligence starts from evidence matching a topic. For each developer, CodeAtlas counts commits and touched files, then produces an expertise score. The score is simple by design: it is evidence-backed and explainable.

This can answer questions like "Who knows payments best?" or "Who introduced Redis?" without guessing.

## How Does CodeAtlas Avoid Hallucinating Decisions?

Decision answers are built from indexed evidence such as ADRs, design docs, or architecture-significant commits. If CodeAtlas cannot find evidence, it returns that no evidence-backed decision was found.

This is important because repository intelligence should be more like a memory and citation layer than a chatbot.

## What Is Context Compression?

Context compression combines multiple signals into one compact response:

- architecture findings
- timeline events
- design decisions
- ownership
- dependencies
- critical files
- related changes
- relevant code snippets

The output is meant for tools like Codex, Claude Code, Cursor, Windsurf, Continue, ChatGPT, Gemini, and local LLMs.

## What Is The Git Nexus Layer?

The Git Nexus layer connects source files through commit history. If two files are repeatedly changed in the same commit, CodeAtlas records a co-change relationship between them.

This is useful because dependency graphs only show static code relationships. Git history shows human workflow relationships: files that developers tend to modify together, components that churn often, and areas with many contributors.

## How Does Impact Review Work?

`codeatlas impact` compares the working tree against a git base ref, then enriches each changed file with:

- historical owners
- co-change neighbors
- matching commit/document evidence
- component risk signals
- token savings from compressed impact context

This is inspired by blast-radius review workflows, but CodeAtlas keeps the result evidence-backed and local.

## How Does The Visualization Work?

`codeatlas serve` starts a local web server, refreshes the code graph and repository memory, and opens a browser map. The page is intentionally local-first: the graph JSON comes from `.codeatlas/index.db`, and the frontend uses a built-in canvas view instead of hosted visualization services.

The architecture tab groups files into major components, then connects them with static code edges such as imports/calls plus historical co-change links from git. The commit tab is different: it shows developers, commits, and the components each commit touched, so someone can understand how the repository evolved instead of only seeing current code dependencies.

The compare tab accepts two git refs or commit SHAs. CodeAtlas archives each ref into a temporary snapshot, indexes both snapshots without checking anything out in the working tree, then shows the two architecture graphs side by side. Red nodes and edges mark added, removed, or changed architecture.

The 2D/3D toggle changes the layout from a flat force map to a depth-based view. The left filter rail can hide common library/documentation nodes or focus on specific components. The visualization is meant as a fast orientation tool for unfamiliar repositories, not a replacement for exact dependency inspection.

## Why Not Only Embeddings?

Embeddings are useful for semantic similarity, but they do not fully capture exact code structure. They can miss precise relationships such as "this method calls that method" or "this class inherits from that base class."

CodeAtlas is local-first and graph-first. It can later add local embeddings, but the structural graph gives deterministic context that is explainable and does not require a cloud model.

## How Is Token Reduction Measured?

CodeAtlas estimates tokens with:

```text
1 token ~= 4 characters
```

For a query, it calculates:

- baseline tokens from indexed files in related directories
- optimized tokens from the snippets actually returned
- savings percentage from the difference

The system does not hardcode a savings percentage. It reports what was measured for the repository and query.

## How Is Latency Measured?

The retrieval engine separately tracks:

- symbol lookup time
- graph traversal time
- ranking and snippet assembly time
- total query time

The benchmark command also measures cold indexing time and warm query latency.

## How Does Incremental Indexing Work?

During indexing, CodeAtlas stores each file's SHA-256 hash in SQLite. On incremental runs, it scans source files, compares current hashes to stored hashes, and reparses only changed files.

For changed files, it removes the old graph section for that file and writes the new file, symbols, imports, nodes, and edges. Deleted files are removed from the graph.

## What Was The Hardest Challenge?

The hardest challenge is balancing precision and simplicity in cross-file resolution. Parsing syntax is straightforward with Tree-sitter, but determining the exact target of every call or reference requires semantic type information.

The current tradeoff is to build a reliable structural graph first, then leave a clear extension point for deeper Pyright/BasedPyright semantic resolution.

## What Tradeoffs Were Made?

- The first parser plugin supports Python only.
- Calls are resolved by symbol name, preferring same-module matches.
- Token counts use a simple character-based estimate.
- Retrieval reads selected snippets from disk instead of storing full source text in SQLite.
- MCP support is optional so the core CLI works without MCP dependencies installed.
- PR review comments are not ingested yet; PullRequest memory is inferred from commit messages.
- Commit intelligence uses heuristics instead of LLM summaries.
- API-flow and infrastructure intelligence are exposed conservatively but not deeply implemented yet.
- Co-change links are historical signals, not proof of runtime dependency.
- Impact review is a prioritization aid, not a replacement for tests.

These tradeoffs keep the MVP local, understandable, and functional while leaving room for deeper language support.

## What Improvements Would Be Added Next?

- Exact Pyright/BasedPyright definition and reference mapping.
- JavaScript, TypeScript, Go, and Java parser plugins.
- Package-aware import resolution.
- Local embeddings as an optional ranking signal.
- Snippet caching inside SQLite.
- Graph visualization exports.
- Richer visualization filters for API routes, queues, databases, and deployment services.
- Labeled retrieval benchmarks for accuracy scoring.
- GitHub/GitLab PR ingestion for reviews, approvals, and requested changes.
- Snapshot-based architecture evolution detection.
- Endpoint, event, and infrastructure parsers for runtime flow intelligence.
- Stronger confidence scoring and evidence ranking.
