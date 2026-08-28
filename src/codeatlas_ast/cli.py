from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .core import build_index, load_index, retrieve


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="codeatlas-ast", description="AST-only Python repository indexing and retrieval")
    commands = root.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Build the AST index")
    index.add_argument("repo", type=Path)
    index.add_argument("--output", type=Path)
    index.add_argument("--json", action="store_true")
    stats = commands.add_parser("stats", help="Show index statistics")
    stats.add_argument("repo", type=Path)
    stats.add_argument("--json", action="store_true")
    context = commands.add_parser("retrieve", help="Retrieve AST-bounded code")
    context.add_argument("repo", type=Path)
    context.add_argument("query")
    context.add_argument("--max-tokens", type=int, default=2000)
    context.add_argument("--depth", type=int, default=0, help="Dependency expansion depth")
    context.add_argument("--json", action="store_true")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "index":
            payload = build_index(args.repo, args.output)
            print(json.dumps(payload, indent=2, sort_keys=True) if args.json else _mapping("AST index created", payload))
            return 0 if not payload["errors"] else 2
        if args.command == "stats":
            data = load_index(args.repo)
            payload = {
                "repository_root": data.repository_root,
                "generated_at": data.generated_at,
                "files": len(data.files),
                "symbols": len(data.symbols),
                "calls": len(data.calls),
                "imports": len(data.imports),
                "inheritance": len(data.inheritance),
                "errors": list(data.errors),
            }
            print(json.dumps(payload, indent=2, sort_keys=True) if args.json else _mapping("AST index statistics", payload))
            return 0
        result = retrieve(args.repo, args.query, max_tokens=args.max_tokens, depth=args.depth)
        payload = result.to_dict()
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else _render(payload))
        return 0
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _mapping(title: str, payload: dict[str, object]) -> str:
    width = max(map(len, payload), default=0)
    return "\n".join([title, *(f"{key:<{width}} : {value}" for key, value in payload.items())])


def _render(payload: dict[str, object]) -> str:
    lines: list[str] = []
    snippets = payload["snippets"]
    assert isinstance(snippets, list)
    for number, snippet in enumerate(snippets, start=1):
        assert isinstance(snippet, dict)
        lines += [
            f"\n[{number}] {snippet['file']}:{snippet['line_start']}-{snippet['line_end']}",
            f"    {snippet['qualified_name']} [{snippet['kind']}] score={snippet['score']:.2f}",
            f"    {snippet['reason']}",
            "-" * 72,
            str(snippet["code"]),
        ]
    lines.append(_mapping("\nRetrieval summary", {
        "baseline_tokens": payload["baseline_tokens"],
        "context_tokens": payload["context_tokens"],
        "savings_percent": round(float(payload["savings_percent"]), 2),
        "latency_ms": round(float(payload["latency_ms"]), 2),
    }))
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
