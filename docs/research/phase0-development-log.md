# AST Core V2: Phase 0 Development Log

## Decision

**Pipeline GO; research claim not yet GO.**

The branch now performs real Python AST extraction, produces symbol-bounded context, evaluates multiple retrieval strategies on a frozen repository snapshot, and archives auditable reports. A five-case development smoke shows enough signal to justify a held-out pilot, but it is too small and too exposed to development decisions to support a paper claim.

## Scope

The Phase 0 question is deliberately narrow:

> At a fixed context budget, can syntax-aware retrieval cover the code required by a natural-language software task more reliably than file-level retrieval?

This phase is Python-only. Dependency-graph expansion, embeddings, model orchestration, UI work, additional languages, and downstream coding-agent experiments remain outside this development smoke.

## Frozen setup

- Research branch: `research/ast-core-v2`
- Validated implementation commit: `0b4c936549bc8a60fa231734ffe5629bb2f21ad0`
- Frozen subject commit: `1ab5c09e3cd58d276c2ca253c3ca9ff03f211611`
- GitHub Actions run: `33202017139`
- Raw artifact ID: `9698152924`
- Artifact digest: `sha256:8d0a00fec0a5ab4f3aa2176587c20aee2eae652d677cde7e66702e096213e4e2`
- Dataset: `benchmarks/ast_v2/codeatlas_smoke.jsonl`
- Cases: five manually labeled development queries

The workflow unpacks the frozen commit into a separate directory and asserts that the research retriever and its tests do not exist in the benchmark subject. The benchmark prompts and implementation therefore cannot be retrieved from the subject tree.

## Problems found before measuring retrieval

### 1. Native parser instability

The original Python Tree-sitter path crashed inside native bindings. A bytes-only fix removed one crash, but the native tree walk still failed. The JavaScript/TypeScript regex parser also crashed while indexing the repository's large frontend source.

For the feasibility gate, Python extraction was replaced with the standard-library `ast` module and the research branch was scoped explicitly to Python. This is deterministic, testable, and sufficient for testing the retrieval hypothesis. It is not a claim that standard-library AST extraction is novel.

### 2. Existing benchmark was not a benchmark

The original benchmark rewarded exact-name lookup and reported token savings without proving that required code was retained. Phase 0 added labeled JSONL cases, raw per-case traces, errors, file recall, rank, token count, and latency.

### 3. Leakage in the first AST run

The first apparent AST improvement was discarded because the subject repository contained research tests whose wording overlapped the development queries. All accepted development measurements now use the frozen pre-research subject commit.

### 4. Metric bias toward AST output

Exact qualified-symbol recall structurally favors AST snippets because a lexical file chunk does not carry a qualified symbol identifier. The evaluator now resolves each gold symbol to its file and line range, then gives every strategy equal credit when returned lines cover the target definition. Exact symbol identity remains diagnostic only.

The strategy-neutral primary metrics are:

- target-definition location recall;
- target-body line coverage;
- location reciprocal rank;
- all-target-locations-found rate;
- context tokens and latency.

Unit tests explicitly verify that a plain file chunk receives full location credit when its lines cover the gold function despite having zero exact-symbol recall.

## Development strategies

1. **Current CodeAtlas retrieval** — existing direct-symbol/FTS behavior.
2. **Lexical chunk baseline** — deterministic overlapping file chunks with normalized identifier terms and the same case-specific token budget.
3. **AST symbol V1** — query-conditioned ranking of AST-delimited symbols using names, qualified names, signatures, paths, decorators, docstrings, and body terms. No graph expansion or embeddings are used.

## Validated development results

| Strategy | Target-location recall | All target locations | Mean body coverage | Location MRR | Mean tokens | Mean latency |
|---|---:|---:|---:|---:|---:|---:|
| Current CodeAtlas | 0.20 | 0.20 | 0.328 | 0.20 | 1,055.8 | 13.4 ms |
| Lexical chunks | 0.40 | 0.40 | 0.410 | 0.30 | 1,774.0 | 162.7 ms |
| AST symbols | 1.00 | 1.00 | 0.847 | 0.65 | 1,767.8 | 272.3 ms |

The raw JSON reports are archived with this log. The development smoke indicates that syntax boundaries can substantially improve target coverage at roughly the lexical baseline's context budget. It also exposes a current weakness: the AST implementation recomputes symbol lexical features per query and is slower than both alternatives.

## What these numbers do not establish

They do **not** establish that CodeAtlas is publication-ready or that AST retrieval generally outperforms lexical, embedding, or production code-search systems because:

- there are only five cases;
- all cases come from one repository;
- the cases were used while developing and debugging the method;
- one query was clarified before the development set was frozen because its original wording did not match its class-level gold label;
- token counts still use the prototype four-characters-per-token approximation;
- no embedding, BM25, language-server, or commercial code-search baseline is present;
- no downstream coding-agent task has been attempted;
- the AST ranking algorithm itself is currently a straightforward syntax-aware baseline, not yet a defensible novel contribution.

## Hard held-out gate

The algorithm and development set should now be frozen. Continue only through a held-out pilot with:

- three external Python repositories pinned to immutable commits;
- at least ten natural-language tasks per repository;
- task text sourced independently from retrieval outputs, preferably issue or pull-request problem statements;
- hidden file and symbol labels derived from the human patch and verified manually;
- no parameter tuning on held-out cases;
- current, lexical/BM25, embedding, AST-only, and later AST-plus-graph conditions under matched token budgets;
- model-specific tokenizer counts;
- paired bootstrap confidence intervals and per-repository reporting.

After retrieval quality is established, a small coding-agent study must use the same model, prompt, tools, and budget for every strategy and measure tests passed or task success. Retrieval recall alone cannot prove that compressed context preserves engineering performance.

## Stop or redesign conditions

Stop or materially redesign the project if the held-out pilot shows any of the following:

- syntax-aware gains disappear outside CodeAtlas;
- gains are explained by symbol names appearing verbatim in task text;
- a standard lexical or embedding baseline matches the AST method at lower complexity;
- context savings come from omitting code needed by the downstream task;
- dependency-graph expansion adds tokens without improving held-out success;
- results depend on one repository or one task category;
- gold labels cannot be produced without leaking patch information into the query.

## Next research contribution to test

`ast-symbol-v1` is a baseline, not the intended paper contribution. The candidate contribution is a **budgeted structural context selector** that begins with query-relevant AST symbols and selects a minimal dependency-closed subgraph under a token constraint. It should be added only after the held-out syntax-only baseline is fixed, so any graph benefit can be measured rather than assumed.
