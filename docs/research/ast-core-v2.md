# AST Core V2: Research Feasibility Contract

## Purpose

CodeAtlas currently contains a broad repository-intelligence product: parsers, a code graph, retrieval, git memory, rules, context packs, MCP tools, and a browser UI. This branch deliberately narrows the research question to the smallest claim that can be tested honestly:

> At a fixed context budget, can syntax-aware and dependency-aware retrieval select the code needed for a software-engineering task more efficiently than non-structural retrieval?

The orchestration/ChatGPT-to-Codex work is out of scope until this claim is supported by measured evidence.

## What counts as the AST core

For the first feasibility study, the core is only:

1. Parse Python source into symbols and structural relationships.
2. Resolve enough imports, calls, containment, references, and inheritance to create a useful graph.
3. Retrieve ranked files and symbols for a natural-language task under a token budget.
4. Compare the result against labeled ground truth and non-AST baselines.

Repository memory, ownership, UI, rule checks, agent installation, and visual maps may remain in the repository, but they are not evidence for the AST research claim.

## Current implementation risks

The existing implementation is a useful prototype, but it is not yet a research result:

- Python uses Tree-sitter, while JavaScript/TypeScript extraction is regex-backed.
- Cross-file call resolution is name-based and can connect the wrong symbol when names collide.
- Natural-language retrieval is optimized around direct symbol matches and otherwise falls back to file text search.
- The current benchmark labels an exact-name hit as "accuracy" without a labeled task set.
- The token estimate uses a four-characters-per-token approximation.
- There is no comparison against lexical, chunk, embedding, or language-server baselines.
- There is no downstream test showing that an AI agent succeeds with the compressed context.

These are starting conditions, not failures to hide.

## Phase 0: hard feasibility gate

### Scope

- Python only.
- Three retrieval strategies:
  - lexical/file-text baseline;
  - AST/graph-only retrieval;
  - hybrid lexical + AST graph retrieval.
- At least three repositories with different structures.
- At least 30 natural-language tasks total for the pilot.
- Ground truth must identify relevant files and, where practical, qualified symbols.

### Primary metrics

- file recall;
- symbol recall;
- mean reciprocal rank;
- context tokens returned;
- retrieval latency;
- all-required-targets-found rate.

Before publication experiments, token counts must use model-specific tokenizers in addition to the current character estimate.

### Secondary metric

After retrieval quality is established, run a small downstream coding-agent study and measure task/test success using identical models, prompts, tools, and budgets. Retrieval metrics alone cannot prove that fewer tokens preserve engineering performance.

### Proceed criteria

Continue to a larger paper only when the pilot shows at least one repeatable result across multiple repositories:

- equal or better target recall with materially fewer context tokens; or
- materially better recall at the same context budget.

The improvement must survive bootstrap confidence intervals and must not come only from exact symbol-name queries.

### Stop or redesign criteria

Stop or redesign the AST algorithm when:

- gains disappear on natural-language tasks;
- name-resolution errors dominate the graph;
- results depend on one repository or synthetic fixture;
- the lexical baseline performs equivalently with less complexity;
- token savings come from omitting required code;
- the evaluator or labels leak patch/answer information into retrieval.

## Benchmark data rules

Every case must be stored as JSONL and include:

- a stable case ID;
- the task query available to the retriever;
- expected files and/or qualified symbols;
- the context budget;
- optional provenance metadata.

Gold labels must be produced independently of the retrieval output. For patch-derived tasks, the task text may be given to the retriever, while the human patch is used only to derive hidden relevance labels.

## Initial execution

```bash
python -m codeatlas.evaluation \
  . \
  benchmarks/ast_v2/codeatlas_smoke.jsonl \
  --reindex \
  --output .codeatlas/ast-v2-smoke-report.json
```

The smoke set is only for validating the evaluator and exposing obvious retrieval weaknesses. It is not publication evidence.

## Next implementation order

1. Land the labeled evaluator and smoke dataset.
2. Record the current CodeAtlas retrieval result without tuning on the labels.
3. Implement a deterministic lexical baseline.
4. Split current retrieval into explicit AST-only and hybrid modes.
5. Improve Python symbol resolution only where pilot errors justify it.
6. Expand to real patch-derived tasks.
7. Decide GO / MODIFY / STOP from the measured results.

No UI work, additional languages, or orchestrator integration should happen before step 7.
