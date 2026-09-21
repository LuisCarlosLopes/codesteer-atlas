# AGENTS.md

Instruções para agentes neste repositório. O Claude Code recebe o mesmo texto porque [`CLAUDE.md`](CLAUDE.md) importa este arquivo.

## Busca de código (MCP codesteer-atlas)

Use as tools do MCP **antes** de `grep`, `rg`, `find`, glob ou leitura em massa.

| Objetivo | Tool |
| --- | --- |
| Se orientar num projeto desconhecido (chame primeiro, uma vez) | `atlas_brief` |
| Pacote da tarefa quando o símbolo/arquivo já é conhecido | `atlas_context` |
| Onde algo está implementado | `atlas_search` |
| Conectividade, hubs, rationale e impacto (`affected`) | `atlas_graph` |
| Diagnóstico do índice | `atlas_status` |
| (Re)indexar | `atlas_index` |

**Quando a tarefa já tem um símbolo ou arquivo**, chame `atlas_context(target, intent)` primeiro (`edit` / `debug` / `review` / `understand`) em vez de encadear `atlas_graph` + `atlas_brief`. Use `atlas_graph(mode="affected")` para o raio de impacto.

**`atlas_search` (2 passos):** retorna só metadados por padrão — localize com
`path_prefix`/`language`/`top_k` baixo; depois `Read` nas linhas ou
`include_content=true` nos poucos hits relevantes. Não chame `atlas_status` antes:
se o índice não existir, a própria tool explica como criá-lo.

`grep`/`Read` só depois que o Atlas indicar o arquivo: confirmar string ou erro
literal, ler caminho que o usuário já deu, editar, diff, commit, git, CI ou testes.
Fallback se o MCP estiver indisponível, a autenticação falhar, ou o índice estiver
vazio ou desatualizado.

Índice desatualizado: `atlas_status` com `is_stale: true` (HEAD indexado diferente
do workspace), reindexar com `atlas_index`, e só então usar `grep`/`Read` como
recorte do momento — reindexar de novo ao fim da sessão.

1. **Descoberta** → `atlas_search` (semântico + BM25; só metadados).
2. **Detalhe** → `Read` no intervalo, ou `include_content=true` nos poucos hits que importam.
3. **Literal** → `grep`/`Read` quando a string exata importa.
4. **Edição** → ferramentas de arquivo e terminal (git, testes).

## Frescor e cobertura do índice

O índice declara o que não sabe. Leia isso antes de confiar no grafo:

- `atlas_status` traz `resolution_coverage` (linguagens em `scip`, `treesitter` e
  `none`, mais `files_unresolved`) e `watch` (`active`/`disabled`/`unavailable`/`failed`).
  Índice anterior a 2.2.0 devolve `{"status": "unknown", "reason": "index_version_below_2_2_0"}`.
- `calls` carrega `origin: "scip"` e `imports` carrega `origin: "treesitter"`; `contains`,
  `cites`, `links_to` e `annotates` não carregam o campo. Ausência de `origin` = grafo pré-2.2.0. Se a linguagem
  do arquivo está em `none`, o grafo não tem as arestas `imports` dele: não conclua
  ausência de dependências.
- `ATLAS_WATCH=1` reindexa em subprocesso após o debounce e exige o extra
  `codesteer-atlas[watch]`; sem ele, `watch: "unavailable"`. `ATLAS_SCIP=1` sem toolchain
  deixa `scip_status: "toolchain_missing"` e nenhuma aresta `calls`.
- `MIN_INDEX_VERSION` continua `2.0.0`: índices 2.0.x/2.1.0 seguem buscáveis sem reindexar.

O mecanismo está na seção de resolução por tier, mais abaixo.

<!-- codesteer:constitution-precedence -->
## Precedência de governança (CodeSteer)

`.memory-bank/operational-memory.md` é **runbook** do repositório: problemas locais, gotchas e **como mitigar** — runbook entre sessões, **não** camada normativa acima da Constitution.

