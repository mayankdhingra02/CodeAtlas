from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import resolve_repo_root


SECTION_START = "<!-- CODEATLAS:START -->"
SECTION_END = "<!-- CODEATLAS:END -->"

AGENT_GUIDANCE = """Use CodeAtlas before broad file exploration when a task depends on repository structure.

Prefer CodeAtlas over grep when:
- you need a first-pass map of unfamiliar code, ownership, routes, dependencies, or dead code
- you need cross-file callers/callees, imports, references, or graph neighbors
- you are preparing an agent context pack for a coding task
- you need evidence-backed snippets with token counts and retrieval timings
- you suspect grep would require opening many files

Use grep/ripgrep first when:
- you need one exact string, literal error text, or a known filename
- you are already inside the right file and only need local edits
- CodeAtlas reports the index is stale and a refresh would be slower than the task

Useful CLI commands:
- `codeatlas index-status .`
- `codeatlas index . --incremental`
- `codeatlas context "<task or symbol>" --max-tokens 8000`
- `codeatlas agent-context "<task>"`
- `codeatlas context-pack "<task>"`
- `codeatlas query callers:<symbol>`
- `codeatlas query calls:<symbol>`
- `codeatlas query imports:<package>`
- `codeatlas query route:/path`
- `codeatlas dead-code .`
- `codeatlas verify-plan . --task "<task>"`
- `codeatlas rules .`

If the MCP server is available, start with `get_index_status`, then use `get_code_context`, `get_context_pack`, `query_code_graph`, `get_verification_plan`, `run_rules`, or `get_source_outline`. Treat `index_stale: true` as a warning to run `codeatlas index . --incremental` before trusting graph answers.

Latency rule: warm retrieval should normally stay under about 1 second. If `warm_retrieval_status` is `slow` or CLI output shows warm retrieval over 1,000ms, fall back to targeted grep for very small lookups and report the slowness.
"""

CODEX_INSTRUCTIONS = f"""# CodeAtlas

{AGENT_GUIDANCE}
"""

CLAUDE_INSTRUCTIONS = f"""## CodeAtlas

{AGENT_GUIDANCE}
"""

CLAUDE_SKILL = f"""---
name: codeatlas
description: Use CodeAtlas for repository maps, context packs, graph queries, stale-index checks, and evidence-backed snippets before broad grep.
---

# CodeAtlas

{AGENT_GUIDANCE}
"""


def install_agent(repo_path: str | Path, agent: str = "codex") -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    normalized = agent.lower().strip()
    if normalized in {"all", "both"}:
        targets = {"codex", "claude"}
    elif normalized in {"codex", "claude"}:
        targets = {normalized}
    else:
        raise ValueError("Unsupported agent. Use 'codex', 'claude', or 'all'.")

    payload: dict[str, Any] = {
        "agent": normalized,
        "repo_root": str(repo_root),
    }
    if "codex" in targets:
        payload |= _install_codex(repo_root)
    if "claude" in targets:
        payload |= _install_claude(repo_root)
    return payload


def _install_codex(repo_root: Path) -> dict[str, Any]:
    codex_dir = repo_root / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    mcp_path = codex_dir / "mcp.json"
    instructions_path = codex_dir / "AGENTS.md"
    mcp_payload = {
        "mcpServers": {
            "codeatlas": {
                "command": "codeatlas",
                "args": ["mcp", "--repo-path", str(repo_root), "--profile", "agent"],
            }
        }
    }
    mcp_path.write_text(json.dumps(mcp_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _upsert_section(instructions_path, CODEX_INSTRUCTIONS)
    return {
        "mcp_config": str(mcp_path),
        "instructions": str(instructions_path),
        "codex_instructions": str(instructions_path),
    }


def _install_claude(repo_root: Path) -> dict[str, Any]:
    instructions_path = repo_root / "CLAUDE.md"
    skill_dir = repo_root / ".claude" / "skills" / "codeatlas"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    _upsert_section(instructions_path, CLAUDE_INSTRUCTIONS)
    skill_path.write_text(CLAUDE_SKILL, encoding="utf-8")
    return {
        "claude_instructions": str(instructions_path),
        "claude_skill": str(skill_path),
    }


def _upsert_section(path: Path, body: str) -> None:
    section = f"{SECTION_START}\n{body.rstrip()}\n{SECTION_END}\n"
    if not path.exists():
        path.write_text(section, encoding="utf-8")
        return
    existing = path.read_text(encoding="utf-8")
    start = existing.find(SECTION_START)
    end = existing.find(SECTION_END)
    if start != -1 and end != -1 and end > start:
        end += len(SECTION_END)
        updated = existing[:start].rstrip() + "\n\n" + section + existing[end:].lstrip()
    else:
        updated = existing.rstrip() + "\n\n" + section
    path.write_text(updated, encoding="utf-8")
