---
tipo: adr
titulo: "ADR-003 — FTS com prefilter"
numero: 3
aliases: [DECISAO-003]
tags: [adr, fts, busca]
status: aceito
criado: 2026-06-13
---

# ADR-003 — FTS com prefilter (DECISAO-003)

## Contexto

Filtros `path_prefix` e `language` devem reduzir candidatos antes da fusão RRF.

## Decisão

- `_build_where_clause` gera SQL `where` para vector e FTS
- FTS atualizado incrementalmente em `append_chunks` (não rebuild total)
- `CANDIDATES_LIMIT` aplicado **com** prefilter

## Consequências

- `top_k` completo mesmo com filtros seletivos
- Escape de aspas em `path_prefix`

## Afeta

- [[storage-backend]]
- [[atlas-search]]
