from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codeatlas.models import SourceFile, SymbolKind
from codeatlas.parsers.python import PythonParser


class PythonParserRuntimeTests(unittest.TestCase):
    def test_stdlib_ast_parser_extracts_structure_and_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            repo_root = Path(temp_name)
            source_path = repo_root / "sample.py"
            source = (
                "from app.base import BaseService\n"
                "\n"
                "@service('/payments')\n"
                "class PaymentService(BaseService):\n"
                "    async def charge(self, client, total):\n"
                "        result = client.send(total, retries=2)\n"
                "\n"
                "        def audit():\n"
                "            return record(result)\n"
                "\n"
                "        return result\n"
            )
            source_path.write_text(source, encoding="utf-8")
            stat = source_path.stat()
            source_file = SourceFile(
                path=source_path,
                relative_path="sample.py",
                language="python",
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256="fixture",
                line_count=source.count("\n"),
            )

            result = PythonParser().parse(repo_root, source_file)

        self.assertEqual(result.module_name, "sample")
        self.assertEqual(result.imports[0].module, "app.base")
        self.assertEqual(result.imports[0].name, "BaseService")
        symbols = {symbol.qualified_name: symbol for symbol in result.symbols}
        self.assertEqual(symbols["sample.PaymentService"].kind, SymbolKind.CLASS)
        self.assertEqual(
            symbols["sample.PaymentService"].decorators,
            ("service('/payments')",),
        )
        self.assertEqual(
            symbols["sample.PaymentService.charge"].kind,
            SymbolKind.METHOD,
        )
        self.assertEqual(
            symbols["sample.PaymentService.charge.audit"].kind,
            SymbolKind.FUNCTION,
        )
        self.assertEqual(result.inheritance[0].target_name, "BaseService")

        calls_by_source = {}
        for call in result.calls:
            calls_by_source.setdefault(call.source_qualified_name, []).append(call)
        charge_calls = calls_by_source["sample.PaymentService.charge"]
        self.assertEqual(charge_calls[0].display_name, "client.send")
        self.assertEqual(charge_calls[0].arguments, ("total", "retries=2"))
        self.assertNotIn("record", {call.target_name for call in charge_calls})
        self.assertIn(
            "record",
            {
                call.target_name
                for call in calls_by_source["sample.PaymentService.charge.audit"]
            },
        )

    def test_parser_reports_syntax_errors_instead_of_returning_partial_ast(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            repo_root = Path(temp_name)
            source_path = repo_root / "broken.py"
            source_path.write_text("def broken(:\n    pass\n", encoding="utf-8")
            stat = source_path.stat()
            source_file = SourceFile(
                path=source_path,
                relative_path="broken.py",
                language="python",
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256="fixture",
                line_count=2,
            )

            with self.assertRaises(SyntaxError):
                PythonParser().parse(repo_root, source_file)


if __name__ == "__main__":
    unittest.main()