Em **todas** as tarefas, as regras em `.memory-bank/constitution.md` prevalecem.
<!-- /codesteer:constitution-precedence -->

## Projeto

CodeSteer Atlas: servidor MCP local de busca híbrida semântica. Indexa via Tree-sitter em chunks de símbolo (classe/função/método), gera embeddings locais com `fastembed` (ONNX, `all-MiniLM-L6-v2`, 384 dims) e guarda em LanceDB embutido. A busca combina similaridade vetorial (cosseno) e BM25, fundidas por Reciprocal Rank Fusion (RRF).

Tudo roda local e offline na configuração padrão — nenhum código-fonte sai do host. Princípios em `.memory-bank/constitution.md`.

## Comandos

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

## Arquitetura

O código fica em `src/codesteer_atlas/`:

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

### Resolução do diretório de índice (DECISAO-002)

The `.code-index` directory location is resolved at startup, in order, by `resolve_index_dir()` in `server.py`: (1) `--index-dir` CLI arg, (2) `ATLAS_INDEX_DIR` env var, (3) ascending discovery from CWD looking for a `.code-index` folder (git-style), (4) ascending discovery from the editor-provided project root (`CLAUDE_PROJECT_DIR` for Claude Code, or `WORKSPACE_FOLDER_PATHS` for Cursor/VS Code), (5) fallback to `DEFAULT_INDEX_DIR` relative to CWD (or to the editor project root, when known).

When the server is registered **globally as a plugin** (Copilot, Cursor, Kiro), it is often launched with CWD = HOME and without those editor env vars, so the startup chain lands on a fallback. To recover without any per-project config, each tool then performs a one-time, per-process **MCP roots** upgrade via `_resolve_index_dir_via_roots(ctx)`: it requests the client's workspace roots (`roots/list`) and re-resolves `.code-index` from there (`roots` when an existing index is found by ascending discovery, `roots-fallback` when none exists yet — pointing the index at `<root>/.code-index` so `atlas_index` creates it inside the project, not HOME). The roots step only runs when startup resolution landed on a fallback (never overriding `cli-arg`/`env`/`discovery`/`editor-project-dir`), is best-effort (clients without `roots` support fall back gracefully, with a `ROOTS_LIST_TIMEOUT_S` guard), and the chosen source is reported in `atlas_status` → `index_resolution`. The sync→async bridge uses `anyio.from_thread.run`, valid because FastMCP runs sync tools in a worker thread via `anyio.to_thread.run_sync`.

### Ranking pós-RRF (DECISAO-007)

`search_hybrid` fuses two arms via RRF — **vector** (cosine over MiniLM embeddings) and **fts**
(BM25 over `content`) — then reorders the result before cutting to `top_k`:

- **`ranking.rerank` reorders a pool** of `min(top_k * RERANK_POOL_MULTIPLIER, CANDIDATES_LIMIT)`
  by title/proximity/phrase boost, RRF score as tiebreak, then cuts to `top_k`. Letting the boost
  dominate (rather than merely weighting the RRF score) is deliberate: both alternatives measured
  worse across all four query classes. `ATLAS_RERANK=0` disables it.
- **`SearchResult.match_arms`** reports which arms retrieved each chunk, so a caller can tell
  consensus from a single-arm hit.

Measured on one index (1482 chunks, 28 queries): total MRR 0.306 → 0.457, recall@5 0.464 → 0.607,
no class regressing — all of it from the rerank. Pruning stopwords from the BM25 query text was
tried and dropped: its effect is ±0.002, indistinguishable from zero against a bootstrap CI of
±0.07. `QUERY_STOPWORDS` survives because `ranking.query_terms` needs it to keep generic words from
earning a title boost.

**Estágios opt-in F2 (DECISAO-008 / DECISAO-009), ambos desligados por padrão:**

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
`exact_symbol` regressed → keep opt-in; 2.2 total MRR 0.4521 but `partial_identifier`
regressed → keep opt-in. Do not promote either stage on a global mean.

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

### Resolução de relações por tier (F3, index_version 2.2.0)

