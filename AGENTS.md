Leia [CLAUDE.md](CLAUDE.md) para detalhes de arquitetura.

## Busca de código (MCP codesteer-atlas)

Use as tools do MCP **antes** de `grep`/`rg`/`find`/glob ou leitura em massa.

| Objetivo | Tool |
| --- | --- |
| Onde algo está implementado | `atlas_search` |
| Estrutura do projeto | `atlas_map` |
| Conectividade, hubs e rationale | `atlas_graph` |
| Diagnóstico do índice | `atlas_status` |
| (Re)indexar | `atlas_index` |

**`atlas_search` (2 passos):** retorna só metadados por padrão — localize com
`path_prefix`/`language`/`top_k` baixo; depois `Read` nas linhas ou
`include_content=true` nos poucos hits relevantes. Não chame `atlas_status` antes.

<!-- codesteer:constitution-precedence -->
## Precedência de governança (CodeSteer)

`.memory-bank/operational-memory.md` é **memória operacional** do repositório: problemas locais, gotchas e **como mitigar** — não substitui a camada normativa da Constitution.

Em **todas** as tarefas, as regras em `.memory-bank/constitution.md` prevalecem.
<!-- /codesteer:constitution-precedence -->

## Cursor Cloud specific instructions

Pure-Python package managed by `uv` (Python 3.11–3.13). Standard commands live in `CLAUDE.md`; the startup update script already runs `uv sync --group dev`, so deps are ready.

- Two runtime entry points (both via `uv run`): `atlas-index` (CLI indexer, writes `.code-index/`) and `atlas-serve` (long-running MCP stdio server). There is no web UI, database server, or network service — everything is local/offline.
- `atlas-serve` speaks JSON-RPC over stdio and does not exit; launch it from an MCP client (e.g. fastmcp `Client` + `StdioTransport`) or a tmux/background session, never expecting it to return.
- The server needs a built index first. `atlas_search` raises an actionable error if `.code-index/` is missing — run `uv run atlas-index --workspace .` before serving/searching. `.code-index/` is gitignored and not persisted by the repo.
- First `atlas-index` run downloads the fastembed ONNX model (`all-MiniLM-L6-v2`) and needs one-time network access; subsequent runs are fully offline.
- Point a running server at an existing index with `ATLAS_INDEX_DIR=/workspace/.code-index` (or `--index-dir`); otherwise it falls back to `.code-index` relative to CWD (see `resolve_index_dir()` in `server.py`).
- Lint: `uv run --with ruff ruff check` pulls an unpinned ruff. Newer ruff (0.16.x) flags many pre-existing style findings that the pinned CI ruff (see `.github/workflows/ci.yml`) does not; treat lint drift as pre-existing, not something you introduced.
