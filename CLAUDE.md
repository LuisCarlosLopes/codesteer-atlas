# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Code Search with MCP Codesteer-Atlas

This repository is indexed by MCP `codesteer-atlas`. Use the MCP tools
**before** `grep`, `rg`, `find`, glob, or bulk file reading to locate
or explore code.

## Available Tools

| Purpose | Tool |
| --- | --- |
| Get oriented in an unfamiliar project (call this first, once) | `atlas_brief` |
| Task pack when the symbol/file is already known | `atlas_context` |
| Find where a function, class, method, or concept is implemented | `atlas_search` |
| Inspect hubs, paths, rationale, and impact (`affected`) | `atlas_graph` |
| Check if the index exists and is up-to-date | `atlas_status` |
| Reindex after major changes or outdated index | `atlas_index` |

## Best practices in `atlas_search`

- Use `path_prefix` to restrict the search to the relevant subdirectory (e.g., `src/codesteer_atlas`).
- Use `language` to filter by language when the context allows.
- **Two-pass flow (token-efficient):** `atlas_search` returns **metadata only by default**
  (`file_path`, lines, symbol, type, score). **Locate first**, then read the exact lines
  with `Read`, or re-call with `include_content=true` only for the few results you need.
- Call `atlas_search` directly; don't call `atlas_status` "just to check" beforehand —
if the index doesn't exist, the tool itself returns an error explaining how to create it.

## When to use grep/Read/find directly

- Confirming an **exact literal** string/error (e.g., exception message, symbol name) **after** Atlas has already indicated the candidate file(s).
- The file has already been indicated with the exact path by the user — go directly with `Read`.
- Editing, diffing, committing — normal file tools.
- Git, CI, testing (`pytest`), dependency installation — always via terminal.
- MCP unavailable, authentication error, or empty/outdated index.

## Outdated Index

1. Run `atlas_status` to confirm (`is_stale: true` indicates that the indexed HEAD
differs from the current workspace HEAD).
2. If necessary, reindex with `atlas_index`.
3. Only then use `grep`/`Read` as a point-in-time fallback, and reindex again after the session.

## Flow Summary

1. **Discovery** → `atlas_search` (semantic + BM25; metadata only by default).
2. **Detail** → `Read` the returned line ranges, or `atlas_search` with `include_content=true` for specific hits.
3. **Exact confirmation** → `grep`/`Read` for literal strings when needed.
4. **Editing** → standard tools (`Edit`, `Write`, terminal for git/tests).

## Project

CodeSteer Atlas: a local MCP (Model Context Protocol) server providing semantic hybrid code search over a codebase. It indexes source files via Tree-sitter AST parsing into symbol-level chunks (classes/functions/methods), generates embeddings locally with `fastembed` (ONNX, `all-MiniLM-L6-v2`, 384 dims), and stores them in an embedded LanceDB database. Search combines vector similarity (cosine) and BM25 full-text search, fused via Reciprocal Rank Fusion (RRF).

Everything runs 100% locally and offline — no source code is ever sent to external services (see `.memory-bank/constitution.md` for the full governing principles).

## Codebase search (MCP)

**Always use the `codesteer-atlas` MCP server** when you need to search or explore this codebase — do not rely on broad file reads, `grep`, or built-in semantic search as the primary discovery path.

| Goal                                                            | Tool           |
| --------------------------------------------------------------- | -------------- |
| Get oriented in an unfamiliar project (call first, once)        | `atlas_brief`  |
| Task pack when the symbol/file is already known                 | `atlas_context` |
| Find where a function, class, method, or concept is implemented | `atlas_search` |
| Inspect hubs, paths, rationale, and impact (`affected`)         | `atlas_graph`  |
| Check whether the index exists and is up to date                | `atlas_status` |
| Reindex after large changes or when status reports stale        | `atlas_index`  |

Use `path_prefix` and `language` on `atlas_search` to narrow scope. By default it returns
metadata only — read the indicated lines with `Read`, or pass `include_content=true` when
you need chunk content in the response. Reserve exact literal matches (symbol names, error
strings) for `grep`/`Read` only after Atlas has pointed you to the right files.

## Commands

```bash
# Setup (idempotent bootstrap: uv sync + critical import check)
./setup.sh          # macOS/Linux
./setup.ps1         # Windows

# Index a workspace (incremental by default; --full forces full rebuild)
uv run atlas-index --workspace .
uv run atlas-index --workspace . --full
uv run atlas-index --workspace . --paths src --paths docs

# Run the MCP server (stdio transport)
uv run atlas-serve
uv run atlas-serve --index-dir /path/to/.code-index

# Run tests
uv run --python 3.12 --with pytest python -m pytest
uv run pytest -v
uv run pytest tests/test_indexer.py::test_name   # single test

# Lint
uv run ruff check

# Validate critical dependency imports (used by setup scripts)
uv run python deploy_mcp.py --check

# Deploy/register the MCP server with editors (Cursor, Claude Desktop, Cline, Claude Code CLI)
uv run python deploy_mcp.py
```

