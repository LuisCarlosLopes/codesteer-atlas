# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Code Search with MCP Codesteer-Atlas

This repository is indexed by MCP `codesteer-atlas`. Use the MCP tools
**before** `grep`, `rg`, `find`, glob, or bulk file reading to locate
or explore code.

## Available Tools

| Purpose | Tool |
| --- | --- |
| Find where a function, class, method, or concept is implemented | `atlas_search` |
| Understand the project structure without reading entire files | `atlas_map` |
| Check if the index exists and is up-to-date | `atlas_status` |
| Reindex after major changes or outdated index | `atlas_index` |

## Best practices in `atlas_search`

- Use `path_prefix` to restrict the search to the relevant subdirectory (e.g., `src/codesteer_atlas`).
- Use `language` to filter by language when the context allows.
- Use `include_content=false` in exploratory searches to save tokens — bring
only metadata/location and only read the full content of relevant results.
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

1. **Discovery** → `atlas_search` (semantic + BM25, granularity of
class/function/method via AST, returns from most relevant to least relevant).
2. **Exact Confirmation** → `grep`/`Read` on the files indicated by Atlas.
3. **Editing** → standard tools (`Edit`, `Write`, terminal for git/tests).

## Project

CodeSteer Atlas: a local MCP (Model Context Protocol) server providing semantic hybrid code search over a codebase. It indexes source files via Tree-sitter AST parsing into symbol-level chunks (classes/functions/methods), generates embeddings locally with `fastembed` (ONNX, `all-MiniLM-L6-v2`, 384 dims), and stores them in an embedded LanceDB database. Search combines vector similarity (cosine) and BM25 full-text search, fused via Reciprocal Rank Fusion (RRF).

Everything runs 100% locally and offline — no source code is ever sent to external services (see `.memory-bank/constitution.md` for the full governing principles).

## Codebase search (MCP)

**Always use the `codesteer-atlas` MCP server** when you need to search or explore this codebase — do not rely on broad file reads, `grep`, or built-in semantic search as the primary discovery path.

| Goal                                                            | Tool           |
| --------------------------------------------------------------- | -------------- |
| Find where a function, class, method, or concept is implemented | `atlas_search` |
| Understand project structure without reading full files         | `atlas_map`    |
| Check whether the index exists and is up to date                | `atlas_status` |
| Reindex after large changes or when status reports stale        | `atlas_index`  |

Use `path_prefix`, `language`, and `include_content=false` on `atlas_search` to narrow scope and save tokens. Reserve exact literal matches (symbol names, error strings) for `grep`/`Read` only after Atlas has pointed you to the right files.

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

- **`config.py`** — central constants: `SUPPORTED_EXTENSIONS` (languages parsed by Tree-sitter), `IGNORE_DIRS`, `MIN_INDEX_VERSION`, `RRF_K`, `CANDIDATES_LIMIT`, `MAX_TOKENS_PER_CHUNK`, `DEFAULT_INDEX_DIR` (`.code-index`).
- **`chunker.py` (`ASTChunker`)** — parses files with `tree_sitter_language_pack`, walks the AST to extract `CodeChunk`s at class/function/method granularity (falling back to whole-module chunks when no parser/symbols are found), and truncates oversized chunks while preserving signatures.
- **`embeddings.py` (`EmbeddingEngine`)** — singleton, lazy-loaded `fastembed.TextEmbedding` wrapper (`FASTEMBED_MODEL_NAME = sentence-transformers/all-MiniLM-L6-v2`). Loads the model only on first `encode`/`encode_single` call to keep server startup instant.
- **`storage.py` (`StorageBackend`)** — all LanceDB interaction and `manifest.json` read/write. Owns hybrid search (`search_hybrid`): runs vector + FTS queries with prefilters, fuses results with RRF, and returns `SearchResult`s. Also handles incremental add/delete of chunks and manifest updates (`update_manifest_after_incremental`). Enforces `MIN_INDEX_VERSION` — manifests from older (sentence-transformers/torch) backends raise an actionable `RuntimeError` requiring reindex.
- **`indexer.py`** — `index_workspace()` is the reusable indexing core (used by both the CLI and the MCP `atlas_index` tool): scans the workspace (or selected `paths` subtrees, with anti-traversal validation), hashes file contents (sha256) for incremental indexing, chunks/embeds only new-or-changed files, and decides between full overwrite vs. incremental delete+append persistence. Also exposes `get_git_head_sha()` and `should_ignore()`.
- **`server.py`** — FastMCP server (`app = FastMCP("CodeSteer Atlas")`). Critically, `sys.stdout` is redirected to `stderr` at import time (before heavy deps like `lancedb`/`fastembed` load) and only restored to the real stdout in `main()` right before `app.run()`, to keep the stdio JSON-RPC channel clean. Exposes MCP tools `atlas_search`, `atlas_map`, `atlas_index` (with `dry_run` mode), `atlas_status`, and resource `atlas://status`.
- **`models.py`** — Pydantic models: `CodeChunk`, `IndexManifest`, `SearchResult`, `IndexStats`.

