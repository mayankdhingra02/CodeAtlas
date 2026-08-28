from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

INDEX_DIR = ".codeatlas-ast"
INDEX_FILE = "index.json"
SCHEMA_VERSION = 1
IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules",
    "build", "dist", "vendor", INDEX_DIR,
}
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "code", "does",
    "for", "from", "how", "in", "is", "it", "of", "on", "or", "that",
    "the", "this", "to", "what", "where", "which", "with",
}


@dataclass(frozen=True)
class Symbol:
    file: str
    module: str
    name: str
    qualified_name: str
    kind: str
    line_start: int
    line_end: int
    signature: str
    docstring: str | None
    decorators: tuple[str, ...]
    parent: str | None


@dataclass(frozen=True)
class Call:
    source: str
    target: str
    display: str
    line: int


@dataclass(frozen=True)
class Import:
    file: str
    module: str
    name: str | None
    alias: str | None
    line: int


@dataclass(frozen=True)
class Index:
    schema_version: int
    repository_root: str
    generated_at: str
    files: tuple[dict[str, Any], ...]
    symbols: tuple[Symbol, ...]
    calls: tuple[Call, ...]
    imports: tuple[Import, ...]
    inheritance: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Index":
        version = int(data.get("schema_version", 0))
        if version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported index schema {version}; re-run index")
        return cls(
            schema_version=version,
            repository_root=str(data["repository_root"]),
            generated_at=str(data["generated_at"]),
            files=tuple(dict(item) for item in data.get("files", [])),
            symbols=tuple(
                Symbol(**{**item, "decorators": tuple(item.get("decorators", []))})
                for item in data.get("symbols", [])
            ),
            calls=tuple(Call(**item) for item in data.get("calls", [])),
            imports=tuple(Import(**item) for item in data.get("imports", [])),
            inheritance=tuple(dict(item) for item in data.get("inheritance", [])),
            errors=tuple(str(item) for item in data.get("errors", [])),
        )


@dataclass(frozen=True)
class Snippet:
    file: str
    qualified_name: str
    kind: str
    line_start: int
    line_end: int
    score: float
    reason: str
    code: str

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.code)


@dataclass(frozen=True)
class Retrieval:
    query: str
    snippets: tuple[Snippet, ...]
    baseline_tokens: int
    context_tokens: int
    latency_ms: float

    @property
    def savings_percent(self) -> float:
        if self.baseline_tokens <= 0:
            return 0.0
        return max(self.baseline_tokens - self.context_tokens, 0) / self.baseline_tokens * 100

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["savings_percent"] = self.savings_percent
        return data


def index_path(repo: str | Path) -> Path:
    return Path(repo).expanduser().resolve() / INDEX_DIR / INDEX_FILE


