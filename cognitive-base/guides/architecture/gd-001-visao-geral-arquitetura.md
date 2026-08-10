---
id: gd-001
type: architecture-overview
title: "Visão geral da arquitetura do CodeSteer Atlas"
status: approved
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: dec-001
    rel: depends-on
  - id: dec-004
    rel: depends-on
  - id: sys-005
    rel: related-to
tags: [arquitetura, mcp, busca]
source: greenfield
migration_status: ""
meta: {}
---

# Visão geral da arquitetura do CodeSteer Atlas

## Contexto

O **CodeSteer Atlas** é um servidor MCP local que oferece busca híbrida de código
(semântica + lexical) sobre um workspace. Tudo roda offline — nenhum código-fonte
sai da máquina ([[dec-004-indice-100-local]]).

## Componentes

```
┌─────────────┐     stdio      ┌──────────────────┐
│ MCP Client  │◄──────────────►│ server.py        │
│ (Cursor,    │                │ FastMCP tools    │
│  Claude…)   │                └────────┬─────────┘
└─────────────┘                         │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
            ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
            │ indexer.py   │   │ storage.py   │   │ chunker.py   │
            │ index_workspace│  │ LanceDB+FTS  │   │ ASTChunker   │
            └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
                   │                  │                  │
                   └──────────────────┼──────────────────┘
                                      ▼
                            ┌──────────────────┐
                            │ embeddings.py    │
                            │ fastembed (ONNX) │
                            └──────────────────┘
                                      │
                                      ▼
                            ┌──────────────────┐
                            │ .code-index/     │
                            │ manifest.json    │
                            └──────────────────┘
```

| Componente | Módulo | Responsabilidade |
| ---------- | ------ | ---------------- |
| Servidor MCP | [[sys-005-mcp-server]] | Expõe `atlas_search`, `atlas_map`, `atlas_index`, `atlas_status` |
| Indexador | [[sys-004-index-workspace]] | Scan incremental, hash sha256, orquestra chunk+embed+persist |
| Chunker | [[sys-001-ast-chunker]] | AST Tree-sitter → chunks via [[meta/glossary#chunk]] |
| Embeddings | [[sys-003-embedding-engine]] | Vetores 384d via `all-MiniLM-L6-v2` |
| Storage | [[sys-002-storage-backend]] | LanceDB, FTS, fusão [[dec-001-busca-hibrida-rrf]] |

## Como se conectam

1. **Indexação** — `atlas_index` ou CLI `atlas-index` chama `index_workspace()`, que
   usa o chunker e o engine de embeddings, persiste via storage ([[dec-003-indexacao-incremental]]).
2. **Busca** — `atlas_search` gera embedding da query, executa busca vetorial + BM25,
   funde com RRF e retorna metadados (conteúdo sob demanda).
3. **Resolução do índice** — `.code-index` é descoberto por cadeia de fallbacks
   ([[dec-002-resolucao-index-dir]]).

## Notas Relacionadas

- [[gd-030-primeiros-passos|Primeiros passos]] — setup e uso diário
- [[dec-001-busca-hibrida-rrf]] — por que busca híbrida com RRF
- [[meta/glossary]] — termos do domínio

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
