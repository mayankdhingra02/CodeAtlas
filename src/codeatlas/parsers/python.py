from __future__ import annotations

import ast
from pathlib import Path

from codeatlas.models import (
    CallRecord,
    ImportRecord,
    InheritanceRecord,
    ParseResult,
    ReferenceRecord,
    SourceFile,
    SymbolKind,
    SymbolRecord,
)

from .base import ParserPlugin


class PythonParser(ParserPlugin):
    """Deterministic Python parser backed by the standard-library AST."""

    language = "python"
    extensions = frozenset({".py"})

    def parse(self, repo_root: Path, source_file: SourceFile) -> ParseResult:
        del repo_root
        source = source_file.path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(
            source,
            filename=source_file.relative_path,
            mode="exec",
            type_comments=True,
        )
        module_name = module_name_for_path(source_file.relative_path)
        return _PythonAstExtractor(source, module_name).extract(source_file, tree)


def module_name_for_path(relative_path: str) -> str:
    path = Path(relative_path)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else path.stem


class _PythonAstExtractor:
    def __init__(self, source: str, module_name: str) -> None:
        self.source = source
        self.lines = source.splitlines()
        self.module_name = module_name
        self.imports: list[ImportRecord] = []
        self.symbols: list[SymbolRecord] = []
        self.calls: list[CallRecord] = []
        self.inheritance: list[InheritanceRecord] = []
        self.references: list[ReferenceRecord] = []

    def extract(self, source_file: SourceFile, tree: ast.Module) -> ParseResult:
        self._collect_imports(tree)
        self._process_body(tree.body, (), "module")
        return ParseResult(
            source_file=source_file,
            module_name=self.module_name,
            imports=tuple(self.imports),
            symbols=tuple(self.symbols),
            calls=tuple(self.calls),
            inheritance=tuple(self.inheritance),
            references=tuple(self.references),
        )

    def _collect_imports(self, tree: ast.Module) -> None:
        import_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        import_nodes.sort(key=lambda node: (node.lineno, node.col_offset))

        for node in import_nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports.append(
                        ImportRecord(
                            module=alias.name,
                            name=None,
                            alias=alias.asname,
                            line_number=node.lineno,
                            is_from=False,
                        )
                    )
                continue

            module = "." * node.level + (node.module or "")
            for alias in node.names:
                self.imports.append(
                    ImportRecord(
                        module=module,
                        name=alias.name,
                        alias=alias.asname,
                        line_number=node.lineno,
                        is_from=True,
                    )
                )

    def _process_body(
        self,
        body: list[ast.stmt],
        parent_parts: tuple[str, ...],
        scope_kind: str,
    ) -> None:
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                self._handle_class(statement, parent_parts)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._handle_function(statement, parent_parts, scope_kind)

    def _handle_class(
        self,
        node: ast.ClassDef,
        parent_parts: tuple[str, ...],
    ) -> None:
        qualified_name = self._qualified_name((*parent_parts, node.name))
        self.symbols.append(
            SymbolRecord(
                name=node.name,
                qualified_name=qualified_name,
                kind=SymbolKind.CLASS,
                module=self.module_name,
                line_start=node.lineno,
                line_end=self._line_end(node),
                col_start=node.col_offset + 1,
                col_end=self._col_end(node),
                docstring=ast.get_docstring(node, clean=False),
                decorators=self._decorators(node.decorator_list),
                signature=self._definition_header(node),
                parent_qualified_name=self._parent_qualified_name(parent_parts),
            )
        )

        for base in node.bases:
            target = self._expr_text(base)
            if target:
                self.inheritance.append(
                    InheritanceRecord(
                        source_qualified_name=qualified_name,
                        target_name=target,
                        line_number=getattr(base, "lineno", node.lineno),
                    )
                )

        self._process_body(node.body, (*parent_parts, node.name), "class")

    def _handle_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent_parts: tuple[str, ...],
        scope_kind: str,
    ) -> None:
        qualified_name = self._qualified_name((*parent_parts, node.name))
        kind = SymbolKind.METHOD if scope_kind == "class" else SymbolKind.FUNCTION
        self.symbols.append(
            SymbolRecord(
                name=node.name,
                qualified_name=qualified_name,
                kind=kind,
                module=self.module_name,
                line_start=node.lineno,
                line_end=self._line_end(node),
                col_start=node.col_offset + 1,
                col_end=self._col_end(node),
                docstring=ast.get_docstring(node, clean=False),
                decorators=self._decorators(node.decorator_list),
                signature=self._definition_header(node),
                parent_qualified_name=self._parent_qualified_name(parent_parts),
            )
        )

        visitor = _FunctionBodyVisitor(self, qualified_name)
        for statement in node.body:
            visitor.visit(statement)

        self._process_body(node.body, (*parent_parts, node.name), "function")

    def _record_call(self, source_qualified_name: str, node: ast.Call) -> None:
        display_name, target_name = self._call_target(node.func)
        if not target_name:
            return
        self.calls.append(
            CallRecord(
                source_qualified_name=source_qualified_name,
                target_name=target_name,
                display_name=display_name,
                line_number=node.lineno,
                arguments=self._call_arguments(node),
            )
        )

    def _record_reference(self, source_qualified_name: str, node: ast.Name) -> None:
        if node.id in {"False", "None", "True", "cls", "self"}:
            return
        self.references.append(
            ReferenceRecord(
                source_qualified_name=source_qualified_name,
                target_name=node.id,
                line_number=node.lineno,
            )
        )

    def _call_target(self, function: ast.expr) -> tuple[str, str]:
        display = self._expr_text(function)
        if isinstance(function, ast.Name):
            return display or function.id, function.id
        if isinstance(function, ast.Attribute):
            return display or function.attr, function.attr
        return display, display.rsplit(".", 1)[-1] if display else ""

    def _call_arguments(self, node: ast.Call) -> tuple[str, ...]:
        arguments: list[str] = []
        for argument in node.args:
            text = self._expr_text(argument)
            if text:
                arguments.append(text)
        for keyword in node.keywords:
            text = self._expr_text(keyword)
            if not text:
                value = self._expr_text(keyword.value)
                if keyword.arg is None:
                    text = f"**{value}" if value else ""
                elif value:
                    text = f"{keyword.arg}={value}"
            if text:
                arguments.append(text)
        return tuple(arguments)

    def _decorators(self, decorators: list[ast.expr]) -> tuple[str, ...]:
        return tuple(text for decorator in decorators if (text := self._expr_text(decorator)))

    def _definition_header(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> str | None:
        if node.lineno < 1 or node.lineno > len(self.lines):
            return None
        header = self.lines[node.lineno - 1].strip().removesuffix(":")
        return header or None

    def _expr_text(self, node: ast.AST) -> str:
        text = ast.get_source_segment(self.source, node)
        if text:
            return text.strip()
        try:
            return ast.unparse(node).strip()
        except Exception:
            return ""

    def _qualified_name(self, parts: tuple[str, ...]) -> str:
        return ".".join((self.module_name, *parts))

    def _parent_qualified_name(self, parent_parts: tuple[str, ...]) -> str | None:
        if not parent_parts:
            return None
        return self._qualified_name(parent_parts)

    def _line_end(self, node: ast.AST) -> int:
        return int(getattr(node, "end_lineno", None) or getattr(node, "lineno", 1))

    def _col_end(self, node: ast.AST) -> int:
        return int(getattr(node, "end_col_offset", None) or getattr(node, "col_offset", 0)) + 1


class _FunctionBodyVisitor(ast.NodeVisitor):
    """Collect behavior from one function without entering nested definitions."""

    def __init__(self, extractor: _PythonAstExtractor, source_qualified_name: str) -> None:
        self.extractor = extractor
        self.source_qualified_name = source_qualified_name

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        del node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        del node

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node

    def visit_Call(self, node: ast.Call) -> None:
        self.extractor._record_call(self.source_qualified_name, node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self.extractor._record_reference(self.source_qualified_name, node)
