# Changelog

Todas as mudanças relevantes deste projeto são documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Unreleased]

Fase 1 omitida do corte 2.2.0, mais as camadas 4 (semântica opt-in) e 5
(arqueologia de Git). Sem flag nova para a história: a janela é constante
(`GIT_HISTORY_MAX_COMMITS_PER_FILE` / `GIT_HISTORY_MAX_MONTHS`). Watcher, SCIP,
rerank e o braço estrutural já estão em [2.2.0].

### Added

- **`atlas_context`** — pacote da tarefa em uma chamada (`target` + `intent`
  ∈ `edit`/`debug`/`review`/`understand`), com teto `CONTEXT_RESPONSE_MAX_CHARS`.
  Substitui encadear `atlas_graph` + `atlas_brief` quando o símbolo já é conhecido.
- **`atlas_graph(mode="affected")`** — raio de impacto por BFS reversa. Um commit
  é história, não dependência: `affected` e `hubs` ignoram nós `commit` e arestas
  `touches` (não entram no grau).
- **Camada semântica opt-in** (`semantic.py`), ligada só com `ATLAS_SEMANTIC=1`.
  Cadeia de origem: sampling MCP síncrono → `ATLAS_SEMANTIC_LOCAL_URL` →
  `ATLAS_SEMANTIC_API_URL`. `ATLAS_SEMANTIC_API_KEY` vai só no header;
  `ATLAS_SEMANTIC_MODEL` troca o payload para `model` + `messages` (OpenAI-compatible /
  OpenRouter). Sem origem, o índice estrutural permanece completo.
  `atlas_status.semantic` declara `enabled`, `origin`, `egress`, `index` e
  `last_generation`. Warnings de busca: `semantic_layer_unavailable` e
  `semantic_arm_unavailable` (vector+FTS seguem disponíveis).
- **Arqueologia de Git** (local, sem env). A indexação lê a janela do repositório
  e publica `.code-index/history.json` + tabela `commits_*`. `atlas_search` pode
  devolver hits `type="commit"` / `language="git"`. `atlas_context(intent="debug")`
  projeta `sections.recent_history` (commits ligados por `touches`). Degradação
  declarada: `git_history_unavailable`, `git_history_partial`, `git_history_stale`,
  `git_history_empty`. `atlas_index` reporta `git_history_status`,
  `git_history_commits` e `git_history_touches`.

### Changed

- `CURRENT_INDEX_VERSION` passa de `2.2.0` para **`2.3.0`** (campos `purpose` /
  `purpose_hash` / `purpose_vector` e sidecar `semantic.json`).
  **`MIN_INDEX_VERSION` continua `2.0.0`**: índices 2.0.x–2.2.x seguem buscáveis.
  Converter para 2.3.0 exige `atlas-index --full` sem `--paths` — recorte
  incremental não mistura schemas.

## [2.2.0] - 2026-08-30

Fase 3 do roadmap — "o índice reflete a realidade". Watcher, grafo de chamadas via
SCIP e imports multi-linguagem. As duas capacidades novas entram **desligadas por
padrão**: sem as flags, o comportamento é idêntico ao de 2.1.x. Esta versão também
publica os estágios opt-in da Fase 2 (recuperação medida), já mesclados na branch
antes do corte.

### Added

- **Watcher de workspace** (`watcher.py`), ativado por `ATLAS_WATCH=1`. Observa o
  workspace numa thread daemon do `watchdog`, coalesce a rajada de eventos em uma
  janela de `WATCH_DEBOUNCE_S` (2 s) e delega a reindexação incremental ao mesmo
  subprocesso já usado no startup — nunca indexa dentro do processo do servidor.
  Eventos filtrados por `should_ignore` + `.atlasignore`, o que inclui `.code-index/`
  e impede o loop de auto-reindexação. `watchdog` é um **extra opcional**
  (`pip install "codesteer-atlas[watch]"`); ausente, o servidor sobe igual e
  `atlas_status` reporta `watch: "unavailable"`.
- **Ingestão SCIP** (`scip_ingest.py`), ativada por `ATLAS_SCIP=1`. Detecta o
  indexador da linguagem (`scip-python`, `scip-typescript`, `scip-go`,
  `rust-analyzer`), invoca-o em subprocesso com timeout e lê o `index.scip` com um
  leitor próprio do wire format do protobuf — **sem dependência nova**. Produz as
  primeiras arestas `kind: "calls"` do grafo, com `origin: "scip"`.
