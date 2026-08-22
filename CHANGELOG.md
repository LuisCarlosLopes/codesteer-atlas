# Changelog

Todas as mudanças relevantes deste projeto são documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Unreleased]

Versão alvo: `2.0.0` (MAJOR — remove uma ferramenta MCP pública, veja **Removed**).

### Added

- **Arestas símbolo→símbolo (`calls`) no `graph.json`:** o chunker extrai alvos de
  chamada da AST (nunca do `content` truncado), persiste `calls_json` no LanceDB e o
  grafo resolve em segunda passada — mesmo arquivo → via import → único no grafo →
  descarta — marcando `resolution` `exact`|`inferred` (`inferred` é pista, não fato).
  Rebuild incremental descarta todas as arestas `calls` e re-resolve no grafo inteiro,
  para não divergir do rebuild completo. Medido neste repositório: **0 → 1356 arestas
  `calls` (98,4% exact / 1,6% inferred)**; símbolos com grau > 0 de 3,4% para 80,4%.
  `atlas_graph(mode=path)` entre dois símbolos que se chamam passa a achar caminho.

- **Invalidação automática por versão do produtor:** `CHUNKER_VERSION` (separado de
  `CURRENT_INDEX_VERSION`) fica no `manifest.json`. Se o chunker ou o modelo de
  embedding divergirem do índice, a próxima `atlas-index` força reindexação completa
  (scan do workspace inteiro, mesmo com `paths`), imprime o motivo em stderr e
  reporta `full_reason` (`chunker_version` | `embedding_model`). Manifests antigos
  sem o campo valem `0.0.0` e re-chunkam — é o que preenche `calls_json` em índices
  já existentes.

### Changed

- **`atlas_graph(mode=hubs)` honra `top_n`:** ordena todos os nós por `(-degree, id)`
  em vez de fatiar `metrics.top_hubs` (teto interno 25). `hubs(top_n=50)` devolve 50.

- **`atlas_graph(mode=explain)` com teto e `omitted`:** no máximo 25 vizinhos por
  kind e 15 notas; a resposta inclui `omitted` com as contagens truncadas. Pior caso
  neste repositório: **~81 KB → ~5 KB**.

- **`atlas_brief` lista só hubs `file`/`doc`:** arestas `calls` entram no `degree` do
  grafo (e em `atlas_graph` / viewer), mas o briefing deixa de promover símbolos a
  "arquivos mais conectados". Sem o filtro, os 8 primeiros hubs do briefing neste
  repo passariam a ser todos `symbol`.

### Fixed

- **Crash intermitente na persistência após extrair calls:** o parser nativo do
  Tree-sitter é unsendable (pyo3); o GC durante as threads do LanceDB levantava
  `RuntimeError`. `ASTChunker.release_parsers()` libera os parsers na thread que os
  criou, antes da escrita.

### Changed

- **README simplificado para setup:** fluxo em 3 passos; aviso explícito contra
  plugin/MCP em escopo **global** (não dá para inferir `.code-index` do projeto
  com confiança); instrução para instalar o plugin no **projeto atual**
  (`--scope project|local`) ou configurar via `mcp.json` na raiz. Exemplo do
  Cursor passa a incluir `ATLAS_INDEX_DIR=${workspaceFolder}/.code-index`.

### Fixed

- **`uvx --from git+... atlas-index` quebrava no primeiro arquivo** com
  `IncompatibleParserError: source must be a bytestring or a callable, not str`.
  Causa: `uvx` ignora o `uv.lock` e resolve `tree-sitter-language-pack` ao último 1.x
  (hoje 1.14.x); a partir de **1.13** o pack voltou a devolver o `tree_sitter.Parser`
  clássico (`parse(bytes)`, `root_node`/`type` como properties), enquanto o chunker
  só aceitava a API nativa de 1.8–1.12 (`parse(str)`, `root_node()`, `kind()`). O pin
  `<2` do PR anterior não cobria essa regressão **dentro** da major 1.x.
  Correção: `_CompatParser` / `_CompatTree` / `_CompatNode` normalizam as duas APIs;
  a verificação de ambiente passa a detectar o sabor em vez de abortar na clássica.

### Added

- **Nova ferramenta MCP `atlas_brief`**: briefing pré-computado do projeto, com **custo de tokens
  limitado por teto fixo, independente do tamanho do repositório**. Substitui o ritual caro de
  orientação de um agente (listar diretórios, ler o README, abrir vários arquivos). Retorna
  `identity` (repo, distribuição de linguagens, tamanho), `layers` (diretórios principais, papel de
  cada um e seus arquivos mais relevantes), `entrypoints` (como o projeto é iniciado de fato) e
  `hubs` (arquivos mais conectados). `level=0` para orientação mínima, `level=1` (padrão) completo.
  Medido neste repositório: **~950 chars no `level=0` e ~4.100 no `level=1`, contra 20.347 chars do
  antigo `atlas_map`** — e a diferença cresce linearmente com o tamanho do repositório.
- **Artefato derivado `brief.json`** (`brief.py`): gerado em nova fase da indexação, junto de
  `graph.json`. Deriva tudo de `manifest` + `graph.json`, sem consultar o LanceDB, o que mantém a
  recomputação sob demanda barata em repositórios de qualquer tamanho. Diferente de `graph.json`,
  persiste `git_head_sha` e `index_version`, permitindo reportar staleness. Uma falha ao gerá-lo
  nunca interrompe a indexação.
