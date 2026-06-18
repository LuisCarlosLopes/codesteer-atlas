# Índice da Base Cognitiva
> Gerado automaticamente em 2026-06-17 · 19 notas

## Grafo por quadrante

### decisions/ — 6 notas

| ID | Título | Type | Status | Conexões |
|---|---|---|---|---|
| [[dec-001-busca-hibrida-rrf]] | Busca híbrida com fusão RRF (vetorial + BM25) | adr | draft | 4 |
| [[dec-002-resolucao-index-dir]] | Resolução do diretório .code-index em múltiplos contextos | adr | draft | 3 |
| [[dec-003-indexacao-incremental]] | Indexação incremental por hash sha256 de arquivos | adr | draft | 5 |
| [[dec-004-indice-100-local]] | Índice e embeddings 100% locais — sem envio de código externo | adr | draft | 4 |
| [[dec-005-backend-embeddings-fastembed]] | Backend de embeddings fastembed (ONNX) em vez de PyTorch | adr | draft | 6 |
| [[dec-006-lock-reindex-concorrente]] | Lock de arquivo para reindexações concorrentes | adr | draft | 0 |

### specs/ — 1 notas

| ID | Título | Type | Status | Conexões |
|---|---|---|---|---|
| [[spc-001-api-ferramentas-mcp]] | API das ferramentas MCP atlas_* | api | draft | 5 |

### system/ — 5 notas

| ID | Título | Type | Status | Conexões |
|---|---|---|---|---|
| [[sys-001-ast-chunker]] | ASTChunker — extração de chunks por símbolo Tree-sitter | service | draft | 2 |
| [[sys-002-storage-backend]] | StorageBackend — LanceDB, FTS e busca híbrida | service | draft | 3 |
| [[sys-003-embedding-engine]] | EmbeddingEngine — wrapper singleton fastembed | service | draft | 4 |
| [[sys-004-index-workspace]] | index_workspace — núcleo de indexação do workspace | service | draft | 4 |
| [[sys-005-mcp-server]] | Servidor MCP FastMCP — ferramentas atlas_* | service | draft | 5 |

### guides/ — 2 notas

| ID | Título | Type | Status | Conexões |
|---|---|---|---|---|
| [[gd-001-visao-geral-arquitetura]] | Visão geral da arquitetura do CodeSteer Atlas | architecture-overview | draft | 6 |
| [[gd-030-primeiros-passos]] | Primeiros passos — setup, indexação e uso do MCP | how-to | draft | 6 |

### ops/ — 5 notas

| ID | Título | Type | Status | Conexões |
|---|---|---|---|---|
| [[ops-001-runbook-reindex]] | Runbook — reindexar o workspace | runbook | draft | 4 |
| [[ops-002-runbook-indice-stale]] | Runbook — índice stale (git HEAD divergente) | runbook | draft | 2 |
| [[ops-003-runbook-manifest-incompativel]] | Runbook — manifest com versão incompatível | runbook | draft | 3 |
| [[ops-004-runbook-reindex-em-progresso]] | Runbook — reindex em progresso (lock concorrente) | runbook | draft | 4 |
| [[ops-005-runbook-stdio-stdout]] | Runbook — canal MCP stdio poluído por stdout | runbook | draft | 0 |

## Nós mais conectados

> Os nós com mais wikilinks apontando para eles são os pilares do domínio.
> Comece por eles para entender a arquitetura.

| ID | Título | Conexões recebidas |
|---|---|---|
| [[dec-005-backend-embeddings-fastembed]] | Backend de embeddings fastembed (ONNX) em vez de PyTorch | 6 |
| [[gd-001-visao-geral-arquitetura]] | Visão geral da arquitetura do CodeSteer Atlas | 6 |
| [[gd-030-primeiros-passos]] | Primeiros passos — setup, indexação e uso do MCP | 6 |
| [[sys-005-mcp-server]] | Servidor MCP FastMCP — ferramentas atlas_* | 5 |
| [[dec-003-indexacao-incremental]] | Indexação incremental por hash sha256 de arquivos | 5 |
| [[spc-001-api-ferramentas-mcp]] | API das ferramentas MCP atlas_* | 5 |
| [[sys-004-index-workspace]] | index_workspace — núcleo de indexação do workspace | 4 |
| [[sys-003-embedding-engine]] | EmbeddingEngine — wrapper singleton fastembed | 4 |
| [[dec-004-indice-100-local]] | Índice e embeddings 100% locais — sem envio de código externo | 4 |
| [[ops-004-runbook-reindex-em-progresso]] | Runbook — reindex em progresso (lock concorrente) | 4 |

## Padrões mais violados pela IA

> Extraído de decisions/ai-corrections/ — alimenta o system prompt do agente.

_Nenhuma correção de IA registrada._