- **Imports multi-linguagem** no grafo: Go, Java, C#, Kotlin, Scala, Rust, PHP,
  Ruby e Swift passam a produzir arestas `imports` (antes só Python/JS/TS). As
  famílias de namespace (Java, C#, Kotlin, Scala) resolvem por
  `manifest.files_declares`; as de convenção de path resolvem pela raiz do pacote
  inferida do manifesto de build (`go.mod`, `pom.xml`, `*.csproj`, `Cargo.toml`).
- **`origin` por aresta** (`"scip"` | `"treesitter"`), presente apenas em `calls` e
  `imports` — os kinds em que a qualidade da resolução varia. `explain` e `affected`
  reportam `origin: "unknown"` quando o campo não existe.
- **`resolution_coverage`** no `graph.json` e em `atlas_status`: quais linguagens do
  índice resolvem por `scip`, quais por `treesitter`, quais **não resolvem** (`none`)
  e quantos arquivos ficaram sem resolução (Princípio VI — degradação explícita).
- **`watch`** reportado por `atlas_status` (∈ `active|disabled|unavailable|failed`)
  e **`scip_status`/`scip_edges`** reportados por `atlas_index` (`scip_status` ∈
  `ok|disabled|toolchain_missing|timeout|parse_failed`).
- `graph.html` distingue a origem da aresta pelo traço (`scip` sólida, `treesitter`
  tracejada, sem origem pontilhada) e traz na legenda os tiers de cobertura,
  incluindo as linguagens que o índice **não** resolve. Segue autocontido e offline.
- Extra opcional `watch = ["watchdog>=4.0,<7"]` em `[project.optional-dependencies]`.
  `[project].dependencies` permanece inalterado — nenhuma dependência obrigatória nova.
- **(Fase 2)** Cross-encoder ONNX opt-in na reordenação pós-RRF, via `ATLAS_RERANK_MODEL`
  (`reranker.CrossEncoderReranker`, fastembed já presente). Ausente, o rerank
  lexical de `ranking.py` permanece inalterado. Falha de carga emite
  `cross_encoder_unavailable` e cai no lexical. Ver `dec-008`.
- **(Fase 2)** Braço estrutural opt-in na fusão RRF, via `atlas_search(..., structural=True)`
  (default `False`). Spreading activation sobre `graph.json`; grafo ausente emite
  `structural_arm_unavailable`. Ver `dec-009`.
- **(Fase 2)** Harness `scripts/eval_search.py --structural` e registro do reranker ativo no
  relatório. Baseline recapturada em `tests/eval/baseline.json` (1644 chunks,
  MRR 0.4289, defaults de produção).

### Changed

- `CURRENT_INDEX_VERSION` passa de `2.1.0` para **`2.2.0`** (novo campo
  `files_declares` no manifest). **`MIN_INDEX_VERSION` continua `2.0.0`**: índices
  2.0.x e 2.1.0 seguem buscáveis, **sem reindexação forçada**. Um grafo anterior a
  2.2.0 simplesmente não tem `origin` nem `resolution_coverage`, e `atlas_status`
  responde `{"status": "unknown", "reason": "index_version_below_2_2_0"}` em vez de
  listas vazias.

## [2.1.1] - 2026-08-26

### Fixed

- **`uvx --from git+... atlas-serve` morria no import** com
  `ImportError: cannot import name 'McpError' from 'mcp.shared.exceptions'`.
  Causa: `uvx` ignora o `uv.lock` e, com `mcp>=1.24.0,<3` no pyproject, resolve
  `mcp` 2.x (SDK 2, `McpError` → `MCPError`) e recua `fastmcp==2.14.1` (ainda
  importa `McpError`). Correção: restaurar o teto `mcp>=1.24.0,<2`. Smoke
  `tests/test_mcp_pin.py` impede o teto `<3` de voltar sem teste falhar.

## [1.4.2] - 2026-07-03

### Added

- Script `doc_agent.py` para automatizar a criação de notas da knowledge base a partir de
  metadados de PR.

### Fixed

- Backend de armazenamento LanceDB corrigido para busca híbrida e escrita atômica de manifest.

### Changed

- Migração da documentação para o modelo de knowledge base e busca com retorno somente de
  metadados por padrão.
- Instruções de busca de código (`CLAUDE.md`) revisadas.

## [1.4.1] - 2026-06-13

### Added

- Resolução do diretório de índice via **MCP roots** (`roots/list`) como fallback quando o
  servidor é registrado globalmente (sem `CLAUDE_PROJECT_DIR`/`WORKSPACE_FOLDER_PATHS`),
  evitando que o índice caia em `HOME`.
- Cabeçalhos com timestamp no log de reindex em background.

## [1.4.0] - 2026-06-13

### Added

- Resolução do diretório de índice a partir de variáveis de ambiente de projeto do editor
  (`CLAUDE_PROJECT_DIR`, `WORKSPACE_FOLDER_PATHS`).
- Resolução de wikilinks do Obsidian em referências markdown retornadas por `atlas_search`.

### Changed

- `CLAUDE.md`/`README.md` atualizados com boas práticas de uso do MCP `codesteer-atlas`.
- Instruções de instalação do plugin/Power e configuração de variáveis de ambiente no README.

## [1.3.0] - 2026-06-13

### Added

- Resultados de `atlas_search` em Markdown enriquecidos com referências cruzadas entre
  documentos (`markdown_references`).
- `CONTRIBUTING.md` com instruções de setup de desenvolvimento.

## [1.2.2] - 2026-06-12

### Changed

- Docstring de `atlas_status` refinada para deixar claro que é apenas diagnóstico, não
  pré-requisito para `atlas_search`.
- Docstring de `atlas_search` refinada quanto a uso e tratamento de erros.

## [1.2.0] - 2026-06-12

### Added

- Documentação visual do MCP (`docs/index.html` + `docs/styles.css`).

### Changed

- Docstrings de `atlas_search`/`atlas_map` reforçadas para uso proativo das ferramentas.

## [1.1.0] - 2026-06-12

### Changed

- Tratamento de erros e logging mais robustos em operações Git e no mecanismo de lock
  (`get_git_head_sha`, `is_reindex_locked` toleram falhas de SO sem quebrar o fluxo).

## [1.0.0] - 2026-06-10

Release inicial do CodeSteer Atlas.

### Added

- Indexação por AST via Tree-sitter (`ASTChunker`), chunking em granularidade de
  classe/função/método, com fallback para chunk de módulo inteiro.
- Embeddings locais (`fastembed`, `all-MiniLM-L6-v2`, 384 dimensões) com lazy loading e
  carregamento thread-safe.
- Armazenamento em LanceDB embutido (`StorageBackend`), com escrita atômica de
  `manifest.json`.
- Busca híbrida (vetorial + BM25) fundida via Reciprocal Rank Fusion (RRF).
- Servidor MCP (FastMCP) expondo `atlas_search`, `atlas_map`, `atlas_status`, `atlas_index`.
- Indexação incremental por hash sha256, com fast path por `mtime`/tamanho para pular
  releitura de arquivos inalterados.
- Suporte a `.atlasignore` para exclusão declarativa de arquivos.
- Lock entre processos (`reindex_lock`) para coordenar reindexações concorrentes.
- Reindex automático em background no startup do servidor, rodando em subprocesso
  separado (evita contenção de GIL e corrupção do protocolo stdio JSON-RPC).
- Suporte multi-linguagem: Python, JavaScript, TypeScript/TSX, Go, Java, C#, Dart, Pascal,
  VB6, Razor, XML, Markdown, SQL, entre outros.
- Script de deploy (`deploy_mcp.py`) para registrar o servidor em Cursor, Claude Desktop,
  Cline e Claude Code CLI.

[Unreleased]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v2.1.1...HEAD
[2.2.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v2.1.1...HEAD
[2.1.1]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v2.1...HEAD
[1.4.2]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.4.0...34ef305
[1.4.1]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.4.0...dbd5c9a
[1.4.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/releases/tag/v1.4.0
[1.3.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.0.0...eb37b4b
[1.2.2]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.0.0...dc39cb1
[1.2.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.0.0...497df8c
[1.1.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.0.0...807c888
[1.0.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/releases/tag/v0.1
