---
tipo: dominio
titulo: "Busca"
aliases: [busca, search]
tags: [dominio, busca, hibrida]
status: ativo
relacionados: ["[[armazenamento]]", "[[embeddings]]"]
criado: 2026-06-13
atualizado: 2026-06-13
---

# Busca

## Visão Geral

Combina similaridade vetorial (cosseno) com BM25 full-text, fundindo rankings via Reciprocal Rank Fusion ([[ADR-002-busca-hibrida-rrf]]).

## Responsabilidades

- Embed da query via [[embedding-engine]]
- Prefilter SQL por `path_prefix`, `language`, `repo` ([[ADR-003-fts-prefilter]])
- Retorno de `SearchResult` com score RRF
- Mapa hierárquico de símbolos via `get_symbols`

## Telas

- [[atlas-search]]
- [[atlas-map]]

## Serviços

- [[storage-backend]] — `search_hybrid`, `get_symbols`, `get_sections_by_file_path`

## Entidades

- [[search-result]]
- [[code-chunk]]

## Riscos e Limitações

- `CANDIDATES_LIMIT = 50` por braço antes da fusão
- Filtros muito restritivos podem retornar menos que `top_k`

## Links Relacionados

- [[MOC-Busca]]
- [[storage-testes]]
