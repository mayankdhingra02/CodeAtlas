from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .models import estimate_tokens
from .memory import MemoryQueryEngine
from .retrieval import RetrievalEngine
from .rules import run_rule_checks
from .source import source_outline
from .status import index_status
from .verification import verification_plan


def context_pack(
    repo_path: str | Path,
    task: str,
    *,
    max_tokens: int = 6000,
) -> dict[str, Any]:
    query = task.strip()
    if not query:
        raise ValueError("Task cannot be empty.")
    retrieval = RetrievalEngine()
    memory = MemoryQueryEngine()
    code_result = retrieval.retrieve(repo_path, query, depth=2, max_tokens=max_tokens)
    snippets = [
        {
            "file_path": snippet.file_path,
            "symbol": snippet.qualified_name,
            "kind": snippet.kind,
            "lines": f"{snippet.line_start}-{snippet.line_end}",
            "reason": snippet.reason,
            "code": redact_text(snippet.code),
        }
        for snippet in code_result.snippets[:10]
    ]
    try:
        memory_context = memory.compressed_context(repo_path, query, max_tokens=max_tokens)
        evidence = [asdict(ref) for ref in memory_context.evidence[:10]]
        ownership = [asdict(entry) for entry in memory_context.ownership[:5]]
    except Exception:
        evidence = []
        ownership = []

    files = list(dict.fromkeys(snippet["file_path"] for snippet in snippets if snippet["file_path"]))
    pack = {
        "task": query,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": index_status(repo_path),
        "recommended_files": files[:10],
        "snippets": snippets,
        "evidence": evidence,
        "ownership": ownership,
        "verification": verification_plan(repo_path, task=query),
        "rule_findings": run_rule_checks(repo_path, limit=20)["findings"],
        "source_outline": source_outline(repo_path, query, limit=8),
        "token_report": code_result.token_report.__dict__
        | {"savings_percent": code_result.token_report.savings_percent},
    }
    pack["estimated_tokens"] = estimate_tokens(json.dumps(pack, sort_keys=True, default=str))
    return pack


def render_context_pack(pack: dict[str, Any], *, output_format: str = "markdown") -> str:
    normalized = output_format.lower()
    if normalized == "json":
        return json.dumps(pack, indent=2, sort_keys=True, default=str)
    if normalized == "xml":
        return render_context_pack_xml(pack)
    if normalized not in {"markdown", "md"}:
        raise ValueError("Context pack format must be markdown, json, or xml.")
    return render_context_pack_markdown(pack)


def render_context_pack_markdown(pack: dict[str, Any]) -> str:
    lines = [
        "# CodeAtlas Context Pack",
        "",
        f"Task: {pack['task']}",
        f"Generated: {pack['generated_at']}",
        f"Estimated tokens: {pack.get('estimated_tokens', 0):,}",
        "",
        "## Recommended Files",
    ]
    files = pack.get("recommended_files", [])
    if files:
        lines.extend(f"- `{file_path}`" for file_path in files)
    else:
        lines.append("- No specific files found.")

    lines.extend(["", "## Snippets"])
    for snippet in pack.get("snippets", []):
        lines.extend(
            [
                f"### `{snippet['file_path']}:{snippet['lines']}`",
                f"Why: {snippet['reason']}",
                "```",
                snippet["code"],
                "```",
            ]
        )

    lines.extend(["", "## Verification"])
    for command in pack.get("verification", {}).get("commands", []):
        lines.append(f"- `{command['command']}` - {command['reason']}")

    lines.extend(["", "## Rule Findings"])
    findings = pack.get("rule_findings", [])
    if findings:
        for finding in findings[:10]:
            lines.append(
                f"- {finding['severity']}: {finding['title']} at "
                f"`{finding['file_path']}:{finding['line']}`"
            )
    else:
        lines.append("- No built-in rule findings in the recommended context.")

    lines.extend(["", "## Evidence"])
    evidence = pack.get("evidence", [])
    if evidence:
        for item in evidence[:8]:
            title = item.get("title") or item.get("source_id") or "Evidence"
            location = item.get("path") or item.get("source_id") or ""
            lines.append(f"- {title} ({location})")
    else:
        lines.append("- No repository memory evidence found.")

    return "\n".join(lines).rstrip() + "\n"


def render_context_pack_xml(pack: dict[str, Any]) -> str:
    lines = [
        "<codeatlas_context_pack>",
        f"  <task>{escape(str(pack['task']))}</task>",
        f"  <generated_at>{escape(str(pack['generated_at']))}</generated_at>",
        "  <recommended_files>",
    ]
    for file_path in pack.get("recommended_files", []):
        lines.append(f"    <file>{escape(str(file_path))}</file>")
    lines.append("  </recommended_files>")
    lines.append("  <snippets>")
    for snippet in pack.get("snippets", []):
        lines.extend(
            [
                "    <snippet>",
                f"      <file>{escape(str(snippet['file_path']))}</file>",
                f"      <lines>{escape(str(snippet['lines']))}</lines>",
                f"      <reason>{escape(str(snippet['reason']))}</reason>",
                f"      <code>{escape(str(snippet['code']))}</code>",
                "    </snippet>",
            ]
        )
    lines.append("  </snippets>")
    lines.append("</codeatlas_context_pack>")
    return "\n".join(lines) + "\n"


def redact_text(text: str) -> str:
    patterns = (
        r"(?i)([A-Za-z0-9_]*(?:api[_-]?key|secret|token|password)[A-Za-z0-9_]*)(\b\s*[:=]\s*['\"])[^'\"]+(['\"])",
        r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._~+/=-]+",
    )
    redacted = text
    for pattern in patterns:
        redacted = re.sub(pattern, _redaction_replacement, redacted)
    return redacted


def _redaction_replacement(match: re.Match[str]) -> str:
    if len(match.groups()) == 3:
        return f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(3)}"
    return f"{match.group(1)}[REDACTED]"
