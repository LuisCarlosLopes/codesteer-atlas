# Índice da Base Cognitiva
> Gerado automaticamente em 2026-09-21 · 24 notas

## Grafo por quadrante

### decisions/ — 10 notas

| ID | Título | Type | Status | Conexões |
|---|---|---|---|---|
| [[dec-001-busca-hibrida-rrf]] | Busca híbrida com fusão RRF (vetorial + BM25) | adr | approved | 9 |
| [[dec-002-resolucao-index-dir]] | Resolução do diretório .code-index em múltiplos contextos | adr | approved | 4 |
| [[dec-003-indexacao-incremental]] | Indexação incremental por hash sha256 de arquivos | adr | approved | 6 |
| [[dec-004-indice-100-local]] | Índice e embeddings 100% locais — sem envio de código externo | adr | approved | 7 |
| [[dec-005-backend-embeddings-fastembed]] | Backend de embeddings fastembed (ONNX) em vez de PyTorch | adr | approved | 8 |
| [[dec-006-lock-reindex-concorrente]] | Lock de arquivo para reindexações concorrentes | adr | approved | 0 |
| [[dec-007-rerank-pos-rrf]] | Ranking de busca: reordenação pós-RRF do pool de candidatos | adr | approved | 6 |
| [[dec-008-cross-encoder-rerank]] | Cross-encoder ONNX opt-in na reordenação pós-RRF | adr | approved | 4 |
| [[dec-009-braco-estrutural-rrf]] | Braço estrutural opt-in na fusão RRF | adr | approved | 1 |
| [[dec-010-jev-entrega-compacta-opt-in]] | Jev e entrega compacta opt-in, com o ranking local de v3.0 como default | adr | draft | 0 |

### specs/ — 1 notas

| ID | Título | Type | Status | Conexões |
|---|---|---|---|---|
| [[spc-001-api-ferramentas-mcp]] | API das ferramentas MCP atlas_* | api | approved | 7 |

### system/ — 5 notas

| ID | Título | Type | Status | Conexões |
|---|---|---|---|---|
| [[sys-001-ast-chunker]] | ASTChunker — extração de chunks por símbolo Tree-sitter | service | approved | 2 |
| [[sys-002-storage-backend]] | StorageBackend — LanceDB, FTS e busca híbrida | service | approved | 4 |
| [[sys-003-embedding-engine]] | EmbeddingEngine — wrapper singleton fastembed | service | approved | 4 |
| [[sys-004-index-workspace]] | index_workspace — núcleo de indexação do workspace | service | approved | 5 |
| [[sys-005-mcp-server]] | Servidor MCP FastMCP — ferramentas atlas_* | service | approved | 7 |

### guides/ — 3 notas

| ID | Título | Type | Status | Conexões |
|---|---|---|---|---|
| [[gd-001-visao-geral-arquitetura]] | Visão geral da arquitetura do CodeSteer Atlas | architecture-overview | approved | 7 |
| [[gd-030-primeiros-passos]] | Primeiros passos — setup, indexação e uso do MCP | how-to | approved | 8 |
| [[gd-040-indexacao-grafo-workspace-mcp]] | Indexação, grafo e workspace multi-repo — guia didático | how-to | approved | 0 |

### ops/ — 5 notas

| ID | Título | Type | Status | Conexões |
|---|---|---|---|---|
| [[ops-001-runbook-reindex]] | Runbook — reindexar o workspace | runbook | approved | 5 |
| [[ops-002-runbook-indice-stale]] | Runbook — índice stale (git HEAD divergente) | runbook | approved | 2 |
| [[ops-003-runbook-manifest-incompativel]] | Runbook — manifest com versão incompatível | runbook | approved | 3 |
| [[ops-004-runbook-reindex-em-progresso]] | Runbook — reindex em progresso (lock concorrente) | runbook | approved | 4 |
| [[ops-005-runbook-stdio-stdout]] | Runbook — canal MCP stdio poluído por stdout | runbook | approved | 0 |

## Nós mais conectados

> Os nós com mais wikilinks apontando para eles são os pilares do domínio.
> Comece por eles para entender a arquitetura.

| ID | Título | Conexões recebidas |
|---|---|---|
| [[dec-001-busca-hibrida-rrf]] | Busca híbrida com fusão RRF (vetorial + BM25) | 9 |
| [[dec-005-backend-embeddings-fastembed]] | Backend de embeddings fastembed (ONNX) em vez de PyTorch | 8 |
| [[gd-030-primeiros-passos]] | Primeiros passos — setup, indexação e uso do MCP | 8 |
| [[dec-004-indice-100-local]] | Índice e embeddings 100% locais — sem envio de código externo | 7 |
| [[gd-001-visao-geral-arquitetura]] | Visão geral da arquitetura do CodeSteer Atlas | 7 |
| [[spc-001-api-ferramentas-mcp]] | API das ferramentas MCP atlas_* | 7 |
| [[sys-005-mcp-server]] | Servidor MCP FastMCP — ferramentas atlas_* | 7 |
| [[dec-003-indexacao-incremental]] | Indexação incremental por hash sha256 de arquivos | 6 |
| [[dec-007-rerank-pos-rrf]] | Ranking de busca: reordenação pós-RRF do pool de candidatos | 6 |
| [[ops-001-runbook-reindex]] | Runbook — reindexar o workspace | 5 |

## Padrões mais violados pela IA

> Extraído de decisions/ai-corrections/ — alimenta o system prompt do agente.

| ID | Título | Padrão violado |
|---|---|---|
| _nenhum_ | — | — |
