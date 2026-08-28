"""AST-only repository indexing and retrieval."""

from .core import build_index, load_index, retrieve

__all__ = ["build_index", "load_index", "retrieve"]
__version__ = "0.1.0"
