# CodeAtlas Interview Notes

## What Problem Does CodeAtlas Solve?

AI coding assistants often spend a lot of tokens repeatedly reading broad parts of a repository to understand how code fits together. CodeAtlas precomputes repository structure and dependencies once, stores that knowledge locally, and returns only the most relevant snippets for a query.

This reduces context size, improves warm retrieval speed, and avoids cloud APIs.

## Concise Project Story

I built CodeAtlas because I was using AI coding assistants on personal and academic projects and noticed they were spending a large number of tokens repeatedly reading unrelated files. I wanted a local system that could precompute repository structure and dependencies so the AI could retrieve only the most relevant code context. In testing, the system significantly reduced context size and improved retrieval speed.

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

These tradeoffs keep the MVP local, understandable, and functional while leaving room for deeper language support.

## What Improvements Would Be Added Next?

- Exact Pyright/BasedPyright definition and reference mapping.
- JavaScript, TypeScript, Go, and Java parser plugins.
- Package-aware import resolution.
- Local embeddings as an optional ranking signal.
- Snippet caching inside SQLite.
- Graph visualization exports.
- Labeled retrieval benchmarks for accuracy scoring.
