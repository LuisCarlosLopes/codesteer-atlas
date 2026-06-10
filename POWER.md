---
name: codesteer-atlas
displayName: CodeSteer Atlas
description: Busca semântica híbrida (vetorial + BM25) em código-fonte via AST/Tree-sitter, embeddings locais (fastembed) e LanceDB. 100% local e offline.
keywords:
  - code search
  - semantic search
  - codebase search
  - find code
  - find function
  - find class
  - search symbols
  - hybrid search
  - RAG
  - codebase map
author: CodeSteer Squad
---

# CodeSteer Atlas

Servidor MCP local de busca semântica em código-fonte. Indexa o workspace via Tree-sitter (chunks de classes/funções/métodos), gera embeddings locais (`all-MiniLM-L6-v2` via `fastembed`) e armazena em LanceDB. A busca combina similaridade vetorial (cosseno) com BM25 (full-text), fundidas por Reciprocal Rank Fusion (RRF).

## Onboarding

Ao ativar este power pela primeira vez:

1. Verifique se `uv`/`uvx` estão instalados (`uvx --version`). Se não estiverem, instale com `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) ou `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows).
2. Indexe o workspace atual (gera a pasta `.code-index` na raiz do projeto):

   ```bash
   uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace .
   ```

3. Não é necessário configurar `--index-dir`/`ATLAS_INDEX_DIR`: o servidor descobre automaticamente a pasta `.code-index` por busca ascendente a partir do diretório de trabalho.
4. Se o workspace mudar significativamente (muitos arquivos novos/alterados), peça para reindexar com o mesmo comando do passo 2 (a indexação é incremental por padrão).

## Quando usar

Use as tools deste power sempre que precisar:

- Encontrar onde uma função, classe, método ou conceito está implementado.
- Entender a estrutura/arquitetura de um diretório ou repositório sem ler todos os arquivos.
- Localizar trechos de código relevantes para uma tarefa antes de editar.
- Verificar se o índice de busca existe e está atualizado em relação ao HEAD do git.

## Tools disponíveis

- `atlas_search(query, top_k, repo, language, path_prefix, include_content)` — busca híbrida por trechos de código relevantes.
- `atlas_map(repo, path_prefix, max_depth)` — mapa hierárquico de classes/funções/métodos do workspace.
- `atlas_status()` — diagnóstico do índice (existência, total de chunks, modelo, staleness).
- `atlas_index(workspace, paths, full, dry_run)` — (re)indexa o workspace; use `dry_run=true` antes de indexar pastas ainda não indexadas.

## Boas práticas

- Prefira `atlas_search`/`atlas_map` a ler arquivos inteiros para localizar código — é muito mais econômico em tokens.
- Antes de indexar um workspace inteiro pela primeira vez, rode `atlas_index` com `dry_run=true` e confirme com o usuário quais pastas indexar.
- Se `atlas_status` indicar `is_stale: true`, sugira reindexar antes de confiar nos resultados de busca.