## Architecture

Source lives under `src/codesteer_atlas/`:

- **`config.py`** — central constants: `SUPPORTED_EXTENSIONS` (languages parsed by Tree-sitter), `IGNORE_DIRS`, `MIN_INDEX_VERSION`, `RRF_K`, `CANDIDATES_LIMIT`, `MAX_TOKENS_PER_CHUNK`, `DEFAULT_INDEX_DIR` (`.code-index`), `IMPORT_RESOLUTION_TIERS` (tier de resolução por linguagem), `WATCH_*`/`SCIP_*` (flags e tetos da F3).
- **`chunker.py` (`ASTChunker`)** — parses files with `tree_sitter_language_pack`, walks the AST to extract `CodeChunk`s at class/function/method granularity (falling back to whole-module chunks when no parser/symbols are found), and truncates oversized chunks while preserving signatures.
- **`embeddings.py` (`EmbeddingEngine`)** — singleton, lazy-loaded `fastembed.TextEmbedding` wrapper (`FASTEMBED_MODEL_NAME = sentence-transformers/all-MiniLM-L6-v2`). Loads the model only on first `encode`/`encode_single` call to keep server startup instant.
- **`storage.py` (`StorageBackend`)** — all LanceDB interaction and `manifest.json` read/write. Owns hybrid search (`search_hybrid`): runs vector + FTS queries with prefilters, fuses results with RRF, and returns `SearchResult`s. Also handles incremental add/delete of chunks and manifest updates (`update_manifest_after_incremental`). Enforces `MIN_INDEX_VERSION` — manifests from older (sentence-transformers/torch) backends raise an actionable `RuntimeError` requiring reindex.
- **`indexer.py`** — `index_workspace()` is the reusable indexing core (used by both the CLI and the MCP `atlas_index` tool): scans the workspace (or selected `paths` subtrees, with anti-traversal validation), hashes file contents (sha256) for incremental indexing, chunks/embeds only new-or-changed files, and decides between full overwrite vs. incremental delete+append persistence. Also exposes `get_git_head_sha()` and `should_ignore()`.
- **`graph.py`** — rebuild completo do `graph.json`, resolução de imports/cites, métricas de hubs, `explain` capado, `affected` (BFS reversa) e predicado `is_noise_hub`.
- **`context.py`** — monta o pacote `atlas_context(target, intent)` sob cotas por seção e teto `CONTEXT_RESPONSE_MAX_CHARS`, sem embeddings.
- **`viewer.py`** — gera `graph.html` autocontido em `.code-index/`, com dados embutidos para abrir via `file://`; esmaece `noise_hub_ids`. O traço da aresta declara o `origin` (`scip` sólida, `treesitter` tracejada, ausente pontilhada) e a legenda lista os tiers de `resolution_coverage`, inclusive as linguagens que não resolvem.
- **`watcher.py`** — watcher de workspace (`ATLAS_WATCH=1`, desligado por padrão). Thread daemon do `watchdog` (extra opcional `[watch]`, importado preguiçosamente dentro de `start_watcher_if_enabled`) que filtra eventos por `should_ignore` + `.atlasignore`, coalesce a rajada em `WATCH_DEBOUNCE_S` e delega a reindexação ao **subprocesso** — nunca indexa in-process. Estado declarado em `atlas_status` → `watch` (`active`/`disabled`/`unavailable`/`failed`).
- **`scip_ingest.py`** — ingestão de `index.scip` (`ATLAS_SCIP=1`, desligada por padrão): detecta o toolchain por linguagem (`SCIP_INDEXERS`), invoca-o em subprocesso com `SCIP_TIMEOUT_S` e lê o wire format do protobuf com leitor próprio (nenhuma dependência nova). É o **único** produtor de arestas `kind: "calls"`, todas com `origin: "scip"`. Degrada em `scip_status` (`ok`/`disabled`/`toolchain_missing`/`timeout`/`parse_failed`) sem interromper a indexação.
- **`brief.py`** — gera `brief.json` (briefing ranqueado do projeto: identidade, camadas, entrypoints, hubs) consumido por `atlas_brief`. Deriva tudo de `manifest` + `graph.json`, **sem tocar o `StorageBackend`**, o que mantém a recomputação sob demanda barata. Todas as listas são capadas pelas constantes `BRIEF_*`, e `render_brief` impõe o teto de caracteres como pós-condição. Hubs usam o mesmo `is_noise_hub` do grafo.
- **`server.py`** — FastMCP server (`app = FastMCP("CodeSteer Atlas")`). Critically, `sys.stdout` is redirected to `stderr` at import time (before heavy deps like `lancedb`/`fastembed` load) and only restored to the real stdout in `main()` right before `app.run()`, to keep the stdio JSON-RPC channel clean. Exposes MCP tools `atlas_search`, `atlas_brief`, `atlas_context`, `atlas_graph`, `atlas_index` (with `dry_run` mode), `atlas_status`, and resource `atlas://status`.
- **`models.py`** — Pydantic models: `CodeChunk`, `IndexManifest`, `SearchResult`, `IndexStats`.