### Index directory resolution (DECISAO-002)

The `.code-index` directory location is resolved at startup, in order, by `resolve_index_dir()` in `server.py`: (1) `--index-dir` CLI arg, (2) `ATLAS_INDEX_DIR` env var, (3) ascending discovery from CWD looking for a `.code-index` folder (git-style), (4) ascending discovery from the editor-provided project root (`CLAUDE_PROJECT_DIR` for Claude Code, or `WORKSPACE_FOLDER_PATHS` for Cursor/VS Code), (5) fallback to `DEFAULT_INDEX_DIR` relative to CWD (or to the editor project root, when known).

When the server is registered **globally as a plugin** (Copilot, Cursor, Kiro), it is often launched with CWD = HOME and without those editor env vars, so the startup chain lands on a fallback. To recover without any per-project config, each tool then performs a one-time, per-process **MCP roots** upgrade via `_resolve_index_dir_via_roots(ctx)`: it requests the client's workspace roots (`roots/list`) and re-resolves `.code-index` from there (`roots` when an existing index is found by ascending discovery, `roots-fallback` when none exists yet — pointing the index at `<root>/.code-index` so `atlas_index` creates it inside the project, not HOME). The roots step only runs when startup resolution landed on a fallback (never overriding `cli-arg`/`env`/`discovery`/`editor-project-dir`), is best-effort (clients without `roots` support fall back gracefully, with a `ROOTS_LIST_TIMEOUT_S` guard), and the chosen source is reported in `atlas_status` → `index_resolution`. The sync→async bridge uses `anyio.from_thread.run`, valid because FastMCP runs sync tools in a worker thread via `anyio.to_thread.run_sync`.

### Incremental indexing (DECISAO-005 / [J])

`index_workspace()` compares per-file sha256 hashes against `manifest.files` to skip unchanged files. Changed/deleted files have their old chunks removed from LanceDB (`delete_by_file_paths`) before new chunks are appended (`append_chunks`). A full reindex (no existing manifest, or `--full` without `paths`) instead overwrites the table entirely via `store_chunks`.

### `deploy_mcp.py`

Standalone deployment script (separate from the package) that registers the MCP server in config files for Cursor, Claude Desktop, Cline, and Claude Code CLI across Windows/macOS/Linux. `--check` mode validates `CRITICAL_MODULES` import successfully and is used by `setup.sh`/`setup.ps1`.

## Code conventions

- Code comments and docstrings are written in Portuguese (pt-BR), per `.memory-bank/constitution.md`. Keep comments minimal — only document non-obvious logic, per the `codesteer-tagger` skill conventions (1-3 tags per logical unit, no redundant/process recap comments).
- Any logic change to the indexer or MCP server must be accompanied by unit/integration tests.
