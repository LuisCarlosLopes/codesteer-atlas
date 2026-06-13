---
tipo: integracao
titulo: "LanceDB"
tags: [integracao, banco, vetorial]
dominio: "[[armazenamento]]"
criado: 2026-06-13
---

# LanceDB

Banco vetorial embutido usado em [[ADR-007-indice-local]]. Persiste tabela de chunks + índice FTS BM25.

## Uso no Atlas

- [[storage-backend]] — conexão em `.code-index/`
- Operações: vector search, FTS, append, delete

## Contrato

- `storage_backend: "lancedb"` em [[index-manifest]]

## Links

- [[armazenamento]]
