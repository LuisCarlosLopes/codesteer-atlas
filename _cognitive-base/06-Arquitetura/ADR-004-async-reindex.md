---
tipo: adr
titulo: "ADR-004 — Reindex async"
numero: 4
aliases: [DECISAO-004]
tags: [adr, mcp, async]
status: aceito
criado: 2026-06-13
---

# ADR-004 — Reindex async (DECISAO-004)

## Contexto

`atlas_index` com `paths` vazio ou `full=true` pode demorar; bloquear stdio MCP prejudica o cliente.

## Decisão

- `paths` vazio ou `full=true` → spawn subprocess/background, retorno imediato de status
- `paths` não vazio + `full=false` → síncrono incremental parcial
- Startup do servidor dispara reindex background se índice existir

## Consequências

- [[atlas-index]] não bloqueia sessão MCP em rebuild completo
- `_safe_responder_respond` evita resposta tardia duplicada

## Afeta

- [[mcp-server]]
- [[atlas-index]]
- [[server-testes]]
