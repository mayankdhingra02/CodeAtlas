from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

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

try:  # pragma: no cover - exercised in integration environments.
    from tree_sitter import Node
    from tree_sitter_language_pack import get_parser
except Exception:  # pragma: no cover
    Node = Any  # type: ignore[misc, assignment]
    get_parser = None  # type: ignore[assignment]

from .base import ParserPlugin


class PythonParser(ParserPlugin):
    language = "python"
    extensions = frozenset({".py"})

    def __init__(self) -> None:
        if get_parser is None:
            msg = "Python parsing requires tree-sitter and tree-sitter-language-pack."
            raise RuntimeError(msg)
        self._parser = get_parser("python")

    def parse(self, repo_root: Path, source_file: SourceFile) -> ParseResult:
        content = source_file.path.read_bytes()
        tree = self._parser.parse(content)
        module_name = module_name_for_path(source_file.relative_path)
        extractor = _PythonTreeSitterExtractor(content, module_name)
        return extractor.extract(source_file, tree.root_node)


def module_name_for_path(relative_path: str) -> str:
    path = Path(relative_path)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else path.stem


class _PythonTreeSitterExtractor:
    def __init__(self, content: bytes, module_name: str) -> None:
        self.content = content
        self.source = content.decode("utf-8", errors="replace")
        self.lines = self.source.splitlines()
        self.module_name = module_name
        self.imports: list[ImportRecord] = []
        self.symbols: list[SymbolRecord] = []
        self.calls: list[CallRecord] = []
        self.inheritance: list[InheritanceRecord] = []
        self.references: list[ReferenceRecord] = []

    def extract(self, source_file: SourceFile, root: Node) -> ParseResult:
        self._collect_imports(root)
        self._process_block(root, (), "module")
        return ParseResult(
            source_file=source_file,
            module_name=self.module_name,
            imports=tuple(self.imports),
            symbols=tuple(self.symbols),
            calls=tuple(self.calls),
            inheritance=tuple(self.inheritance),
            references=tuple(self.references),
        )

    def _process_block(
        self,
        block: Node,
        parent_parts: tuple[str, ...],
        scope_kind: str,
        decorators: tuple[str, ...] = (),
    ) -> None:
        for child in block.named_children:
            self._process_statement(child, parent_parts, scope_kind, decorators)

    def _process_statement(
        self,
        node: Node,
        parent_parts: tuple[str, ...],
        scope_kind: str,
        decorators: tuple[str, ...],
    ) -> None:
        if node.type == "decorated_definition":
            found_decorators = tuple(
                self._clean_decorator(self._text(child))
                for child in node.named_children
                if child.type == "decorator"
            )
            for child in node.named_children:
                if child.type in {"class_definition", "function_definition"}:
                    self._process_statement(
                        child,
                        parent_parts,
                        scope_kind,
                        decorators + found_decorators,
                    )
            return

        if node.type == "class_definition":
            self._handle_class(node, parent_parts, decorators)
            return

        if node.type == "function_definition":
            self._handle_function(node, parent_parts, scope_kind, decorators)

    def _handle_class(
        self,
        node: Node,
        parent_parts: tuple[str, ...],
        decorators: tuple[str, ...],
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self._text(name_node)
        qualified_name = ".".join((self.module_name, *parent_parts, name))
        body = node.child_by_field_name("body")
        docstring = self._docstring(body)
        symbol = SymbolRecord(
            name=name,
            qualified_name=qualified_name,
            kind=SymbolKind.CLASS,
            module=self.module_name,
            line_start=self._line_start(node),
            line_end=self._line_end(node),
            col_start=self._col_start(node),
            col_end=self._col_end(node),
            docstring=docstring,
            decorators=decorators,
            signature=self._header(node),
            parent_qualified_name=self._parent_qualified_name(parent_parts),
        )
        self.symbols.append(symbol)

        superclasses = node.child_by_field_name("superclasses")
        for base_name in self._argument_names(superclasses):
            self.inheritance.append(
                InheritanceRecord(
                    source_qualified_name=qualified_name,
                    target_name=base_name,
                    line_number=self._line_start(node),
                )
            )

        if body is not None:
            self._process_block(body, (*parent_parts, name), "class")

    def _handle_function(
        self,
        node: Node,
        parent_parts: tuple[str, ...],
        scope_kind: str,
        decorators: tuple[str, ...],
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self._text(name_node)
        qualified_name = ".".join((self.module_name, *parent_parts, name))
        body = node.child_by_field_name("body")
        kind = SymbolKind.METHOD if scope_kind == "class" else SymbolKind.FUNCTION
        symbol = SymbolRecord(
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            module=self.module_name,
            line_start=self._line_start(node),
            line_end=self._line_end(node),
            col_start=self._col_start(node),
            col_end=self._col_end(node),
            docstring=self._docstring(body),
            decorators=decorators,
            signature=self._header(node),
            parent_qualified_name=self._parent_qualified_name(parent_parts),
        )
        self.symbols.append(symbol)

        if body is not None:
            for call_node in self._iter_non_nested_nodes(body, {"call"}):
                target_display, target_name = self._call_target(call_node)
                if target_name:
                    self.calls.append(
                        CallRecord(
                            source_qualified_name=qualified_name,
                            target_name=target_name,
                            display_name=target_display,
                            line_number=self._line_start(call_node),
                        )
                    )
            for identifier_node in self._iter_non_nested_nodes(body, {"identifier"}):
                target_name = self._text(identifier_node)
                if self._is_reference_name(target_name):
                    self.references.append(
                        ReferenceRecord(
                            source_qualified_name=qualified_name,
                            target_name=target_name,
                            line_number=self._line_start(identifier_node),
                        )
                    )
            self._process_block(body, (*parent_parts, name), "function")

    def _collect_imports(self, root: Node) -> None:
        for node in self._iter_nodes(root):
            if node.type == "import_statement":
                self.imports.extend(self._parse_import_statement(node))
            elif node.type == "import_from_statement":
                self.imports.extend(self._parse_import_from_statement(node))

    def _parse_import_statement(self, node: Node) -> tuple[ImportRecord, ...]:
        records: list[ImportRecord] = []
        for child_index, child in enumerate(node.children):
            if node.field_name_for_child(child_index) != "name":
                continue
            module, alias = self._import_name_and_alias(child)
            if module:
                records.append(
                    ImportRecord(
                        module=module,
                        name=None,
                        alias=alias,
                        line_number=self._line_start(child),
                        is_from=False,
                    )
                )
        return tuple(records)

    def _parse_import_from_statement(self, node: Node) -> tuple[ImportRecord, ...]:
        module_node: Node | None = None
        names: list[Node] = []
        for child_index, child in enumerate(node.children):
            field = node.field_name_for_child(child_index)
            if field == "module_name":
                module_node = child
            elif field == "name":
                names.append(child)
        module = self._text(module_node) if module_node is not None else ""
        records: list[ImportRecord] = []
        for name_node in names:
            name, alias = self._import_name_and_alias(name_node)
            if name:
                records.append(
                    ImportRecord(
                        module=module,
                        name=name,
                        alias=alias,
                        line_number=self._line_start(name_node),
                        is_from=True,
                    )
                )
        return tuple(records)

    def _import_name_and_alias(self, node: Node) -> tuple[str, str | None]:
        if node.type != "aliased_import":
            return self._text(node), None
        name_node: Node | None = None
        alias_node: Node | None = None
        for child_index, child in enumerate(node.children):
            field = node.field_name_for_child(child_index)
            if field == "name":
                name_node = child
            elif field == "alias":
                alias_node = child
        return (
            self._text(name_node) if name_node is not None else "",
            self._text(alias_node) if alias_node is not None else None,
        )

    def _iter_nodes(self, node: Node) -> tuple[Node, ...]:
        found: list[Node] = []
        stack = [node]
        while stack:
            current = stack.pop()
            found.append(current)
            stack.extend(reversed(current.named_children))
        return tuple(found)

    def _iter_non_nested_nodes(self, root: Node, wanted_types: set[str]) -> tuple[Node, ...]:
        found: list[Node] = []
        stack = list(reversed(root.named_children))
        while stack:
            current = stack.pop()
            if current.type in {"class_definition", "function_definition", "decorated_definition"}:
                continue
            if current.type in wanted_types:
                found.append(current)
            stack.extend(reversed(current.named_children))
        return tuple(found)

    def _call_target(self, call_node: Node) -> tuple[str, str]:
        function_node = call_node.child_by_field_name("function")
        if function_node is None:
            return "", ""
        display = self._text(function_node)
        identifiers = [
            self._text(node)
            for node in self._iter_nodes(function_node)
            if node.type == "identifier"
        ]
        return display, identifiers[-1] if identifiers else display

    def _argument_names(self, argument_list: Node | None) -> tuple[str, ...]:
        if argument_list is None:
            return ()
        names: list[str] = []
        for child in argument_list.named_children:
            text = self._text(child).strip()
            if text:
                names.append(text)
        return tuple(names)

    def _docstring(self, body: Node | None) -> str | None:
        if body is None:
            return None
        first = body.named_children[0] if body.named_children else None
        if first is None or first.type != "string":
            return None
        raw = self._text(first)
        try:
            value = ast.literal_eval(raw)
        except Exception:
            value = "".join(
                self._text(child)
                for child in first.named_children
                if child.type == "string_content"
            )
        return value if isinstance(value, str) and value else None

    def _header(self, node: Node) -> str:
        body = node.child_by_field_name("body")
        end_byte = body.start_byte if body is not None else node.end_byte
        header = self.content[node.start_byte:end_byte].decode("utf-8", errors="replace")
        first_line = header.splitlines()[0] if header.splitlines() else header
        return first_line.strip().removesuffix(":")

    def _clean_decorator(self, text: str) -> str:
        return text.strip().removeprefix("@")

    def _parent_qualified_name(self, parent_parts: tuple[str, ...]) -> str | None:
        if not parent_parts:
            return None
        return ".".join((self.module_name, *parent_parts))

    def _is_reference_name(self, name: str) -> bool:
        return name not in {
            "False",
            "None",
            "True",
            "cls",
            "self",
        }

    def _text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self.content[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _line_start(self, node: Node) -> int:
        return _point_row(node.start_point) + 1

    def _line_end(self, node: Node) -> int:
        return _point_row(node.end_point) + 1

    def _col_start(self, node: Node) -> int:
        return _point_column(node.start_point) + 1

    def _col_end(self, node: Node) -> int:
        return _point_column(node.end_point) + 1


def _point_row(point: Any) -> int:
    row = getattr(point, "row", None)
    if row is not None:
        return int(row)
    return int(point[0])


def _point_column(point: Any) -> int:
    column = getattr(point, "column", None)
    if column is not None:
        return int(column)
    return int(point[1])
