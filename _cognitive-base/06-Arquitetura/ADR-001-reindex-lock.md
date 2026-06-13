---
tipo: adr
titulo: "ADR-001 — Lock de reindexação"
numero: 1
tags: [adr, concorrencia]
status: aceito
criado: 2026-06-13
---

# ADR-001 — Lock de reindexação (DECISAO-001)

## Contexto

CLI, MCP `atlas_index` e reindex em background no startup podem concorrer pelo mesmo `.code-index`.

## Decisão

Arquivo `.reindex.lock` coordenado por [[reindex-lock]] — apenas um processo indexa por vez.

## Consequências

- Segunda chamada retorna `skipped_reason: reindex_in_progress`
- `atlas_status.reindexing` reflete estado do lock

## Afeta

- [[index-workspace]]
- [[atlas-index]]
- [[index-stats]]
