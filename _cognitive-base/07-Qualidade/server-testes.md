---
tipo: qualidade
titulo: "server-testes"
arquivo: "tests/test_server.py"
tags: [testes, mcp, server]
dominio: "[[mcp-server]]"
criado: 2026-06-13
---

# server-testes

## Escopo

`tests/test_server.py` — MCP tools, resolução de índice, background reindex.

## Cenários cobertos

- atlas_search/map/status/index happy path
- include_content, markdown references
- resolve_index_dir precedência e discovery
- atlas_index async vs sync, dry_run, paths traversal
- background reindex no startup
- reindexing flag com lock
- spawn subprocess errors

## Relacionados

- [[atlas-search]], [[atlas-map]], [[atlas-index]], [[atlas-status]]
- [[ADR-004-async-reindex]]
