from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from .config import DEFAULT_IGNORE_DIRS, SUPPORTED_EXTENSIONS
from .models import SourceFile


def iter_source_files(
    repo_root: Path,
    *,
    ignore_dirs: frozenset[str] = DEFAULT_IGNORE_DIRS,
    supported_extensions: dict[str, str] = SUPPORTED_EXTENSIONS,
) -> Iterable[SourceFile]:
    """Yield supported source files under repo_root while respecting ignored directories."""

    root = repo_root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in ignore_dirs for part in path.relative_to(root).parts):
            continue
        language = supported_extensions.get(path.suffix)
        if language is None:
            continue
        stat = path.stat()
        content = path.read_bytes()
        yield SourceFile(
            path=path,
            relative_path=path.relative_to(root).as_posix(),
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
