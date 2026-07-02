from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from pathlib import Path

from .models import SourceFile
from .project_config import enabled_extensions, load_project_config, path_is_ignored

MAX_SOURCE_BYTES = 1_000_000
MINIFIED_CANDIDATE_BYTES = 50_000
MINIFIED_LINE_BYTES = 16_000
MINIFIED_EXTENSIONS = frozenset({".js", ".jsx", ".ts", ".tsx"})


def iter_source_files(
    repo_root: Path,
    *,
    ignore_dirs: frozenset[str] | None = None,
    supported_extensions: dict[str, str] | None = None,
    max_file_size_bytes: int = MAX_SOURCE_BYTES,
) -> Iterable[SourceFile]:
    """Yield supported source files under repo_root while respecting ignored directories."""

    root = repo_root.resolve()
    project_config = load_project_config(root)
    active_ignore_dirs = ignore_dirs if ignore_dirs is not None else project_config.ignore_dirs
    active_extensions = (
        supported_extensions
        if supported_extensions is not None
        else enabled_extensions(project_config)
    )
    for dirpath, dirnames, filenames in os.walk(root):
        current_dir = Path(dirpath)
        relative_dir = _relative_path(current_dir, root)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in active_ignore_dirs
            and not path_is_ignored(_join_relative(relative_dir, name), project_config)
        ]
        for filename in sorted(filenames):
            path = current_dir / filename
            if not path.is_file():
                continue
            relative_path = _join_relative(relative_dir, filename)
            if any(part in active_ignore_dirs for part in Path(relative_path).parts):
                continue
            if path_is_ignored(relative_path, project_config):
                continue
            language = active_extensions.get(path.suffix)
            if language is None:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > max_file_size_bytes:
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            if _looks_minified(path.suffix, content):
                continue
            yield SourceFile(
                path=path,
                relative_path=relative_path,
                language=language,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256=hashlib.sha256(content).hexdigest(),
                line_count=_line_count(content),
            )


def _relative_path(path: Path, root: Path) -> str:
    if path == root:
        return ""
    return path.relative_to(root).as_posix()


def _join_relative(parent: str, child: str) -> str:
    return f"{parent}/{child}" if parent else child


def _looks_minified(suffix: str, content: bytes) -> bool:
    if suffix not in MINIFIED_EXTENSIONS or len(content) < MINIFIED_CANDIDATE_BYTES:
        return False
    lines = content.splitlines() or [content]
    return len(lines) <= 3 or any(len(line) > MINIFIED_LINE_BYTES for line in lines[:20])


def _line_count(content: bytes) -> int:
    if not content:
        return 0
    return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)
