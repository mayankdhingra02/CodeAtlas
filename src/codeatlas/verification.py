from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import resolve_repo_root
from .memory import changed_files, component_for_path
from .scanner import iter_source_files


def verification_plan(
    repo_path: str | Path,
    *,
    base_ref: str = "HEAD",
    task: str = "",
) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_path)
    warnings: list[str] = []
    try:
        changed = list(changed_files(repo_root, base_ref=base_ref))
    except Exception as exc:
        changed = []
        warnings.append(f"Could not inspect git changes: {exc}")

    source_paths = sorted(source.relative_path for source in iter_source_files(repo_root))
    test_paths = [path for path in source_paths if is_test_path(path)]
    impacted: list[dict[str, Any]] = []
    selected_tests: list[str] = []
    for status, file_path in changed:
        related = related_tests_for(file_path, test_paths)
        selected_tests.extend(related)
        impacted.append(
            {
                "status": status,
                "file_path": file_path,
                "component": component_for_path(file_path),
                "related_tests": related,
                "reason": impact_reason(status, file_path, related),
            }
        )

    selected_tests = list(dict.fromkeys(selected_tests))
    commands = verification_commands(repo_root, selected_tests, source_paths)
    if not changed:
        warnings.append("No changed files were detected; plan falls back to repository-level checks.")
    if not selected_tests and test_paths:
        warnings.append("No exact test matches found; run the broader test commands.")

    return {
        "task": task.strip(),
        "base_ref": base_ref,
        "changed_files": [item["file_path"] for item in impacted],
        "impacted_files": impacted,
        "test_files": selected_tests,
        "commands": commands,
        "warnings": warnings,
    }


def related_tests_for(file_path: str, test_paths: list[str], *, limit: int = 8) -> list[str]:
    if is_test_path(file_path):
        return [file_path]
    path = Path(file_path)
    stem = path.stem.removeprefix("test_").removesuffix("_test")
    component = component_for_path(file_path)
    matches: list[tuple[int, str]] = []
    for test_path in test_paths:
        test = Path(test_path)
        score = 0
        test_stem = test.stem.removeprefix("test_").removesuffix("_test")
        if test_stem == stem:
            score += 5
        if stem and stem in test_stem:
            score += 3
        if component and component_for_path(test_path) == component:
            score += 2
        if path.parent.name and path.parent.name in test.parts:
            score += 1
        if score:
            matches.append((score, test_path))
    matches.sort(key=lambda item: (-item[0], item[1]))
    return [path for _, path in matches[:limit]]


def verification_commands(repo_root: Path, selected_tests: list[str], source_paths: list[str]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    has_python = any(path.endswith(".py") for path in source_paths)
    has_js = any(path.endswith((".js", ".jsx", ".ts", ".tsx")) for path in source_paths)
    py_tests = [path for path in selected_tests if path.endswith(".py")]
    js_tests = [path for path in selected_tests if path.endswith((".js", ".jsx", ".ts", ".tsx"))]

    if py_tests:
        commands.append(
            {
                "command": "python -m pytest " + " ".join(py_tests),
                "reason": "Targeted Python tests related to changed files.",
            }
        )
    elif has_python and (repo_root / "tests").exists():
        commands.append(
            {
                "command": "python -m unittest discover",
                "reason": "Broad Python test fallback because no exact test file matched.",
            }
        )

    if js_tests:
        commands.append(
            {
                "command": "npm test -- " + " ".join(js_tests),
                "reason": "Targeted JavaScript/TypeScript tests related to changed files.",
            }
        )
    elif has_js and (repo_root / "package.json").exists():
        commands.append(
            {
                "command": "npm test",
                "reason": "JavaScript/TypeScript project test fallback.",
            }
        )

    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8", errors="replace") if (repo_root / "pyproject.toml").exists() else ""
    if "ruff" in pyproject:
        commands.append({"command": "python -m ruff check .", "reason": "Configured Python lint check."})
    if (repo_root / "package.json").exists():
        commands.append({"command": "npm run lint", "reason": "Common JavaScript/TypeScript lint check."})
    if not commands:
        commands.append({"command": "codeatlas index . --incremental", "reason": "Refresh the index and inspect diagnostics."})
    return commands


def is_test_path(file_path: str) -> bool:
    lower = file_path.lower()
    name = Path(lower).name
    return (
        "/test/" in lower
        or "/tests/" in lower
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def impact_reason(status: str, file_path: str, related_tests: list[str]) -> str:
    if status.startswith("D"):
        return "Deleted file; run related and broader regression tests."
    if is_test_path(file_path):
        return "Changed test file; verify the test still exercises the intended behavior."
    if related_tests:
        return "Related tests matched by component and filename."
    return "No direct test match found; use broader commands and inspect callers."
