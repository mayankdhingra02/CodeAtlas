from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from codeatlas.models import SourceFile
from codeatlas.parsers.python import PythonParser


class EmptyRoot:
    named_children: tuple[Any, ...] = ()


class EmptyTree:
    root_node = EmptyRoot()


class RecordingParser:
    def __init__(self) -> None:
        self.source: bytes | None = None

    def parse(self, source: bytes) -> EmptyTree:
        if not isinstance(source, bytes):
            raise AssertionError("Tree-sitter input must be bytes")
        self.source = source
        return EmptyTree()


class PythonParserRuntimeTests(unittest.TestCase):
    def test_parser_passes_source_bytes_to_native_tree_sitter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            repo_root = Path(temp_name)
            source_path = repo_root / "sample.py"
            source = "def greet(name: str) -> str:\n    return f'Hello {name}'\n"
            source_path.write_text(source, encoding="utf-8")
            stat = source_path.stat()
            source_file = SourceFile(
                path=source_path,
                relative_path="sample.py",
                language="python",
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256="fixture",
                line_count=2,
            )
            native_parser = RecordingParser()
            parser = PythonParser.__new__(PythonParser)
            parser._parser = native_parser

            result = parser.parse(repo_root, source_file)

            self.assertEqual(native_parser.source, source.encode("utf-8"))
            self.assertEqual(result.module_name, "sample")


if __name__ == "__main__":
    unittest.main()