A origem de cada relação é registrada **por aresta**, seguindo a hierarquia do Princípio II
(índice do toolchain → Tree-sitter). `origin` existe apenas em `calls` (sempre `"scip"`) e
`imports` (`"treesitter"`) — os kinds em que a qualidade varia; `contains`/`cites`/`links_to`/
`annotates` são exatos por construção e não carregam o campo. `origin` não influencia grau,
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
| `ATLAS_WATCH` | `watcher.py` observa o workspace e dispara reindexação incremental em subprocesso após o debounce. Extra: `codesteer-atlas[watch]` | `atlas_status` → `watch`: `unavailable` (sem o extra), `failed`, `disabled` |
| `ATLAS_SCIP` | fase `scip` da indexação invoca o indexador externo e ingere `index.scip` | `atlas_index` → `scip_status` + `scip_edges`. Sem toolchain: `toolchain_missing` e nenhuma aresta `calls`. `atlas_status` não expõe esses campos |

`CURRENT_INDEX_VERSION` é `2.3.0` (campos semânticos irmãos e `semantic.json`; imports por
namespace continuam em `files_declares`). **`MIN_INDEX_VERSION` continua `2.0.0`** — índices
legados seguem buscáveis, mas só `full=true` sem `paths` faz o rechunk integral para 2.3.0.
Não há migration nem conversão incremental.

### Camada semântica opt-in (F4)

`ATLAS_SEMANTIC=1` habilita propósito por símbolo, cache por
`(file_path, scope_name, sha256(content))`, vetor `purpose_vector` e sumários
`semantic.json`. Sem a flag, `content`/`vector`, busca, brief, context e grafo permanecem
estruturais. O braço `semantic` usa a mesma query e filtros, entra no RRF apenas quando o
índice 2.3.0 está `ready`, e `match_arms` registra somente os braços que recuperaram o chunk.
`atlas_search` não recebe parâmetro novo.

As origens seguem `sampling` MCP síncrono → `ATLAS_SEMANTIC_LOCAL_URL` →
`ATLAS_SEMANTIC_API_URL`; não existe host default. `ATLAS_SEMANTIC_API_KEY` fica somente no
header de API. `ATLAS_SEMANTIC_MODEL` ativa o contrato OpenAI-compatible (`model` +
`messages`) para APIs como OpenRouter; sem ele, o endpoint recebe o payload genérico legado.
OpenRouter usa `ATLAS_SEMANTIC_API_URL`, `ATLAS_SEMANTIC_API_KEY` e o slug explícito do modelo,
sem provider default. `atlas_status.semantic` declara `enabled`, `origin`, `egress`, `index` e
`last_generation`; falhas usam warnings e preservam a recuperação estrutural. Full/async,
CLI e watcher não atravessam `ctx` nem usam sampling. Sumários entram apenas em brief e
`understand`, cedendo primeiro ao teto de resposta. `graph.json`/`graph.html` não recebem
overlay semântico.

### Avaliador de relevância Jev (opt-in)

`ATLAS_RELEVANCE=1` (ou `true`) substitui a reordenação lexical/cross-encoder pelo
Jev no OpenRouter System One, no pool pós-RRF **antes** de `_merge_typed` e do
corte `top_k`. Default False. `ATLAS_RERANK=0` desliga Jev e o rerank local.
Lotes ≤ 24 KB (máx. 2 chamadas, timeout compartilhado 3 s). Baixa confiança
isola o candidato; falha de contrato invalida só o lote. No perfil compacto,
seleção semântica remove apenas score ≤ 0,25 com confiança ≥ 0,90. O custo
remoto sai em uma linha JSON em stderr por `atlas_search` (conhecido /
parcialmente conhecido / zero / desconhecido); `atlas_status.relevance` é
estático. Default `~typesafe/jev-latest` via `ATLAS_RELEVANCE_MODEL`. Reinicie
o MCP após mudar o `env`.

### Otimização de contexto (opt-in)

