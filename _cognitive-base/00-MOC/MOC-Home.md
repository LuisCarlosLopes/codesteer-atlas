---
tipo: moc
titulo: "Home — CodeSteer Atlas"
aliases: [MOC Home, Atlas Home]
tags: [moc, atlas]
status: ativo
criado: 2026-06-13
atualizado: 2026-06-13
---

# MOC Home — CodeSteer Atlas

Mapa de conteúdo principal do vault. O Atlas é um **servidor MCP local** de busca semântica híbrida sobre bases de código — 100% offline.

## Domínios

- [[indexacao]] — pipeline AST → embed → persist
- [[busca]] — vector + BM25 via RRF
- [[armazenamento]] — LanceDB + manifest
- [[mcp-server]] — tools FastMCP stdio
- [[deploy]] — registro em clientes MCP
- [[embeddings]] — vetorização local fastembed

## Navegação por tipo

| Pasta | MOC |
|-------|-----|
| Domínios | [[MOC-Indexacao]], [[MOC-Busca]], [[MOC-MCP-Server]], [[MOC-Armazenamento]], [[MOC-Deploy]] |
| Interfaces MCP | [[atlas-search]], [[atlas-map]], [[atlas-index]], [[atlas-status]] |
| Arquitetura | [[ADR-001-reindex-lock]], [[ADR-002-busca-hibrida-rrf]], [[ADR-005-indexacao-incremental]] |
| Qualidade | [[indexer-testes]], [[server-testes]] |
| Riscos | [[indice-stale]], [[manifest-version-incompativel]] |

## Diagrama

Ver [[../codesteer-atlas-mindmap-enriquecido|mindmap enriquecido]].
