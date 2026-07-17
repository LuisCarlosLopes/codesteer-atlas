# Changelog

Todas as mudanças relevantes deste projeto são documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Unreleased]

Mudanças na branch `feat/fable5`, ainda não publicadas em `pyproject.toml` (versão atual: `1.4.2`).

### Added

- **Grafo de conhecimento derivado** (`graph.json`): novo módulo `graph.py` reconstrói um grafo
  de nós (`file`/`doc`/`symbol`/`section`/`rationale`) e arestas (`contains`/`imports`/`cites`/
  `mentions`) a partir do índice. Suporta rebuild completo e **rebuild incremental** para arquivos
  de código já indexados que só tiveram o conteúdo alterado.
- **Nova ferramenta MCP `atlas_graph`** (`mode=hubs|path|explain`): consulta hubs de centralidade,
  caminhos entre dois nós (BFS) e a vizinhança/explicação de um nó, lendo `graph.json` sem
  reconstruí-lo.
- **Visualizador local do grafo** (`viewer.py`): gera `.code-index/graph.html` autocontido,
  abrível via `file://`, com pan/zoom, filtros, busca e painel de detalhes.
- **Rationale refs em código** (`rationale.py`): extrai referências de rationale (comentários
  `NOTE`/`WHY`, cites `DEC/ADR/RFC`, wikilinks) de cada chunk; persistidas como `references` em
  `CodeChunk`/`SearchResult` e retornadas por `atlas_search` como `rationale_refs`.
- **Extração de imports** (`chunker.py`): novo `ASTChunker.extract_imports()` para Python e
  JS/TS, reaproveitando o parser Tree-sitter cacheado; alimenta `manifest.files_imports`, base
  das arestas `imports` do grafo.
- **Observabilidade da indexação**: `atlas_index` passa a expor `phase_durations_s` (por fase:
  scan/hash/chunk/embed/persist/graph), `files_scanned`, `files_eligible`, `chunks_generated`,
  `graph_strategy` (`full`/`incremental-code`/`skipped-unchanged`) e métricas do grafo
  (`graph_nodes`, `graph_edges`, `graph_bytes`, `graph_html_bytes`).
- `dry_run` de `atlas_index` agora recomenda `paths` específicos quando o workspace tem mais de
  200 arquivos elegíveis, em vez de sugerir indexação completa.
- Novo script `scripts/benchmark_index.py` para medir performance da indexação.
- Guia didático `docs/guia-indexacao-grafo-mcp.md` e nota `cognitive-base/guides/architecture/
  gd-040-indexacao-grafo-workspace-mcp.md` documentando o pipeline de grafo.
- Agentes espelhados para Codex CLI em `.codex/agents/*.toml` (19 arquivos) + `.codex/config.toml`.

### Changed

- Base de conhecimento renomeada de `knowledge-base/` para `cognitive-base/` (vault Obsidian
  `.obsidian/*` removido do controle de versão).
- `StorageBackend.get_graph_projection()` reduz uso de memória: nunca carrega a coluna `vector`
  e só carrega `content` de chunks Markdown (chunks de código usam apenas refs/imports para o
  grafo).
- Reindex automático em background agora tem **debounce**: é pulado quando o manifest está
  recente e o HEAD do Git não mudou desde a última indexação.
- `CURRENT_INDEX_VERSION` avança para `2.1.0` — índices anteriores não têm `graph.json` e exigem
  reindexação para usar `atlas_graph`.
- `docs/index.html`/`docs/styles.css` expandidos com a documentação visual do grafo.

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

[Unreleased]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.4.0...HEAD
[1.4.2]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.4.0...34ef305
[1.4.1]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.4.0...dbd5c9a
[1.4.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/releases/tag/v1.4.0
[1.3.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.0.0...eb37b4b
[1.2.2]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.0.0...dc39cb1
[1.2.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.0.0...497df8c
[1.1.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/compare/v1.0.0...807c888
[1.0.0]: https://github.com/LuisCarlosLopes/codesteer-atlas/releases/tag/v0.1
