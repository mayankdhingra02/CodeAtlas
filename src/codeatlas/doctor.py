from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import CodeAtlasPaths, resolve_repo_root
from .parsers import ParserRegistry
from .project_config import load_project_config
from .status import index_status
from .storage import GraphStore, SCHEMA_VERSION


def doctor_report(repo_path: str | Path) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    paths = CodeAtlasPaths(repo_root)
    status = index_status(repo_root)
    checks: list[dict[str, Any]] = []

    add_check(
        checks,
        name="index database",
        ok=paths.database_path.exists(),
        detail=str(paths.database_path) if paths.database_path.exists() else "missing",
        command=f"codeatlas index {repo_root}",
    )
    checks.append(schema_check(paths.database_path, repo_root))
    add_check(
        checks,
        name="index freshness",
        ok=bool(status.get("indexed")) and not bool(status.get("stale")),
        detail=(
            f"age={status.get('index_age_seconds')}s, "
            f"dirty={status.get('dirty_files_count')}, "
            f"new={status.get('new_files')}, deleted={status.get('deleted_files')}"
        ),
        command=f"codeatlas index {repo_root} --incremental",
    )
    checks.append(parser_check())
    checks.append(config_check(repo_root))
    checks.append(stats_check(paths.stats_path, repo_root))

    recommended_commands = tuple(
        dict.fromkeys(
            str(check["command"])
            for check in checks
            if not check["ok"] and check.get("command")
        )
    )
    return {
        "ok": all(bool(check["ok"]) for check in checks),
        "repo_root": str(repo_root),
        "index_age_seconds": status.get("index_age_seconds"),
        "dirty_files_count": status.get("dirty_files_count"),
        "stale": bool(status.get("stale")),
        "checks": checks,
        "recommended_commands": recommended_commands,
    }


def schema_check(database_path: Path, repo_root: Path) -> dict[str, Any]:
    if not database_path.exists():
        return {
            "name": "schema version",
            "ok": False,
            "detail": "index database is missing",
            "command": f"codeatlas index {repo_root}",
        }
    store = GraphStore(database_path)
    try:
        store.initialize(validate_schema=False)
        status = store.schema_version_status()
    finally:
        store.close()
    return {
        "name": "schema version",
        "ok": bool(status["ok"]),
        "detail": f"actual={status['actual']}, expected={SCHEMA_VERSION}",
        "command": "" if status["ok"] else f"codeatlas index {repo_root}",
    }


def parser_check() -> dict[str, Any]:
    try:
        languages = ParserRegistry().supported_languages
    except Exception as exc:
        return {
            "name": "parser availability",
            "ok": False,
            "detail": str(exc),
            "command": "pip install -e '.[semantic]'",
        }
    return {
        "name": "parser availability",
        "ok": True,
        "detail": ", ".join(languages),
        "command": "",
    }


def config_check(repo_root: Path) -> dict[str, Any]:
    config = load_project_config(repo_root)
    detail = str(config.path) if config.path else "default configuration"
    return {
        "name": "project config",
        "ok": True,
        "detail": f"{detail}; fingerprint={config.fingerprint}",
        "command": "",
    }


def stats_check(stats_path: Path, repo_root: Path) -> dict[str, Any]:
    if not stats_path.exists():
        return {
            "name": "parse quality stats",
            "ok": False,
            "detail": "stats.json is missing",
            "command": f"codeatlas index {repo_root}",
        }
    try:
        payload = json.loads(stats_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "name": "parse quality stats",
            "ok": False,
            "detail": f"stats.json is unreadable: {exc}",
            "command": f"codeatlas index {repo_root}",
        }
    quality = payload.get("parse_quality") if isinstance(payload, dict) else None
    summary = quality.get("summary") if isinstance(quality, dict) else None
    if not isinstance(summary, dict):
        return {
            "name": "parse quality stats",
            "ok": False,
            "detail": "stats.json does not include parse_quality.summary",
            "command": f"codeatlas index {repo_root}",
        }
    return {
        "name": "parse quality stats",
        "ok": True,
        "detail": (
            f"symbols/KLOC={summary.get('symbols_per_kloc')}, "
            f"unresolved_call_ratio={summary.get('unresolved_call_ratio')}"
        ),
        "command": "",
    }


def add_check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    ok: bool,
    detail: str,
    command: str,
) -> None:
    checks.append({"name": name, "ok": ok, "detail": detail, "command": "" if ok else command})
