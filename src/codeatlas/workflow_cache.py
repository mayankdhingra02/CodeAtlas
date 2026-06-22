from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import CodeAtlasPaths, resolve_repo_root
from .project_config import load_project_config


def cached_workflow(
    repo_path: str | Path,
    name: str,
    params: dict[str, Any],
    compute: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    config = load_project_config(repo_root)
    if not config.cache.enabled:
        payload = compute()
        payload["cache"] = {"hit": False, "enabled": False}
        return payload
    paths = CodeAtlasPaths(repo_root)
    cache_dir = paths.cache_dir / "workflows"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key(paths.database_path, config.path, config.fingerprint, name, params)
    cache_path = cache_dir / f"{key}.json"
    now = time.time()
    if cache_path.exists() and config.cache.ttl_seconds > 0:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = None
        if isinstance(cached, dict):
            created_at = float(cached.get("_cached_at", 0) or 0)
            if now - created_at <= config.cache.ttl_seconds:
                payload = cached.get("payload")
                if isinstance(payload, dict):
                    payload = dict(payload)
                    payload["cache"] = {
                        "hit": True,
                        "enabled": True,
                        "key": key,
                        "age_seconds": round(now - created_at, 3),
                    }
                    return payload
    payload = compute()
    cache_payload = {"_cached_at": now, "payload": payload}
    cache_path.write_text(json.dumps(cache_payload, sort_keys=True, default=str), encoding="utf-8")
    payload = dict(payload)
    payload["cache"] = {"hit": False, "enabled": True, "key": key, "age_seconds": 0}
    return payload


def cache_key(
    database_path: Path,
    config_path: Path | None,
    config_fingerprint: str,
    name: str,
    params: dict[str, Any],
) -> str:
    database_mtime = database_path.stat().st_mtime_ns if database_path.exists() else 0
    config_mtime = config_path.stat().st_mtime_ns if config_path and config_path.exists() else 0
    payload = {
        "database_mtime": database_mtime,
        "config_mtime": config_mtime,
        "config": config_fingerprint,
        "name": name,
        "params": params,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
