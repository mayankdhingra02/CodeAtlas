from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import resolve_repo_root
from .project_config import load_project_config
from .scanner import iter_source_files


RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "possible-secret",
        "title": "Possible hard-coded secret",
        "severity": "high",
        "languages": {"python", "javascript"},
        "pattern": re.compile(
            r"(?i)\b(?P<name>[A-Za-z0-9_]*(?:api[_-]?key|secret|token|password)[A-Za-z0-9_]*)\b\s*[:=]\s*['\"](?P<value>[^'\"]{8,})['\"]"
        ),
        "message": "A credential-like value appears to be assigned in source code.",
        "recommendation": "Move the value to a secret manager or environment variable.",
    },
    {
        "id": "python-requests-without-timeout",
        "title": "requests call without timeout",
        "severity": "medium",
        "languages": {"python"},
        "pattern": re.compile(r"\brequests\.(get|post|put|patch|delete|request)\s*\("),
        "message": "HTTP client call does not show an explicit timeout on the same line.",
        "recommendation": "Pass timeout=... so callers do not hang indefinitely.",
    },
    {
        "id": "shell-true",
        "title": "subprocess shell=True",
        "severity": "high",
        "languages": {"python"},
        "pattern": re.compile(r"\bsubprocess\.[A-Za-z_]+\([^#\n]*shell\s*=\s*True"),
        "message": "shell=True can turn user-controlled strings into command execution.",
        "recommendation": "Prefer argument lists and keep shell=True behind strict input control.",
    },
    {
        "id": "dynamic-code-execution",
        "title": "Dynamic code execution",
        "severity": "high",
        "languages": {"python", "javascript"},
        "pattern": re.compile(r"\b(eval|exec)\s*\("),
        "message": "Dynamic code execution is difficult to reason about and audit.",
        "recommendation": "Replace with structured parsing or an explicit dispatch table when possible.",
    },
    {
        "id": "sql-string-interpolation",
        "title": "Interpolated SQL string",
        "severity": "high",
        "languages": {"python", "javascript"},
        "pattern": re.compile(r"\bexecute(?:Query)?\s*\(\s*(?:f['\"]|`|\w+\s*\+)"),
        "message": "SQL appears to be built with string interpolation or concatenation.",
        "recommendation": "Use parameterized queries through the database driver.",
    },
    {
        "id": "fetch-without-abort",
        "title": "fetch call without cancellation signal",
        "severity": "low",
        "languages": {"javascript"},
        "pattern": re.compile(r"\bfetch\s*\("),
        "message": "fetch call does not show a cancellation signal on the same line.",
        "recommendation": "Consider AbortController or a request wrapper with timeouts.",
    },
)


def run_rule_checks(
    repo_path: str | Path,
    *,
    limit: int = 100,
    severity: str | None = None,
) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    config = load_project_config(repo_root)
    if not config.rules.enabled:
        return _rule_payload([], {"high": 0, "medium": 0, "low": 0}, disabled=True)
    severity_filter = severity.lower().strip() if severity else ""
    findings: list[dict[str, Any]] = []
    counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for source_file in iter_source_files(repo_root):
        try:
            lines = source_file.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue
            for rule in RULES:
                if source_file.language not in rule["languages"]:
                    continue
                if not rule["pattern"].search(line):
                    continue
                if rule["id"] == "possible-secret" and should_ignore_possible_secret(
                    stripped,
                    source_file.relative_path,
                    rule["pattern"],
                ):
                    continue
                if rule["id"] == "python-requests-without-timeout" and "timeout=" in line:
                    continue
                if rule["id"] == "fetch-without-abort" and ("signal:" in line or "AbortController" in line):
                    continue
                if is_suppressed(rule["id"], source_file.relative_path, config.rules.suppressions):
                    continue
                final_severity = configured_severity(
                    rule["id"],
                    str(rule["severity"]),
                    source_file.relative_path,
                    config.rules.severity_overrides,
                    tests_lower=config.rules.tests_lower_severity,
                )
                if severity_filter and final_severity != severity_filter:
                    continue
                finding = {
                    "rule_id": rule["id"],
                    "title": rule["title"],
                    "severity": final_severity,
                    "file_path": source_file.relative_path,
                    "line": line_number,
                    "snippet": redact_line(stripped),
                    "message": rule["message"],
                    "recommendation": rule["recommendation"],
                    "confidence": 0.74 if final_severity == "low" else 0.82,
                }
                findings.append(finding)
                counts[final_severity] = counts.get(final_severity, 0) + 1
                if len(findings) >= limit:
                    return _rule_payload(findings, counts, config=config)
    return _rule_payload(findings, counts, config=config)


def redact_line(line: str) -> str:
    return re.sub(
        r"(?i)([A-Za-z0-9_]*(?:api[_-]?key|secret|token|password)[A-Za-z0-9_]*)(\b\s*[:=]\s*['\"])[^'\"]+(['\"])",
        r"\1\2[REDACTED]\3",
        line,
    )


def should_ignore_possible_secret(line: str, file_path: str, pattern: re.Pattern[str]) -> bool:
    match = pattern.search(line)
    if not match:
        return False
    name = match.groupdict().get("name", "").lower()
    value = match.groupdict().get("value", "").strip()
    value_lower = value.lower()
    if re.fullmatch(r"[A-Z][A-Z0-9_]{7,}", value):
        return True
    if value_lower in {
        "change_password",
        "updating_password",
        "admin_password",
        "fake_password",
        "fake_token",
        "test_password",
    }:
        return True
    if value_lower in name and not any(marker in name for marker in ("api", "secret", "token")):
        return True
    if is_test_path(file_path) and any(
        marker in value_lower for marker in ("fake", "dummy", "test", "password", "token", "secret")
    ):
        return True
    return False


def is_test_path(file_path: str) -> bool:
    lower = file_path.lower()
    name = Path(lower).name
    return "/test/" in lower or "/tests/" in lower or name.startswith("test_") or ".test." in name


def is_suppressed(rule_id: str, file_path: str, suppressions: tuple[dict[str, str], ...]) -> bool:
    for suppression in suppressions:
        rule = suppression.get("rule", "*")
        path = suppression.get("path", "*")
        if rule not in {"*", rule_id}:
            continue
        if fnmatch_path(file_path, path):
            return True
    return False


def configured_severity(
    rule_id: str,
    default: str,
    file_path: str,
    overrides: dict[str, str],
    *,
    tests_lower: bool,
) -> str:
    severity = overrides.get(rule_id, default).lower()
    if tests_lower and is_test_path(file_path):
        severity = lower_severity(severity)
    return severity if severity in {"high", "medium", "low"} else default


def lower_severity(severity: str) -> str:
    if severity == "high":
        return "medium"
    if severity == "medium":
        return "low"
    return severity


def fnmatch_path(file_path: str, pattern: str) -> bool:
    from fnmatch import fnmatch

    return fnmatch(file_path, pattern) or fnmatch(Path(file_path).name, pattern)


def _rule_payload(
    findings: list[dict[str, Any]],
    counts: dict[str, int],
    *,
    config: Any | None = None,
    disabled: bool = False,
) -> dict[str, Any]:
    return {
        "count": len(findings),
        "counts": {key: value for key, value in counts.items() if value},
        "findings": findings,
        "config": config.public_payload()["rules"] if config is not None else {"enabled": not disabled},
        "rules": [
            {
                "id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
            }
            for rule in RULES
        ],
    }