- **Rastreabilidade das afirmações do brief**: cada entrypoint carrega `confidence`
  (`declared` quando lido de `pyproject.toml`/`package.json`/`Dockerfile`, `inferred` quando
  detectado no código e confirmado por probe) e `evidence`. Um candidato que não passa no probe é
  descartado, nunca rebaixado — lista vazia é resposta válida. `warnings` usa vocabulário fechado
  (`graph_unavailable`, `no_import_edges`, `low_symbol_coverage`, `multi_repo_index`,
  `layers_collapsed`, `truncated_for_budget`, `brief_recomputed_at_query_time`, `index_stale`).
- `atlas_index` passa a expor `brief_status` (`full`/`degraded-no-graph`/`failed`), `brief_bytes`,
  `brief_layers` e `brief_entrypoints`, além da nova fase `brief` em `phase_durations_s`.
- **Grafo de conhecimento derivado** (`graph.json`): novo módulo `graph.py` reconstrói um grafo
  de nós (`file`/`doc`/`symbol`/`section`/`rationale`) e arestas (`contains`/`imports`/`cites`/
  `mentions`/`calls`) a partir do índice. Suporta rebuild completo e **rebuild incremental** para
  arquivos de código já indexados que só tiveram o conteúdo alterado.
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

### Fixed

- **Imports absolutos não resolviam em layout `src/`, deixando o grafo quase sem arestas
  `imports`.** `_resolve_python_import` montava candidatos encurtando o caminho do módulo
  (`codesteer_atlas/config.py`, ...) e comparava com as chaves do manifest, que são relativas ao
  workspace — logo o caminho real (`src/codesteer_atlas/config.py`) nunca casava. Só imports
  relativos resolviam. Neste repositório havia **1 aresta `imports` para 29 arquivos Python**.

  Correção: novo `infer_package_roots()` deduz as raízes de código a partir dos `__init__.py`
  (subindo até o pacote mais externo) mais as convenções `src/` e `lib/`, e o novo helper
  compartilhado `resolve_module_path()` testa cada raiz. `brief.py::_resolve_module_attr` passou a
  delegar a ele, eliminando a versão local que tinha `src/` hard-coded. Resultado neste
  repositório: **1 → 61 arestas `imports`**.

  Efeito a jusante: `atlas_graph` (hubs/path/explain) ganha conectividade real, e no `atlas_brief`
  as camadas de código passam a ranquear por `degree` em vez de cair no fallback por contagem de
  símbolos — o topo de `src/codesteer_atlas` deixou de ser `embeddings.py`/`markdown_links.py` e
  passou a ser `config.py`/`server.py`/`indexer.py`, com `config.py` entrando na lista de hubs.

- **Indexação em background produzia índice vazio e reportava sucesso.** O chunker usava
  `parser.parse(str)` / `tree.root_node()` sem qualquer verificação, mas existem duas APIs de
  parser mutuamente exclusivas em circulação — a do `tree-sitter-language-pack` recente (usada
  aqui) e a clássica baseada em bytes. No ambiente errado, **todo** arquivo de código falhava com
  `source must be a bytestring or a callable, not str`, o erro era engolido por arquivo e a
  execução terminava com "Indexação Concluída com Sucesso!". Neste repositório isso resultou em
  549 chunks com `server.py`, `graph.py`, `indexer.py` e `storage.py` a **zero símbolos**, contra
  1361 numa indexação íntegra.

  A divergência de ambiente vem de o servidor MCP ser registrado via
  `uvx --from git+https://...` ([.mcp.json](.mcp.json)): esse ambiente é resolvido pelos ranges do
  `pyproject.toml` e **ignora o `uv.lock`**, e o subprocesso de reindex herda esse interpretador
  via `sys.executable`.

  Correções: `ASTChunker._verify_parser_api()` valida a API uma única vez na criação do primeiro
  parser e levanta `IncompatibleParserError` com instruções acionáveis (incluindo `uv cache clean`);
  o indexador propaga esse erro e **aborta** em vez de tratá-lo como problema pontual de arquivo;
  falhas legítimas por arquivo agora são contadas em `IndexStats.files_failed`, exibidas pela CLI
  (que passa a imprimir "Indexação Concluída COM FALHAS") e expostas por `atlas_index` como
  `files_failed` + `warning`. O pin passou a ter teto (`tree-sitter-language-pack>=1.8.0,<2`).

### Removed

- **BREAKING: ferramenta MCP `atlas_map` removida.** Ela e `atlas_brief` respondiam à mesma
  pergunta — "como este projeto é organizado" — e manter as duas fazia o agente escolher pela ordem
  em que as via, ou chamar ambas. `atlas_map` era exaustiva e plana (uma linha por símbolo), com
  custo de tokens crescendo linearmente com o repositório.

  **Migração:** para se orientar num projeto, use `atlas_brief`. Para localizar uma implementação
  dentro de um diretório, use `atlas_search` com `path_prefix`. Para a lista completa de símbolos de
  um arquivo específico, use `Read` direto. A enumeração exaustiva de símbolos sob um diretório
  arbitrário deixa de existir até que `atlas_brief` ganhe o parâmetro `focus`.

  Arquivos `CLAUDE.md`/`AGENTS.md` de outros repositórios que citem `atlas_map` precisam ser
  atualizados manualmente.

- `StorageBackend.get_symbols()` deixa de ter chamador em produção (era usada apenas por
  `atlas_map`). Mantida como utilitário de inspeção do índice.

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