### Index directory resolution (DECISAO-002)

The `.code-index` directory location is resolved at startup, in order, by `resolve_index_dir()` in `server.py`: (1) `--index-dir` CLI arg, (2) `ATLAS_INDEX_DIR` env var, (3) ascending discovery from CWD looking for a `.code-index` folder (git-style), (4) ascending discovery from the editor-provided project root (`CLAUDE_PROJECT_DIR` for Claude Code, or `WORKSPACE_FOLDER_PATHS` for Cursor/VS Code), (5) fallback to `DEFAULT_INDEX_DIR` relative to CWD (or to the editor project root, when known).

When the server is registered **globally as a plugin** (Copilot, Cursor, Kiro), it is often launched with CWD = HOME and without those editor env vars, so the startup chain lands on a fallback. To recover without any per-project config, each tool then performs a one-time, per-process **MCP roots** upgrade via `_resolve_index_dir_via_roots(ctx)`: it requests the client's workspace roots (`roots/list`) and re-resolves `.code-index` from there (`roots` when an existing index is found by ascending discovery, `roots-fallback` when none exists yet — pointing the index at `<root>/.code-index` so `atlas_index` creates it inside the project, not HOME). The roots step only runs when startup resolution landed on a fallback (never overriding `cli-arg`/`env`/`discovery`/`editor-project-dir`), is best-effort (clients without `roots` support fall back gracefully, with a `ROOTS_LIST_TIMEOUT_S` guard), and the chosen source is reported in `atlas_status` → `index_resolution`. The sync→async bridge uses `anyio.from_thread.run`, valid because FastMCP runs sync tools in a worker thread via `anyio.to_thread.run_sync`.

### Search ranking: post-RRF rerank + lexical stopword pruning (DECISAO-007)

`search_hybrid` fuses two arms via RRF — **vector** (cosine over MiniLM embeddings) and **fts**
(BM25 over `content`) — then reorders the result before cutting to `top_k`:

- **`ranking.rerank` reorders a pool** of `min(top_k * RERANK_POOL_MULTIPLIER, CANDIDATES_LIMIT)`
  by title/proximity/phrase boost, RRF score as tiebreak, then cuts to `top_k`. Letting the boost
  dominate (rather than merely weighting the RRF score) is deliberate: both alternatives measured
  worse across all four query classes. `ATLAS_RERANK=0` disables it.
- **`SearchResult.match_arms`** reports which arms retrieved each chunk, so a caller can tell
  consensus from a single-arm hit.

Measured on one index (1482 chunks, 28 queries): total MRR 0.306 → 0.457, recall@5 0.464 → 0.607,
no class regressing — **all of it from the rerank**. Pruning stopwords from the BM25 query text was
tried and dropped: its effect is ±0.002, indistinguishable from zero against a bootstrap CI of
±0.07. `QUERY_STOPWORDS` survives because `ranking.query_terms` needs it to keep generic words from
earning a title boost.

**F2 opt-in stages (DECISAO-008 / DECISAO-009), both off by default:**

- **`ATLAS_RERANK_MODEL`** — when set, `reranker.CrossEncoderReranker` (fastembed
  `TextCrossEncoder`, lazy singleton) reorders the post-RRF pool. Absent → `ranking.rerank`
  unchanged. Load failure → `warnings: cross_encoder_unavailable` and lexical fallback.
  `ATLAS_RERANK=0` still disables **all** reordering, including the cross-encoder.
- **`atlas_search(..., structural=True)`** — adds a third RRF arm (`graph`) via spreading
  activation over `graph.json`. Default `False`. Missing graph → `warnings:
  structural_arm_unavailable` (no-op). The arm only re-ranks chunks already in the pool.
  `structural.is_noise_hub` is the single noise predicate (expansion only; F1 §1.3 should reuse it).

