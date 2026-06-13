---
tipo: adr
titulo: "ADR-002 — Busca híbrida RRF"
numero: 2
tags: [adr, busca, rrf]
status: aceito
criado: 2026-06-13
---

# ADR-002 — Busca híbrida RRF

## Contexto

Busca só vetorial perde matches lexicais exatos; só BM25 perde semântica.

## Decisão

Executar vector search + FTS BM25 em paralelo, fundir com Reciprocal Rank Fusion (`RRF_K = 60`).

## Consequências

- [[storage-backend]].`search_hybrid` retorna [[search-result]] com score RRF
- `CANDIDATES_LIMIT = 50` por braço

## Afeta

- [[busca]]
- [[atlas-search]]
- [[code-chunk]]
