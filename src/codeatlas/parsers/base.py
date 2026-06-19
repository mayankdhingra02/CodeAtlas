from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from codeatlas.models import ParseResult, SourceFile


class ParserPlugin(ABC):
    language: str
    extensions: frozenset[str]

    @abstractmethod
    def parse(self, repo_root: Path, source_file: SourceFile) -> ParseResult:
        """Parse one file into CodeAtlas records."""