Gate is `tests/eval/baseline.json` recaptured on a clean `--full` index (1644 chunks, lexical
rerank ON, structural OFF): total MRR **0.4289**, not the historical 0.3057 (RRF-only) nor the
roadmap's 0.605 (contaminated FTS). Measured on that same index: 2.1 total MRR 0.4391 but
`exact_symbol` regressed → **keep opt-in**; 2.2 total MRR 0.4521 but `partial_identifier`
regressed → **keep opt-in**. Do not promote either stage on a global mean.

**Any change to ranking must be measured**, not argued: run `scripts/eval_search.py` against
`tests/eval/golden_queries.yaml` and compare per class to `tests/eval/baseline.json`. A global mean
hides a change that helps literal matching while hurting natural language — which is exactly what
happened twice while building this. `tests/eval/` is excluded via `.atlasignore` so the answer key
never enters the corpus being searched.

Two traps this eval already fell into, both worth re-reading before trusting a number:

1. **`query_type="fts"` without `fts_columns` searches every FTS-indexed column.** A discarded
   prototype left a second FTS index on the table, and every "before" measurement taken against
   that index was silently inflated. Re-capture `baseline.json` on an index free of the
   experiment's artifacts, not merely with the code reverted.
2. **The golden set targets this repo**, so the corpus moves whenever the source does. Compare only
   old-vs-new on one index, and prefer a frozen external corpus for serious ranking work.

### Resolução de relações por tier e frescor do índice (F3, index_version 2.2.0)

A origem de cada relação é registrada **por aresta**, seguindo a hierarquia do Princípio II
(índice do toolchain → Tree-sitter). `origin` existe apenas em `calls` (sempre `"scip"`) e
`imports` (`"treesitter"`) — os kinds em que a qualidade varia; `contains`/`cites`/`links_to`/
`annotates` são exatos por construção e não carregam o campo. `origin` **não** influencia grau,
`top_hubs` nem `is_noise_hub`.

`config.IMPORT_RESOLUTION_TIERS` classifica **toda** linguagem de `SUPPORTED_EXTENSIONS` em
exatamente um tier (`scip`, `treesitter`, `none`); `tests/test_resolution_coverage.py` falha
quando uma extensão nova entra sem decisão de tier. O bloco `resolution_coverage` do
`graph.json` (também em `atlas_status`) lista as linguagens **presentes no índice** por tier
mais `files_unresolved` — grafo anterior a 2.2.0 devolve
`{"status": "unknown", "reason": "index_version_below_2_2_0"}`, nunca listas vazias.

Duas variáveis de ambiente, ambas **desligadas por padrão** (`= "1"` liga):

| Variável | Efeito | Degradação declarada |
| --- | --- | --- |
| `ATLAS_WATCH` | `watcher.py` observa o workspace e dispara reindexação incremental em subprocesso após o debounce | `atlas_status` → `watch`: `unavailable` (sem o extra `[watch]`), `failed`, `disabled` |
| `ATLAS_SCIP` | fase `scip` da indexação invoca o indexador externo e ingere `index.scip` | `atlas_index` → `scip_status` + `scip_edges` (`atlas_status` não expõe esses campos) |

`CURRENT_INDEX_VERSION` é `2.2.0` (campo `files_declares` no manifest, usado para resolver
imports por namespace em Java/C#/Kotlin/Scala). **`MIN_INDEX_VERSION` continua `2.0.0`** — não
há reindexação forçada.

### Incremental indexing (DECISAO-005 / [J])

`index_workspace()` compares per-file sha256 hashes against `manifest.files` to skip unchanged files. Changed/deleted files have their old chunks removed from LanceDB (`delete_by_file_paths`) before new chunks are appended (`append_chunks`). A full reindex (no existing manifest, or `--full` without `paths`) instead overwrites the table entirely via `store_chunks`.

### `deploy_mcp.py`

Standalone deployment script (separate from the package) that registers the MCP server in config files for Cursor, Claude Desktop, Cline, and Claude Code CLI across Windows/macOS/Linux. `--check` mode validates `CRITICAL_MODULES` import successfully and is used by `setup.sh`/`setup.ps1`.

## Code conventions

- Code comments and docstrings are written in Portuguese (pt-BR), per `.memory-bank/constitution.md`. Keep comments minimal — only document non-obvious logic, per the `codesteer-tagger` skill conventions (1-3 tags per logical unit, no redundant/process recap comments).
- Any logic change to the indexer or MCP server must be accompanied by unit/integration tests.
