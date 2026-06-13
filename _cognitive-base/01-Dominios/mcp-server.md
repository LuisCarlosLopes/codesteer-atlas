---
tipo: dominio
titulo: "MCP Server"
aliases: [mcp-server, servidor-mcp]
tags: [dominio, mcp, fastmcp]
status: ativo
relacionados: ["[[busca]]", "[[indexacao]]", "[[deploy]]"]
criado: 2026-06-13
atualizado: 2026-06-13
---

# MCP Server

## Visão Geral

Servidor [FastMCP](https://github.com/jlowin/fastmcp) em transporte **stdio**. Expõe quatro tools e o resource `atlas://status`.

## Responsabilidades

- Redirecionar `stdout` → `stderr` até `main()` para não corromper JSON-RPC ([[stdio-stdout-redirecionado]])
- Resolver `.code-index` via [[resolve-index-dir]] ([[ADR-002-resolucao-index-dir]])
- Reindex em background no startup e via [[ADR-004-async-reindex]]
- Erros acionáveis quando índice ausente

## Telas

- [[atlas-search]]
- [[atlas-map]]
- [[atlas-index]]
- [[atlas-status]]

## Serviços

- [[resolve-index-dir]]
- `get_status_data` / `get_status_resource`

## Entidades

- [[index-manifest]]
- [[search-result]]
- [[index-stats]]

## Riscos e Limitações

- [[indice-stale]] — `is_stale` quando git HEAD diverge
- Dependência de índice pré-existente para search/map

## Links Relacionados

- [[MOC-MCP-Server]]
- [[server-testes]]
