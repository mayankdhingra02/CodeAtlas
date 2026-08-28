# AST V2 Retrieval Benchmarks

Each dataset is JSONL. One line represents one retrieval task.

```json
{
  "id": "unique-case-id",
  "query": "natural-language task given to the retriever",
  "expected_files": ["relative/path.py"],
  "expected_symbols": ["module.Class.method"],
  "max_tokens": 2000,
  "depth": 2,
  "metadata": {
    "source": "manual-smoke"
  }
}
```

At least one of `expected_files` or `expected_symbols` is required. Paths are repository-relative. Symbols should use the exact qualified names stored by the CodeAtlas index.

Run a dataset with:

```bash
python -m codeatlas.evaluation /path/to/repo /path/to/cases.jsonl --reindex
```

The included CodeAtlas smoke set validates the measurement pipeline. Do not use it to claim research performance because it is small and created from the same repository being developed.
