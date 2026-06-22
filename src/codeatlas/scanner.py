from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from .models import SourceFile
from .project_config import enabled_extensions, load_project_config, path_is_ignored


def iter_source_files(
    repo_root: Path,
    *,
    ignore_dirs: frozenset[str] | None = None,
    supported_extensions: dict[str, str] | None = None,
) -> Iterable[SourceFile]:
    """Yield supported source files under repo_root while respecting ignored directories."""

    root = repo_root.resolve()
    project_config = load_project_config(root)
    active_ignore_dirs = ignore_dirs if ignore_dirs is not None else project_config.ignore_dirs
    active_extensions = supported_extensions if supported_extensions is not None else enabled_extensions(project_config)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root).as_posix()
        if any(part in active_ignore_dirs for part in path.relative_to(root).parts):
            continue
        if path_is_ignored(relative_path, project_config):
            continue
        language = active_extensions.get(path.suffix)
        if language is None:
            continue
        stat = path.stat()
        content = path.read_bytes()
        yield SourceFile(
            path=path,
            relative_path=relative_path,
            language=language,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            sha256=hashlib.sha256(content).hexdigest(),
            line_count=_line_count(content),
        )


def _line_count(content: bytes) -> int:
    if not content:
        return 0
    return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)
