from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import resolve_repo_root


CODEX_INSTRUCTIONS = """# CodeAtlas

Use CodeAtlas before broad file exploration when a task depends on repository structure.

Useful commands:
- `codeatlas index . --incremental`
- `codeatlas index-status .`
- `codeatlas query callers:<symbol>`
- `codeatlas query calls:<symbol>`
- `codeatlas dead-code .`
- `codeatlas agent-context "<task>"`
"""


def install_agent(repo_path: str | Path, agent: str = "codex") -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    normalized = agent.lower().strip()
    if normalized != "codex":
        raise ValueError("Only the Codex agent installer is available right now.")
    codex_dir = repo_root / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    mcp_path = codex_dir / "mcp.json"
    instructions_path = codex_dir / "AGENTS.md"
    mcp_payload = {
        "mcpServers": {
            "codeatlas": {
                "command": "codeatlas",
                "args": ["mcp", "--repo-path", str(repo_root)],
            }
        }
    }
    mcp_path.write_text(json.dumps(mcp_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if instructions_path.exists():
        existing = instructions_path.read_text(encoding="utf-8")
        if "Use CodeAtlas before broad file exploration" not in existing:
            instructions_path.write_text(existing.rstrip() + "\n\n" + CODEX_INSTRUCTIONS, encoding="utf-8")
    else:
        instructions_path.write_text(CODEX_INSTRUCTIONS, encoding="utf-8")
    return {
        "agent": "codex",
        "repo_root": str(repo_root),
        "mcp_config": str(mcp_path),
        "instructions": str(instructions_path),
    }