`ATLAS_CONTEXT_OPTIMIZATION=1` faz `response_profile=default` resolver para
`compact` em `atlas_search`/`atlas_context`. Independente do Jev. Compacto:
projeção enxuta, dedup por cobertura verificável, orçamento a 70%,
`atlas_expand` por id. Sem a flag (ou com `response_profile=full`), o formato
atual permanece.

### Indexação incremental (DECISAO-005 / [J])

`index_workspace()` compares per-file sha256 hashes against `manifest.files` to skip unchanged files. Changed/deleted files have their old chunks removed from LanceDB (`delete_by_file_paths`) before new chunks are appended (`append_chunks`). A full reindex (no existing manifest, or `--full` without `paths`) instead overwrites the table entirely via `store_chunks`.

### `deploy_mcp.py`

Standalone deployment script (separate from the package) that registers the MCP server in config files for Cursor, Claude Desktop, Cline, and Claude Code CLI across Windows/macOS/Linux. `--check` mode validates `CRITICAL_MODULES` import successfully and is used by `setup.sh`/`setup.ps1`.

## Convenções de código

- Comentários e docstrings em português (pt-BR), conforme `.memory-bank/constitution.md`. Comentários mínimos — só o que não é óbvio, no padrão da skill `codesteer-tagger` (1–3 tags por unidade lógica, sem recap de processo).
- Mudança de lógica no indexador ou no servidor MCP vem com teste unitário ou de integração.

## Cursor Cloud

Pacote Python puro gerido por `uv` (Python 3.11–3.13). O script de atualização do ambiente já roda `uv sync --group dev`.

- Dois entry points (ambos via `uv run`): `atlas-index` (CLI, escreve `.code-index/`) e `atlas-serve` (servidor MCP stdio de longa duração). Não há UI web, banco servidor nem serviço de rede.
- `atlas-serve` fala JSON-RPC por stdio e não encerra; lance por um cliente MCP (por exemplo fastmcp `Client` + `StdioTransport`) ou sessão em background. Não espere retorno.
- O servidor precisa de índice. `atlas_search` erra de forma acionável se `.code-index/` não existir — rode `uv run atlas-index --workspace .` antes de servir ou buscar. `.code-index/` está no gitignore.
- A primeira indexação baixa o modelo ONNX do fastembed (`all-MiniLM-L6-v2`) e precisa de rede uma vez; as seguintes são offline.
- Aponte um servidor já em execução com `ATLAS_INDEX_DIR=/workspace/.code-index` (ou `--index-dir`); senão o fallback é `.code-index` relativo ao CWD (`resolve_index_dir()` em `server.py`).
- Lint: `uv run --with ruff ruff check` puxa um ruff sem pin. Ruff novo (0.16.x) marca estilo pré-existente que o ruff pinado do CI (`.github/workflows/ci.yml`) não marca; trate esse drift como pré-existente.


<!-- atlas:response-profile -->
Em `atlas_search` e `atlas_context`, **omita `response_profile`** normalmente
(ou use `default`) para respeitar `ATLAS_CONTEXT_OPTIMIZATION` do operador.
Não envie `full` por rotina: ele sobrescreve a flag mesmo quando está ligada.
Use `full` apenas se solicitado ou se precisar de campos ausentes no compacto;
para obter conteúdo de um resultado compacto, prefira `atlas_expand(refs)`.
<!-- /atlas:response-profile -->

<!-- atlas:selective-expansion -->
Na primeira chamada de `atlas_expand`, envie somente uma ou duas refs diretamente
ligadas à pergunta. Leia a resposta antes de fazer outra expansão. Cada ref
adicional deve preencher uma lacuna concreta de evidência; não abra todos os hits
nem auxiliares e tipos apenas porque aparecem no resultado. Lotes de três a cinco
só quando a tarefa já exigir comparar esses símbolos. Pare assim que houver
informação suficiente; se `atlas_context` já atende, não repita o conteúdo.
Continue `next_ref` somente se precisar da parte restante do símbolo.
<!-- /atlas:selective-expansion -->
