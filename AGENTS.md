Leia [CLAUDE.md](CLAUDE.md) para detalhes de arquitetura.

## Busca de código (MCP codesteer-atlas)

Use as tools do MCP **antes** de `grep`/`rg`/`find`/glob ou leitura em massa.

| Objetivo | Tool |
| --- | --- |
| Se orientar num projeto desconhecido (chame primeiro) | `atlas_brief` |
| Pacote da tarefa quando o símbolo/arquivo já é conhecido | `atlas_context` |
| Onde algo está implementado | `atlas_search` |
| Conectividade, hubs, rationale e impacto (`affected`) | `atlas_graph` |
| Diagnóstico do índice | `atlas_status` |
| (Re)indexar | `atlas_index` |

**Quando a tarefa já tem um símbolo ou arquivo**, chame `atlas_context(target, intent)` primeiro (`edit` / `debug` / `review` / `understand`) em vez de encadear `atlas_graph` + `atlas_brief`. Use `atlas_graph(mode="affected")` para o raio de impacto.

**`atlas_search` (2 passos):** retorna só metadados por padrão — localize com
`path_prefix`/`language`/`top_k` baixo; depois `Read` nas linhas ou
`include_content=true` nos poucos hits relevantes. Não chame `atlas_status` antes.

## Frescor e cobertura do índice (2.2.0)

O índice **declara o que não sabe** — leia isso antes de confiar no grafo:

- `atlas_status` traz `resolution_coverage` (linguagens que resolvem por `scip`, por
  `treesitter` e as que **não resolvem**, em `none`, mais `files_unresolved`) e `watch`
  (`active`/`disabled`/`unavailable`/`failed`). Índice anterior a 2.2.0 devolve
  `{"status": "unknown", "reason": "index_version_below_2_2_0"}`.
- Arestas `imports` e `calls` carregam `origin` (`treesitter`/`scip`); `contains`, `cites`,
  `links_to` e `annotates` não carregam. Ausência de `origin` = grafo pré-2.2.0. Se a linguagem
  do arquivo está em `none`, o grafo **não** tem as arestas `imports` dele: não conclua
  "sem dependências" a partir disso.
- Duas flags, desligadas por padrão: `ATLAS_WATCH=1` (`watcher.py` reindexa em subprocesso após
  o debounce; exige o extra opcional `codesteer-atlas[watch]` — sem ele, `watch: "unavailable"`)
  e `ATLAS_SCIP=1` (`scip_ingest.py` ingere `index.scip`; sem toolchain instalado,
  `scip_status: "toolchain_missing"` e **nenhuma** aresta `calls` existe).
- `MIN_INDEX_VERSION` continua `2.0.0`: índices 2.0.x/2.1.0 seguem buscáveis sem reindexar.

<!-- codesteer:constitution-precedence -->
## Precedência de governança (CodeSteer)

`.memory-bank/operational-memory.md` é **runbook** do repositório: problemas locais, gotchas e **como mitigar** — runbook entre sessões, **não** camada normativa acima da Constitution.

Em **todas** as tarefas, as regras em `.memory-bank/constitution.md` prevalecem.
<!-- /codesteer:constitution-precedence -->

## Camada semântica F4

`ATLAS_SEMANTIC=1` é o único opt-in e permanece desligado por padrão. Ele gera
propósito por símbolo e sumários em `semantic.json`; `atlas_search` não recebe
parâmetro novo e só usa o braço `semantic` em índice 2.3.0 `ready`. O status sempre
expõe `semantic.enabled`, `origin`, `egress`, `index` e `last_generation`.

A cadeia é `sampling` somente em indexação MCP síncrona, depois endpoint local
explicitamente configurado e, por fim, API somente com URL explícita. CLI,
subprocesso e watcher não recebem `ctx`; falhas degradam para a estrutura e nunca
poluem stdout JSON-RPC. Índices anteriores a 2.3.0 são legados: só `full=true` sem
`paths` faz reindex/rechunk integral; não há migration nem conversão incremental.
Para APIs OpenAI-compatible, `ATLAS_SEMANTIC_MODEL` troca o request para `model` +
`messages`; OpenRouter usa `ATLAS_SEMANTIC_API_URL`, `ATLAS_SEMANTIC_API_KEY` e o
slug explícito do modelo, sem provider default.

## Cursor Cloud specific instructions

Pure-Python package managed by `uv` (Python 3.11–3.13). Standard commands live in `CLAUDE.md`; the startup update script already runs `uv sync --group dev`, so deps are ready.

- Two runtime entry points (both via `uv run`): `atlas-index` (CLI indexer, writes `.code-index/`) and `atlas-serve` (long-running MCP stdio server). There is no web UI, database server, or network service — everything is local/offline.
- `atlas-serve` speaks JSON-RPC over stdio and does not exit; launch it from an MCP client (e.g. fastmcp `Client` + `StdioTransport`) or a tmux/background session, never expecting it to return.
- The server needs a built index first. `atlas_search` raises an actionable error if `.code-index/` is missing — run `uv run atlas-index --workspace .` before serving/searching. `.code-index/` is gitignored and not persisted by the repo.
- First `atlas-index` run downloads the fastembed ONNX model (`all-MiniLM-L6-v2`) and needs one-time network access; subsequent runs are fully offline.
- Point a running server at an existing index with `ATLAS_INDEX_DIR=/workspace/.code-index` (or `--index-dir`); otherwise it falls back to `.code-index` relative to CWD (see `resolve_index_dir()` in `server.py`).
- Lint: `uv run --with ruff ruff check` pulls an unpinned ruff. Newer ruff (0.16.x) flags many pre-existing style findings that the pinned CI ruff (see `.github/workflows/ci.yml`) does not; treat lint drift as pre-existing, not something you introduced.
