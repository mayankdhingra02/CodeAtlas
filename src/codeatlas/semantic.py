from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SemanticAnalysisReport:
    available: bool
    command: str | None
    diagnostics: tuple[dict[str, Any], ...]
    raw_summary: dict[str, Any]


class PyrightSemanticAnalyzer:
    """Optional Pyright/BasedPyright integration for richer semantic diagnostics."""

    def __init__(self, command: str | None = None) -> None:
        self.command = command or shutil.which("basedpyright") or shutil.which("pyright")

    def analyze(self, repo_root: Path) -> SemanticAnalysisReport:
        if self.command is None:
            return SemanticAnalysisReport(
                available=False,
                command=None,
                diagnostics=(),
                raw_summary={"message": "basedpyright or pyright is not installed"},
            )
        process = subprocess.run(
            [self.command, "--outputjson", str(repo_root)],
            check=False,
            capture_output=True,
            text=True,
        )
        if not process.stdout.strip():
            return SemanticAnalysisReport(
                available=True,
                command=self.command,
                diagnostics=(),
                raw_summary={"returncode": process.returncode, "stderr": process.stderr},
            )
        payload = json.loads(process.stdout)
        diagnostics = tuple(payload.get("generalDiagnostics", ()))
        return SemanticAnalysisReport(
            available=True,
            command=self.command,
            diagnostics=diagnostics,
            raw_summary=payload.get("summary", {}),
        )
