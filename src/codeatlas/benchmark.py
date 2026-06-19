from __future__ import annotations

import time
from pathlib import Path

from .config import CodeAtlasPaths, resolve_repo_root
from .indexer import RepositoryIndexer
from .models import BenchmarkReport
from .retrieval import RetrievalEngine
from .storage import GraphStore


class Benchmarker:
    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        retrieval: RetrievalEngine | None = None,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.retrieval = retrieval or RetrievalEngine()

    def run(self, repo_path: str | Path, *, query: str | None = None) -> BenchmarkReport:
        repo_root = resolve_repo_root(repo_path)
        index_report = self.indexer.index(repo_root, incremental=False)
        benchmark_query = query or self._first_symbol_name(repo_root) or ""

        retrieval_start = time.perf_counter()
        retrieval_result = self.retrieval.retrieve(repo_root, benchmark_query)
        retrieval_latency_ms = (time.perf_counter() - retrieval_start) * 1000
        files_returned = len({snippet.file_path for snippet in retrieval_result.snippets})
        accuracy = self._accuracy_label(benchmark_query, retrieval_result.snippets)

        return BenchmarkReport(
            repo_root=repo_root,
            indexing_time_seconds=index_report.duration_seconds,
            retrieval_latency_ms=retrieval_latency_ms,
            files_scanned=index_report.files_scanned,
            files_returned=files_returned,
            estimated_tokens_before=retrieval_result.token_report.baseline_tokens,
            estimated_tokens_after=retrieval_result.token_report.optimized_tokens,
            token_reduction_percent=retrieval_result.token_report.savings_percent,
            graph_traversal_ms=retrieval_result.timings.graph_traversal_ms,
            retrieval_accuracy=accuracy,
            query=benchmark_query,
            extra={
                "files_indexed": index_report.files_indexed,
                "symbols_indexed": index_report.symbols_indexed,
                "parser_errors": list(index_report.parser_errors),
            },
        )

    def _first_symbol_name(self, repo_root: Path) -> str | None:
        store = GraphStore(CodeAtlasPaths(repo_root).database_path)
        try:
            store.initialize()
            symbols = store.all_symbols()
            if not symbols:
                return None
            return str(symbols[0]["name"])
        finally:
            store.close()

    def _accuracy_label(self, query: str, snippets: object) -> str:
        snippet_tuple = tuple(snippets)  # type: ignore[arg-type]
        if not query:
            return "not measured: repository has no indexed symbols"
        if not snippet_tuple:
            return "0 matched snippets"
        exact = sum(1 for snippet in snippet_tuple if snippet.symbol_name == query)
        return f"{len(snippet_tuple)} snippets returned, {exact} exact symbol matches"
