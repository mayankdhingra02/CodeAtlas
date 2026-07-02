from __future__ import annotations

from pathlib import Path
from typing import Any

from codeatlas.models import (
    CallRecord,
    ImportRecord,
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


CONTROL_FLOW_CALLEES = frozenset(
    {
        "catch",
        "for",
        "if",
        "return",
        "switch",
        "while",
        "with",
    }
)
HTTP_ROUTE_OWNERS = frozenset({"app", "router"})
HTTP_ROUTE_METHODS = frozenset({"delete", "get", "patch", "post", "put", "use"})
TEST_CALLEES = frozenset({"describe", "it", "test"})


class JavaScriptParser(ParserPlugin):
    language = "javascript"
    extensions = frozenset({".js", ".jsx", ".ts", ".tsx"})

    def __init__(self) -> None:
        if get_parser is None:
            msg = "JavaScript parsing requires tree-sitter and tree-sitter-language-pack."
            raise RuntimeError(msg)
        self._parsers = {
            "javascript": get_parser("javascript"),
            "typescript": get_parser("typescript"),
            "tsx": get_parser("tsx"),
        }

    def parse(self, repo_root: Path, source_file: SourceFile) -> ParseResult:
        content = source_file.path.read_bytes()
        source_text = content.decode("utf-8", errors="replace")
        parser = self._parser_for(source_file.path.suffix)
        try:
            tree = parser.parse(source_text)
        except TypeError:
            tree = parser.parse(content)
        root_node = tree.root_node() if callable(tree.root_node) else tree.root_node
        module_name = module_name_for_path(source_file.relative_path)
        extractor = _JavaScriptTreeSitterExtractor(content, module_name)
        return extractor.extract(source_file, root_node)

    def _parser_for(self, suffix: str) -> Any:
        if suffix == ".ts":
            return self._parsers["typescript"]
        if suffix == ".tsx":
            return self._parsers["tsx"]
        return self._parsers["javascript"]


def module_name_for_path(relative_path: str) -> str:
    path = Path(relative_path)
    return ".".join(path.with_suffix("").parts) if path.parts else path.stem


class _JavaScriptTreeSitterExtractor:
    def __init__(self, content: bytes, module_name: str) -> None:
        self.content = content
        self.source = content.decode("utf-8", errors="replace")
        self.module_name = module_name
        self.imports: list[ImportRecord] = []
        self.symbols: list[SymbolRecord] = []
        self.calls: list[CallRecord] = []
        self.references: list[ReferenceRecord] = []

    def extract(self, source_file: SourceFile, root: Node) -> ParseResult:
        self._collect_imports(root)
        self._process_block(root, ())
        self._collect_route_and_test_symbols(root)
        return ParseResult(
            source_file=source_file,
            module_name=self.module_name,
            imports=tuple(self.imports),
            symbols=tuple(self.symbols),
            calls=tuple(self.calls),
            references=tuple(self.references),
        )

    def _collect_imports(self, root: Node) -> None:
        for node in self._iter_nodes(root):
            node_type = _node_type(node)
            if node_type == "import_statement":
                self.imports.extend(self._parse_import_statement(node))
            elif node_type in {"lexical_declaration", "variable_declaration"}:
                self.imports.extend(self._parse_require_declaration(node))

    def _parse_import_statement(self, node: Node) -> tuple[ImportRecord, ...]:
        module = self._module_string(node)
        if not module:
            return ()
        line_number = self._line_start(node)
        clause = next(
            (child for child in _named_children(node) if _node_type(child) == "import_clause"),
            None,
        )
        if clause is None:
            return (
                ImportRecord(
                    module=module,
                    name=None,
                    alias=None,
                    line_number=line_number,
                    is_from=False,
                ),
            )

        records: list[ImportRecord] = []
        for child in _named_children(clause):
            child_type = _node_type(child)
            if child_type in {"identifier", "type_identifier"}:
                records.append(
                    ImportRecord(
                        module=module,
                        name=self._text(child),
                        alias=None,
                        line_number=self._line_start(child),
                        is_from=True,
                    )
                )
            elif child_type == "named_imports":
                records.extend(self._named_import_records(module, child))
            elif child_type == "namespace_import":
                alias = self._first_identifier_text(child)
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

    def _named_import_records(self, module: str, node: Node) -> tuple[ImportRecord, ...]:
        records: list[ImportRecord] = []
        for specifier in _named_children(node):
            if _node_type(specifier) != "import_specifier":
                continue
            identifiers = [
                self._text(child)
                for child in _named_children(specifier)
                if _node_type(child) in {"identifier", "property_identifier", "type_identifier"}
            ]
            if not identifiers:
                continue
            records.append(
                ImportRecord(
                    module=module,
                    name=identifiers[0],
                    alias=identifiers[1] if len(identifiers) > 1 else None,
                    line_number=self._line_start(specifier),
                    is_from=True,
                )
            )
        return tuple(records)

    def _parse_require_declaration(self, node: Node) -> tuple[ImportRecord, ...]:
        records: list[ImportRecord] = []
        for declarator in _named_children(node):
            if _node_type(declarator) != "variable_declarator":
                continue
            name = _field_child(declarator, "name")
            value = _field_child(declarator, "value")
            if name is None or value is None or _node_type(value) != "call_expression":
                continue
            function_node = _field_child(value, "function")
            if self._text(function_node) != "require":
                continue
            module = self._first_argument_string(value)
            if module:
                records.append(
                    ImportRecord(
                        module=module,
                        name=None,
                        alias=self._text(name),
                        line_number=self._line_start(declarator),
                        is_from=False,
                    )
                )
        return tuple(records)

    def _process_block(self, node: Node, parent_parts: tuple[str, ...]) -> None:
        for child in _named_children(node):
            self._process_statement(child, parent_parts)

    def _process_statement(self, node: Node, parent_parts: tuple[str, ...]) -> None:
        node_type = _node_type(node)
        if node_type == "export_statement":
            self._process_block(node, parent_parts)
            return
        if node_type in {"class", "class_declaration"}:
            self._handle_class(node, parent_parts)
            return
        if node_type in {"function", "function_declaration"}:
            self._handle_function(node, parent_parts, SymbolKind.FUNCTION)
            return
        if node_type in {"lexical_declaration", "variable_declaration"}:
            self._handle_variable_declaration(node, parent_parts)
            return
        self._process_block(node, parent_parts)

    def _handle_class(self, node: Node, parent_parts: tuple[str, ...]) -> None:
        name_node = _field_child(node, "name") or self._first_named_child_of_type(
            node, {"identifier", "type_identifier"}
        )
        if name_node is None:
            return
        name = self._text(name_node)
        qualified_name = ".".join((self.module_name, *parent_parts, name))
        self._add_symbol(
            name,
            qualified_name,
            SymbolKind.CLASS,
            node,
            parent_qualified_name=self._parent_qualified_name(parent_parts),
        )
        for base_name in self._class_base_names(node):
            self.references.append(
                ReferenceRecord(
                    source_qualified_name=qualified_name,
                    target_name=base_name,
                    line_number=self._line_start(node),
                )
            )
        body = _field_child(node, "body")
        if body is not None:
            for child in _named_children(body):
                if _node_type(child) == "method_definition":
                    self._handle_method(child, (*parent_parts, name))

    def _handle_function(
        self,
        node: Node,
        parent_parts: tuple[str, ...],
        kind: SymbolKind,
        *,
        explicit_name: str | None = None,
        symbol_node: Node | None = None,
    ) -> None:
        name_node = _field_child(node, "name")
        name = explicit_name or (self._text(name_node) if name_node is not None else "")
        if not name:
            return
        target_node = symbol_node or node
        qualified_name = ".".join((self.module_name, *parent_parts, name))
        self._add_symbol(
            name,
            qualified_name,
            kind,
            target_node,
            parent_qualified_name=self._parent_qualified_name(parent_parts),
        )
        body = _field_child(node, "body") or target_node
        self._collect_calls(owner_qualified_name=qualified_name, root=body)

    def _handle_method(self, node: Node, parent_parts: tuple[str, ...]) -> None:
        name_node = _field_child(node, "name") or self._first_named_child_of_type(
            node,
            {"identifier", "private_property_identifier", "property_identifier"},
        )
        if name_node is None:
            return
        self._handle_function(
            node,
            parent_parts,
            SymbolKind.METHOD,
            explicit_name=self._text(name_node).removeprefix("#"),
        )

    def _handle_variable_declaration(self, node: Node, parent_parts: tuple[str, ...]) -> None:
        for declarator in _named_children(node):
            if _node_type(declarator) != "variable_declarator":
                continue
            name_node = _field_child(declarator, "name")
            value_node = _field_child(declarator, "value")
            if name_node is None or value_node is None:
                continue
            if _node_type(value_node) in {"arrow_function", "function", "function_expression"}:
                self._handle_function(
                    value_node,
                    parent_parts,
                    SymbolKind.FUNCTION,
                    explicit_name=self._text(name_node),
                    symbol_node=declarator,
                )

    def _collect_route_and_test_symbols(self, root: Node) -> None:
        for call_node in self._iter_nodes(root):
            if _node_type(call_node) != "call_expression":
                continue
            display, target_name = self._call_target(call_node)
            route = route_symbol_name(display, self._first_argument_string(call_node))
            if route:
                self._add_synthetic_symbol(route, call_node)
                continue
            if target_name in TEST_CALLEES:
                title = self._first_argument_string(call_node)
                if title:
                    self._add_synthetic_symbol(f"{target_name}_{slug_name(title)}", call_node)

    def _add_synthetic_symbol(self, name: str, node: Node) -> None:
        qualified_name = f"{self.module_name}.{name}"
        if self._add_symbol(name, qualified_name, SymbolKind.FUNCTION, node):
            self._collect_calls(owner_qualified_name=qualified_name, root=node)

    def _collect_calls(self, *, owner_qualified_name: str, root: Node) -> None:
        for call_node in self._iter_non_nested_nodes(root, {"call_expression"}):
            display, target_name = self._call_target(call_node)
            if not target_name or target_name in CONTROL_FLOW_CALLEES:
                continue
            self.calls.append(
                CallRecord(
                    source_qualified_name=owner_qualified_name,
                    target_name=target_name,
                    display_name=display,
                    line_number=self._line_start(call_node),
                    arguments=self._call_arguments(call_node),
                )
            )

    def _call_target(self, call_node: Node) -> tuple[str, str]:
        function_node = _field_child(call_node, "function")
        if function_node is None:
            return "", ""
        display = self._text(function_node)
        if _node_type(function_node) == "member_expression":
            children = _named_children(function_node)
            target = self._text(children[-1]) if children else display.rsplit(".", 1)[-1]
            owner = self._text(children[0]) if children else ""
            if owner in HTTP_ROUTE_OWNERS and target.lower() in HTTP_ROUTE_METHODS:
                return display, "route_" + target.lower()
            return display, target
        return display, display

    def _call_arguments(self, call_node: Node) -> tuple[str, ...]:
        arguments_node = _field_child(call_node, "arguments")
        if arguments_node is None:
            return ()
        arguments: list[str] = []
        for child in _named_children(arguments_node):
            text = self._text(child).strip()
            if text:
                arguments.append(text)
        return tuple(arguments)

    def _first_argument_string(self, call_node: Node) -> str:
        arguments_node = _field_child(call_node, "arguments")
        if arguments_node is None:
            return ""
        first = next(
            (child for child in _named_children(arguments_node) if _node_type(child) == "string"),
            None,
        )
        return self._string_value(first) if first is not None else ""

    def _module_string(self, node: Node) -> str:
        strings = [child for child in _named_children(node) if _node_type(child) == "string"]
        return self._string_value(strings[-1]) if strings else ""

    def _string_value(self, node: Node | None) -> str:
        if node is None:
            return ""
        fragments = [
            self._text(child)
            for child in _named_children(node)
            if _node_type(child) in {"string_fragment", "template_chars"}
        ]
        if fragments:
            return "".join(fragments)
        return self._text(node).strip("'\"`")

    def _class_base_names(self, node: Node) -> tuple[str, ...]:
        heritage = next(
            (child for child in _named_children(node) if _node_type(child) == "class_heritage"),
            None,
        )
        if heritage is None:
            return ()
        names: list[str] = []
        for child in self._iter_nodes(heritage):
            if _node_type(child) in {"identifier", "type_identifier"}:
                names.append(self._text(child).rsplit(".", 1)[-1])
        return tuple(dict.fromkeys(names))

    def _add_symbol(
        self,
        name: str,
        qualified_name: str,
        kind: SymbolKind,
        node: Node,
        *,
        parent_qualified_name: str | None = None,
    ) -> bool:
        if any(symbol.qualified_name == qualified_name for symbol in self.symbols):
            return False
        self.symbols.append(
            SymbolRecord(
                name=name,
                qualified_name=qualified_name,
                kind=kind,
                module=self.module_name,
                line_start=self._line_start(node),
                line_end=self._line_end(node),
                col_start=self._col_start(node),
                col_end=self._col_end(node),
                signature=self._header(node),
                parent_qualified_name=parent_qualified_name,
            )
        )
        return True

    def _header(self, node: Node) -> str:
        body = _field_child(node, "body")
        end_byte = _start_byte(body) if body is not None else _end_byte(node)
        header = self.content[_start_byte(node) : end_byte].decode("utf-8", errors="replace")
        first_line = header.splitlines()[0] if header.splitlines() else header
        return first_line.strip().removesuffix("{").strip()

    def _parent_qualified_name(self, parent_parts: tuple[str, ...]) -> str | None:
        if not parent_parts:
            return None
        return ".".join((self.module_name, *parent_parts))

    def _iter_nodes(self, node: Node) -> tuple[Node, ...]:
        found: list[Node] = []
        stack = [node]
        while stack:
            current = stack.pop()
            found.append(current)
            stack.extend(reversed(_named_children(current)))
        return tuple(found)

    def _iter_non_nested_nodes(self, root: Node, wanted_types: set[str]) -> tuple[Node, ...]:
        found: list[Node] = []
        stack = list(reversed(_named_children(root)))
        while stack:
            current = stack.pop()
            current_type = _node_type(current)
            if current is not root and current_type in {
                "arrow_function",
                "class",
                "class_declaration",
                "function",
                "function_declaration",
                "function_expression",
                "method_definition",
            }:
                continue
            if current_type in wanted_types:
                found.append(current)
            stack.extend(reversed(_named_children(current)))
        return tuple(found)

    def _first_identifier_text(self, node: Node) -> str | None:
        child = self._first_named_child_of_type(
            node,
            {"identifier", "property_identifier", "type_identifier"},
        )
        return self._text(child) if child is not None else None

    def _first_named_child_of_type(self, node: Node, node_types: set[str]) -> Node | None:
        return next((child for child in _named_children(node) if _node_type(child) in node_types), None)

    def _text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self.content[_start_byte(node) : _end_byte(node)].decode("utf-8", errors="replace")

    def _line_start(self, node: Node) -> int:
        return _point_row(_start_point(node)) + 1

    def _line_end(self, node: Node) -> int:
        return _point_row(_end_point(node)) + 1

    def _col_start(self, node: Node) -> int:
        return _point_column(_start_point(node)) + 1

    def _col_end(self, node: Node) -> int:
        return _point_column(_end_point(node)) + 1


def route_symbol_name(display: str, first_argument: str) -> str:
    if not first_argument or "." not in display:
        return ""
    owner, method = display.rsplit(".", 1)
    if owner not in HTTP_ROUTE_OWNERS or method.lower() not in HTTP_ROUTE_METHODS:
        return ""
    return "route_" + method.lower() + "_" + slug_name(first_argument)


def slug_name(value: str) -> str:
    pieces = []
    for char in value.lower():
        if char.isalnum():
            pieces.append(char)
        elif pieces and pieces[-1] != "_":
            pieces.append("_")
    slug = "".join(pieces).strip("_")
    return slug[:48] or "unnamed"


def _field_child(node: Node, name: str) -> Node | None:
    child_by_field_name = getattr(node, "child_by_field_name", None)
    if child_by_field_name is None:
        return None
    return child_by_field_name(name)


def _member_value(obj: Any, *names: str) -> Any:
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            return value() if callable(value) else value
    return None


def _node_type(node: Node) -> str:
    return str(_member_value(node, "type", "kind") or "")


def _named_children(node: Node) -> tuple[Node, ...]:
    children = getattr(node, "named_children", None)
    if children is not None:
        value = children() if callable(children) else children
        return tuple(value)
    count = _member_value(node, "named_child_count") or 0
    return tuple(node.named_child(index) for index in range(int(count)))


def _start_byte(node: Node | None) -> int:
    if node is None:
        return 0
    return int(_member_value(node, "start_byte") or 0)


def _end_byte(node: Node | None) -> int:
    if node is None:
        return 0
    return int(_member_value(node, "end_byte") or 0)


def _start_point(node: Node) -> Any:
    return _member_value(node, "start_point", "start_position")


def _end_point(node: Node) -> Any:
    return _member_value(node, "end_point", "end_position")


def _point_row(point: Any) -> int:
    if hasattr(point, "row"):
        return int(point.row)
    return int(point[0])


def _point_column(point: Any) -> int:
    if hasattr(point, "column"):
        return int(point.column)
    return int(point[1])
