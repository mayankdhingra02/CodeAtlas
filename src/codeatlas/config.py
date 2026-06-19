from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".codeatlas",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        "coverage",
        "target",
    }
)

SUPPORTED_EXTENSIONS = {
    ".py": "python",
}

ARTIFACT_DIR = ".codeatlas"
DATABASE_NAME = "index.db"
METADATA_NAME = "metadata.json"
STATS_NAME = "stats.json"


@dataclass(frozen=True)
class CodeAtlasPaths:
    repo_root: Path

    @property
    def artifact_dir(self) -> Path:
        return self.repo_root / ARTIFACT_DIR

    @property
    def database_path(self) -> Path:
        return self.artifact_dir / DATABASE_NAME

    @property
    def metadata_path(self) -> Path:
        return self.artifact_dir / METADATA_NAME

    @property
    def stats_path(self) -> Path:
        return self.artifact_dir / STATS_NAME

    @property
    def cache_dir(self) -> Path:
        return self.artifact_dir / "cache"


def resolve_repo_root(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()
