from __future__ import annotations

import re
from pathlib import Path

from codeatlas.models import (
    CallRecord,
    ImportRecord,
    ParseResult,
    ReferenceRecord,
    SourceFile,
    SymbolKind,
    SymbolRecord,
)

from .base import ParserPlugin


class JavaScriptParser(ParserPlugin):
    language = "javascript"
    extensions = frozenset({".js", ".jsx", ".ts", ".tsx"})

    def parse(self, repo_root: Path, source_file: SourceFile) -> ParseResult:
        source = source_file.path.read_text(encoding="utf-8", errors="replace")
        module_name = module_name_for_path(source_file.relative_path)
        extractor = _JavaScriptRegexExtractor(source, module_name)
        return extractor.extract(source_file)


def module_name_for_path(relative_path: str) -> str:
    path = Path(relative_path)
    return ".".join(path.with_suffix("").parts) if path.parts else path.stem


class _JavaScriptRegexExtractor:
    def __init__(self, source: str, module_name: str) -> None:
        self.source = source
        self.lines = source.splitlines()
        self.module_name = module_name
        self.imports: list[ImportRecord] = []
        self.symbols: list[SymbolRecord] = []
        self.calls: list[CallRecord] = []
        self.references: list[ReferenceRecord] = []

    def extract(self, source_file: SourceFile) -> ParseResult:
        self._collect_imports()
        self._collect_symbols()
        self._collect_calls()
        return ParseResult(
            source_file=source_file,
            module_name=self.module_name,
            imports=tuple(self.imports),
            symbols=tuple(self.symbols),
            calls=tuple(self.calls),
            references=tuple(self.references),
        )

    def _collect_imports(self) -> None:
        for line_number, line in enumerate(self.lines, start=1):
            for match in re.finditer(r"\bimport\s+(?:type\s+)?(.+?)\s+from\s+['\"]([^'\"]+)['\"]", line):
                imported, module = match.groups()
                for name, alias in imported_names(imported):
                    self.imports.append(
                        ImportRecord(
                            module=module,
                            name=name,
                            alias=alias,
                            line_number=line_number,
                            is_from=True,
                        )
                    )
            for match in re.finditer(r"\bimport\s+['\"]([^'\"]+)['\"]", line):
                self.imports.append(
                    ImportRecord(
                        module=match.group(1),
                        name=None,
                        alias=None,
                        line_number=line_number,
                        is_from=False,
                    )
                )
            for match in re.finditer(
                r"\bconst\s+(\w+)\s*=\s*require\(['\"]([^'\"]+)['\"]\)", line
            ):
                alias, module = match.groups()
                self.imports.append(
                    ImportRecord(
                        module=module,
                        name=None,
                        alias=alias,
                        line_number=line_number,
                        is_from=False,
                    )
                )

    def _collect_symbols(self) -> None:
        class_ranges: list[tuple[str, int, int]] = []
        class_pattern = re.compile(
            r"^\s*(?:export\s+default\s+|export\s+)?class\s+(\w+)(?:\s+extends\s+([\w.]+))?",
            re.MULTILINE,
        )
        for match in class_pattern.finditer(self.source):
            name = match.group(1)
            start = self._line_for_offset(match.start())
            end = self._block_end_line(match.end())
            class_ranges.append((name, start, end))
            self._add_symbol(name, SymbolKind.CLASS, start, end, self._line_text(start))
            if match.group(2):
                self.references.append(
                    ReferenceRecord(
                        source_qualified_name=self._qualified_name(name),
                        target_name=match.group(2).split(".")[-1],
                        line_number=start,
                    )
                )

        function_pattern = re.compile(
            r"^\s*(?:export\s+default\s+|export\s+)?(?:async\s+)?function\s+(\w+)\s*\(",
            re.MULTILINE,
        )
        for match in function_pattern.finditer(self.source):
            name = match.group(1)
            start = self._line_for_offset(match.start())
            self._add_symbol(name, SymbolKind.FUNCTION, start, self._block_end_line(match.end()), self._line_text(start))

        arrow_pattern = re.compile(
            r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>",
            re.MULTILINE,
        )
        for match in arrow_pattern.finditer(self.source):
            name = match.group(1)
            start = self._line_for_offset(match.start())
            self._add_symbol(name, SymbolKind.FUNCTION, start, self._block_end_line(match.end()), self._line_text(start))

        route_pattern = re.compile(
            r"\b(?:app|router)\.(get|post|put|patch|delete|use)\s*\(\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        )
        for match in route_pattern.finditer(self.source):
            method, route = match.groups()
            start = self._line_for_offset(match.start())
            name = "route_" + method.lower() + "_" + slug_name(route)
            self._add_symbol(name, SymbolKind.FUNCTION, start, self._block_end_line(match.end()), self._line_text(start))

        test_pattern = re.compile(r"\b(describe|it|test)\s*\(\s*['\"]([^'\"]+)['\"]")
        for match in test_pattern.finditer(self.source):
            kind, title = match.groups()
            start = self._line_for_offset(match.start())
            name = kind + "_" + slug_name(title)
            self._add_symbol(name, SymbolKind.FUNCTION, start, self._block_end_line(match.end()), self._line_text(start))

        method_pattern = re.compile(r"^\s{2,}(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{", re.MULTILINE)
        for match in method_pattern.finditer(self.source):
            name = match.group(1)
            start = self._line_for_offset(match.start())
            parent = class_parent_for_line(class_ranges, start)
            if not parent:
                continue
            self._add_symbol(
                name,
                SymbolKind.METHOD,
                start,
                self._block_end_line(match.end()),
                self._line_text(start),
                parent=parent,
            )

    def _collect_calls(self) -> None:
        symbols = sorted(self.symbols, key=lambda symbol: symbol.line_start)
        if not symbols:
            return
        for symbol in symbols:
            body = "\n".join(self.lines[symbol.line_start - 1 : symbol.line_end])
            for match in re.finditer(r"(?<!function\s)\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(", body):
                display = match.group(1)
                target = call_target_name(display)
                if target in {"if", "for", "while", "switch", "catch", "return"}:
                    continue
                self.calls.append(
                    CallRecord(
                        source_qualified_name=symbol.qualified_name,
                        target_name=target,
                        display_name=display,
                        line_number=symbol.line_start + body[: match.start()].count("\n"),
                        arguments=(),
                    )
                )

    def _add_symbol(
        self,
        name: str,
        kind: SymbolKind,
        line_start: int,
        line_end: int,
        signature: str,
        *,
        parent: str | None = None,
    ) -> None:
        qualified_name = self._qualified_name(name, parent=parent)
        if any(symbol.qualified_name == qualified_name for symbol in self.symbols):
            return
        self.symbols.append(
            SymbolRecord(
                name=name,
                qualified_name=qualified_name,
                kind=kind,
                module=self.module_name,
                line_start=line_start,
                line_end=max(line_start, line_end),
                col_start=0,
                col_end=len(signature),
                signature=signature.strip() or None,
                parent_qualified_name=self._qualified_name(parent) if parent else None,
            )
        )

    def _qualified_name(self, name: str | None, *, parent: str | None = None) -> str:
        if not name:
            return self.module_name
        if parent:
            return ".".join((self.module_name, parent, name))
        return ".".join((self.module_name, name))

    def _line_for_offset(self, offset: int) -> int:
        return self.source.count("\n", 0, offset) + 1

    def _line_text(self, line_number: int) -> str:
        if line_number < 1 or line_number > len(self.lines):
            return ""
        return self.lines[line_number - 1].strip()

    def _block_end_line(self, offset: int) -> int:
        depth = 0
        started = False
        for index in range(offset, len(self.source)):
            char = self.source[index]
            if char == "{":
                depth += 1
                started = True
            elif char == "}":
                depth -= 1
                if started and depth <= 0:
                    return self._line_for_offset(index)
        return self._line_for_offset(offset)


def imported_names(imported: str) -> list[tuple[str | None, str | None]]:
    text = imported.strip()
    if not text:
        return [(None, None)]
    results: list[tuple[str | None, str | None]] = []
    default = text.split(",", 1)[0].strip()
    if default and not default.startswith(("{", "*")):
        results.append((default, None))
    brace_match = re.search(r"\{([^}]+)\}", text)
    if brace_match:
        for part in brace_match.group(1).split(","):
            clean = part.strip()
            if not clean:
                continue
            if " as " in clean:
                name, alias = [piece.strip() for piece in clean.split(" as ", 1)]
            else:
                name, alias = clean, None
            results.append((name, alias))
    namespace_match = re.search(r"\*\s+as\s+(\w+)", text)
    if namespace_match:
        results.append((None, namespace_match.group(1)))
    return results or [(None, None)]


def class_parent_for_line(class_ranges: list[tuple[str, int, int]], line_number: int) -> str | None:
    for name, start, end in sorted(class_ranges, key=lambda item: item[1], reverse=True):
        if start < line_number <= end:
            return name
    return None


def call_target_name(display: str) -> str:
    if "." not in display:
        return display
    owner, method = display.rsplit(".", 1)
    if owner in {"app", "router"} and method.lower() in {"get", "post", "put", "patch", "delete", "use"}:
        return "route_" + method.lower()
    return method


def slug_name(value: str) -> str:
    pieces = []
    for char in value.lower():
        if char.isalnum():
            pieces.append(char)
        elif pieces and pieces[-1] != "_":
            pieces.append("_")
    slug = "".join(pieces).strip("_")
    return slug[:48] or "unnamed"
