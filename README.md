# CodeAtlas AST

This is the **AST-only** branch of CodeAtlas.

It contains only:

- Python AST parsing;
- class, function, method, decorator, docstring, import, call, inheritance, and containment extraction;
- a local JSON AST index;
- AST-bounded retrieval under a token budget;
- optional dependency-neighbor expansion;
- tests and a small command-line interface.

It does **not** contain the old visualization server, HTML/JavaScript assets, repository-memory layer, rules engine, MCP server, ownership/history features, or agent installer.

## Requirements

- Python 3.11+
- Git

## Fresh clone of only this branch

```bash
git clone --branch research/ast-only-clean --single-branch \
  https://github.com/mayankdhingra02/CodeAtlas.git CodeAtlas-AST
cd CodeAtlas-AST
```

## Use it from an existing CodeAtlas clone

```bash
cd /path/to/CodeAtlas
git fetch origin
git switch --track origin/research/ast-only-clean
```

If that local tracking branch already exists:

```bash
git switch research/ast-only-clean
git pull --ff-only origin research/ast-only-clean
```

## Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Verify it:

```bash
codeatlas-ast --help
python -m unittest discover -s tests -v
```

## Run it on a local Python repository

```bash
TARGET_REPO=/absolute/path/to/your/python/repository
```

Build the AST index:

```bash
codeatlas-ast index "$TARGET_REPO"
```

This creates:

```text
$TARGET_REPO/.codeatlas-ast/index.json
```

Inspect the index:

```bash
codeatlas-ast stats "$TARGET_REPO"
```

Retrieve relevant AST symbols:

```bash
codeatlas-ast retrieve "$TARGET_REPO" \
  "Where is authentication token validation implemented?" \
  --max-tokens 2000
```

Include one dependency-neighbor hop:

```bash
codeatlas-ast retrieve "$TARGET_REPO" \
  "How is an order created and persisted?" \
  --depth 1 \
  --max-tokens 2000
```

JSON output:

```bash
codeatlas-ast retrieve "$TARGET_REPO" \
  "How is an order created and persisted?" \
  --depth 1 \
  --max-tokens 2000 \
  --json > retrieval.json
```

Re-run `codeatlas-ast index "$TARGET_REPO"` whenever the target repository changes.
