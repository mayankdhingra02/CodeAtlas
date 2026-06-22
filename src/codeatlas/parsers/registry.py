from __future__ import annotations

from collections.abc import Iterable

from .base import ParserPlugin
from .javascript import JavaScriptParser
from .python import PythonParser


class ParserRegistry:
    def __init__(self, parsers: Iterable[ParserPlugin] | None = None) -> None:
        parser_list = tuple(parsers) if parsers is not None else (PythonParser(), JavaScriptParser())
        self._by_language = {parser.language: parser for parser in parser_list}

    def get(self, language: str) -> ParserPlugin:
        try:
            return self._by_language[language]
        except KeyError as exc:
            supported = ", ".join(sorted(self._by_language))
            msg = f"No parser registered for {language!r}. Supported: {supported}"
            raise ValueError(msg) from exc

    @property
    def supported_languages(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_language))
