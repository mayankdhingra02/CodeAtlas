from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import CodeAtlasPaths, resolve_repo_root


@dataclass(frozen=True)
class GraphArtifactReport:
    repo_root: Path
    database_path: Path
    artifact_path: Path
    size_bytes: int
    action: str


def export_graph_artifact(
    repo_path: str | Path,
    artifact_path: str | Path | None = None,
) -> GraphArtifactReport:
    repo_root = resolve_repo_root(repo_path)
    paths = CodeAtlasPaths(repo_root)
    source = paths.database_path
    if not source.exists():
        msg = f"No CodeAtlas index found at {source}. Run `codeatlas index {repo_root}` first."
        raise FileNotFoundError(msg)
    target = Path(artifact_path).expanduser().resolve() if artifact_path else paths.graph_artifact_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codeatlas-export-") as temp_name:
        snapshot = Path(temp_name) / "index.db"
        source_conn = sqlite3.connect(str(source))
        try:
            dest_conn = sqlite3.connect(str(snapshot))
            try:
                source_conn.backup(dest_conn)
            finally:
                dest_conn.close()
        finally:
            source_conn.close()
        with snapshot.open("rb") as input_file, gzip.open(target, "wb", compresslevel=6) as output_file:
            shutil.copyfileobj(input_file, output_file)
    return GraphArtifactReport(
        repo_root=repo_root,
        database_path=source,
        artifact_path=target,
        size_bytes=target.stat().st_size,
        action="exported",
    )


def import_graph_artifact(
    repo_path: str | Path,
    artifact_path: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> GraphArtifactReport:
    repo_root = resolve_repo_root(repo_path)
    paths = CodeAtlasPaths(repo_root)
    source = Path(artifact_path).expanduser().resolve() if artifact_path else paths.graph_artifact_path
    if not source.exists():
        msg = f"No CodeAtlas graph artifact found at {source}."
        raise FileNotFoundError(msg)
    target = paths.database_path
    if target.exists() and not overwrite:
        msg = f"Refusing to overwrite existing index at {target}. Pass --overwrite to replace it."
        raise FileExistsError(msg)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(".importing")
    with gzip.open(source, "rb") as input_file, temp_target.open("wb") as output_file:
        shutil.copyfileobj(input_file, output_file)
    _validate_sqlite(temp_target)
    for sidecar in (target, target.with_suffix(target.suffix + "-wal"), target.with_suffix(target.suffix + "-shm")):
        if sidecar.exists():
            sidecar.unlink()
    temp_target.replace(target)
    return GraphArtifactReport(
        repo_root=repo_root,
        database_path=target,
        artifact_path=source,
        size_bytes=source.stat().st_size,
        action="imported",
    )


def _validate_sqlite(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if not row or str(row[0]).lower() != "ok":
        raise ValueError(f"Imported graph artifact is not a valid SQLite database: {path}")