def build_index(repo: str | Path, output: str | Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    root = _root(repo)
    destination = Path(output).expanduser().resolve() if output else index_path(root)
    files: list[dict[str, Any]] = []
    symbols: list[Symbol] = []
    calls: list[Call] = []
    imports: list[Import] = []
    inheritance: list[dict[str, Any]] = []
    errors: list[str] = []

    paths = _python_files(root)
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
            source = raw.decode("utf-8", errors="replace")
            tree = ast.parse(source, filename=relative, type_comments=True)
            extractor = _Extractor(source, relative, _module(relative))
            extractor.visit_module(tree)
            files.append({
                "path": relative,
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "line_count": _line_count(raw),
            })
            symbols.extend(extractor.symbols)
            calls.extend(extractor.calls)
            imports.extend(extractor.imports)
            inheritance.extend(extractor.inheritance)
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append(f"{relative}: {type(exc).__name__}: {exc}")

    data = Index(
        schema_version=SCHEMA_VERSION,
        repository_root=str(root),
        generated_at=datetime.now(UTC).isoformat(),
        files=tuple(files),
        symbols=tuple(symbols),
        calls=tuple(calls),
        imports=tuple(imports),
        inheritance=tuple(inheritance),
        errors=tuple(errors),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(data.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return {
        "repository_root": str(root),
        "index_path": str(destination),
        "files_scanned": len(paths),
        "files_indexed": len(files),
        "symbols_indexed": len(symbols),
        "calls_indexed": len(calls),
        "imports_indexed": len(imports),
        "inheritance_edges": len(inheritance),
        "errors": errors,
        "duration_seconds": time.perf_counter() - started,
    }


def load_index(repo_or_file: str | Path) -> Index:
    candidate = Path(repo_or_file).expanduser().resolve()
    path = candidate if candidate.is_file() else index_path(candidate)
    if not path.exists():
        raise FileNotFoundError(f"No AST index at {path}; run codeatlas-ast index first")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Index JSON root must be an object")
    return Index.from_dict(payload)


def retrieve(
    repo: str | Path,
    query: str,
    *,
    max_tokens: int = 2000,
    depth: int = 0,
    max_symbols_per_file: int = 3,
) -> Retrieval:
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if depth < 0:
        raise ValueError("depth must be non-negative")
    started = time.perf_counter()
    root = _root(repo)
    data = load_index(root)
    query_terms = terms(query)
    views = [_view(root, symbol) for symbol in data.symbols]
    document_frequency = Counter(term for view in views for term in view["all_terms"])
    ranked = _rank(query, query_terms, views, document_frequency)
    if depth and ranked:
        ranked = _expand_graph(data, views, ranked, depth)
    snippets = _pack(ranked, max_tokens, max_symbols_per_file)
    baseline = sum((int(file["size_bytes"]) + 3) // 4 for file in data.files)
    context = sum(snippet.tokens for snippet in snippets)
    return Retrieval(
        query=query,
        snippets=snippets,
        baseline_tokens=max(baseline, context),
        context_tokens=context,
        latency_ms=(time.perf_counter() - started) * 1000,
    )


class _Extractor:
    def __init__(self, source: str, file: str, module: str) -> None:
        self.source = source
        self.file = file
        self.module = module
        self.symbols: list[Symbol] = []
        self.calls: list[Call] = []
        self.imports: list[Import] = []
        self.inheritance: list[dict[str, Any]] = []

    def visit_module(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports.append(Import(self.file, alias.name, None, alias.asname, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                for alias in node.names:
                    self.imports.append(Import(self.file, module, alias.name, alias.asname, node.lineno))
        self._body(tree.body, (), "module")

    def _body(self, body: Sequence[ast.stmt], parents: tuple[str, ...], scope: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                self._class(node, parents)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._function(node, parents, scope)

    def _class(self, node: ast.ClassDef, parents: tuple[str, ...]) -> None:
        qualified = self._qualified((*parents, node.name))
        self.symbols.append(self._symbol(node, qualified, "CLASS", parents))
        for base in node.bases:
            target = _source(self.source, base)
            if target:
                self.inheritance.append({"source": qualified, "target": target, "line": node.lineno})
        self._body(node.body, (*parents, node.name), "class")

    def _function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parents: tuple[str, ...],
        scope: str,
    ) -> None:
        qualified = self._qualified((*parents, node.name))
        kind = "METHOD" if scope == "class" else "FUNCTION"
        self.symbols.append(self._symbol(node, qualified, kind, parents))
        visitor = _CallVisitor(self.source, qualified, self.calls)
        for statement in node.body:
            visitor.visit(statement)
        self._body(node.body, (*parents, node.name), "function")

    def _symbol(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        qualified: str,
        kind: str,
        parents: tuple[str, ...],
    ) -> Symbol:
        decorators = tuple(_unparse(item) for item in node.decorator_list if _unparse(item))
        line_start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
        return Symbol(
            file=self.file,
            module=self.module,
            name=node.name,
            qualified_name=qualified,
            kind=kind,
            line_start=line_start,
            line_end=int(getattr(node, "end_lineno", node.lineno)),
            signature=_signature(node),
            docstring=ast.get_docstring(node, clean=False),
            decorators=decorators,
            parent=self._qualified(parents) if parents else None,
        )

    def _qualified(self, parts: Sequence[str]) -> str:
        return ".".join((self.module, *parts))


class _CallVisitor(ast.NodeVisitor):
    def __init__(self, source: str, owner: str, calls: list[Call]) -> None:
        self.source = source
        self.owner = owner
        self.calls = calls

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        del node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        del node

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node

    def visit_Call(self, node: ast.Call) -> None:
        display = _source(self.source, node.func)
        target = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else display.rsplit(".", 1)[-1]
        if target:
            self.calls.append(Call(self.owner, target, display, node.lineno))
        self.generic_visit(node)


def _root(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)
    return root


def _python_files(root: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in IGNORED_DIRS)
        base = Path(current)
        found.extend(base / name for name in sorted(files) if name.endswith(".py") and not (base / name).is_symlink())
    return tuple(sorted(found, key=lambda path: path.relative_to(root).as_posix()))


def _module(relative: str) -> str:
    parts = list(Path(relative).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or Path(relative).stem


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(_unparse(base) for base in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {_unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({_unparse(node.args)}){returns}"


def _source(source: str, node: ast.AST) -> str:
    return (ast.get_source_segment(source, node) or _unparse(node)).strip()


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node).strip()
    except Exception:
        return ""


def _line_count(raw: bytes) -> int:
    return 0 if not raw else raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1)


def terms(text: str) -> tuple[str, ...]:
    expanded = CAMEL_RE.sub(" ", text).replace("_", " ").replace("-", " ")
    values = []
    for match in TOKEN_RE.finditer(expanded):
        value = _stem(match.group(0).lower())
        if value and value not in STOP_WORDS:
            values.append(value)
    return tuple(values)


def _stem(value: str) -> str:
    if len(value) > 5 and value.endswith("ing"):
        return value[:-3]
    if len(value) > 4 and value.endswith("ied"):
        return value[:-3] + "y"
    if len(value) > 4 and value.endswith("ed"):
        return value[:-2]
    if len(value) > 4 and value.endswith("ies"):
        return value[:-3] + "y"
    if len(value) > 3 and value.endswith("s") and not value.endswith("ss"):
        return value[:-1]
    return value


def _view(root: Path, symbol: Symbol) -> dict[str, Any]:
    code = _lines(root / symbol.file, symbol.line_start, symbol.line_end)
    fields = {
        "name": Counter(terms(symbol.name)),
        "qualified": Counter(terms(symbol.qualified_name)),
        "signature": Counter(terms(symbol.signature)),
        "path": Counter(terms(symbol.file)),
        "doc": Counter(terms(symbol.docstring or "")),
        "decorators": Counter(terms(" ".join(symbol.decorators))),
        "code": Counter(terms(code)),
    }
    return {
        "symbol": symbol,
        "code": code,
        "fields": fields,
        "all_terms": {term for counter in fields.values() for term in counter},
    }


def _rank(query: str, query_terms: tuple[str, ...], views: Sequence[dict[str, Any]], df: Counter[str]) -> list[dict[str, Any]]:
    weights = {"name": 9, "qualified": 5, "signature": 5, "path": 3.5, "doc": 2.5, "decorators": 2.5, "code": 1}
    unique = tuple(dict.fromkeys(query_terms))
    ranked: list[dict[str, Any]] = []
    for view in views:
        score = 0.0
        matched: list[str] = []
        for query_term in unique:
            best = 0.0
            for field, counter in view["fields"].items():
                candidate = _best_match(query_term, counter)
                if candidate is None:
                    continue
                similarity = 1.0 if candidate == query_term else 0.82
                idf = math.log((len(views) + 1) / (df[candidate] + 1)) + 1
                best = max(best, weights[field] * idf * (1 + math.log1p(counter[candidate])) * similarity)
            if best:
                score += best
                matched.append(query_term)
        if unique:
            score += 12 * len(set(matched)) / len(unique)
        symbol: Symbol = view["symbol"]
        if symbol.name.lower() in query.lower():
            score += 15
        if score:
            ranked.append({**view, "score": score, "matched": tuple(dict.fromkeys(matched)), "distance": None})
    ranked.sort(key=lambda item: (-item["score"], item["symbol"].file, item["symbol"].line_start))
    return ranked


def _best_match(query: str, counter: Counter[str]) -> str | None:
    if query in counter:
        return query
    for candidate in counter:
        shared = 0
        for left, right in zip(query, candidate):
            if left != right:
                break
            shared += 1
        if min(len(query), len(candidate)) >= 4 and shared >= max(4, min(len(query), len(candidate)) - 2):
            return candidate
    return None


def _expand_graph(data: Index, views: Sequence[dict[str, Any]], ranked: list[dict[str, Any]], depth: int) -> list[dict[str, Any]]:
    by_qualified = {view["symbol"].qualified_name: view for view in views}
    by_name: dict[str, list[Symbol]] = defaultdict(list)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for symbol in data.symbols:
        by_name[symbol.name].append(symbol)
        if symbol.parent:
            adjacency[symbol.qualified_name].add(symbol.parent)
            adjacency[symbol.parent].add(symbol.qualified_name)
    for call in data.calls:
        candidates = by_name.get(call.target, [])
        if candidates:
            target = min(candidates, key=lambda symbol: (symbol.module not in call.source, len(symbol.qualified_name)))
            adjacency[call.source].add(target.qualified_name)
            adjacency[target.qualified_name].add(call.source)
    scores = {item["symbol"].qualified_name: item for item in ranked}
    queue = deque((item["symbol"].qualified_name, 0) for item in ranked[:8])
    visited = {name for name, _ in queue}
    while queue:
        current, distance = queue.popleft()
        if distance >= depth:
            continue
        for neighbor in sorted(adjacency.get(current, ())):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            queue.append((neighbor, distance + 1))
            view = by_qualified.get(neighbor)
            if view and neighbor not in scores:
                scores[neighbor] = {**view, "score": 18 / (distance + 1), "matched": (), "distance": distance + 1}
    result = list(scores.values())
    result.sort(key=lambda item: (-item["score"], item["symbol"].file, item["symbol"].line_start))
    return result


def _pack(ranked: Sequence[dict[str, Any]], max_tokens: int, max_per_file: int) -> tuple[Snippet, ...]:
    selected: list[Snippet] = []
    used = 0
    per_file: dict[str, int] = defaultdict(int)
    cap = max(64, max_tokens // 2)
    for item in ranked:
        symbol: Symbol = item["symbol"]
        if per_file[symbol.file] >= max_per_file:
            continue
        remaining = max_tokens - used
        if remaining < 32:
            break
        code = item["code"]
        allocation = min(remaining, cap)
        truncated = estimate_tokens(code) > allocation
        if truncated:
            code = _trim(code, allocation)
        tokens_used = estimate_tokens(code)
        if not code.strip() or used + tokens_used > max_tokens:
            continue
        reason = "AST symbol"
        if item["matched"]:
            reason += "; matched " + ", ".join(item["matched"][:6])
        if item["distance"] is not None:
            reason += f"; dependency distance {item['distance']}"
        if truncated:
            reason += "; body capped for context diversity"
        line_end = min(symbol.line_end, symbol.line_start + code.count("\n")) if truncated else symbol.line_end
        selected.append(Snippet(symbol.file, symbol.qualified_name, symbol.kind, symbol.line_start, line_end, item["score"], reason, code))
        per_file[symbol.file] += 1
        used += tokens_used
    return tuple(selected)


def _lines(path: Path, start: int, end: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[max(start - 1, 0): min(end, len(lines))])


def estimate_tokens(text: str) -> int:
    return 0 if not text else max(1, (len(text) + 3) // 4)


def _trim(text: str, budget: int) -> str:
    marker = "\n# ... truncated by codeatlas-ast token budget"
    limit = max(budget * 4 - len(marker), 0)
    return text[:limit].rstrip() + marker
