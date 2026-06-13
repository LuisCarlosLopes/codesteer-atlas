---
tipo: tela
titulo: "atlas_status"
dominio: "[[mcp-server]]"
rota: "MCP tool → codesteer-atlas/atlas_status"
tags: [tela, mcp, diagnostico]
status: implementado
relacionados: ["[[index-manifest]]"]
criado: 2026-06-13
---

# atlas_status

## Propósito

Diagnóstico do índice: existência, staleness, chunks, linguagens, resolução do diretório.

## Fluxo de Entrada

1. Chamada sem parâmetros
2. `get_status_data()` lê manifest + git HEAD atual
3. JSON com `is_stale`, `reindexing`, `index_resolution`

## Componentes Principais

- Resource MCP `atlas://status`
- `index_resolution`: `cli-arg` | `env` | `discovery` | `cwd-fallback`

## Serviços Consumidos

- [[storage-backend]]
- [[resolve-index-dir]]
- [[reindex-lock]] — probe `reindexing`

## Estados da Tela

| Campo | Significado |
|-------|-------------|
| `index_exists: false` | Precisa `atlas_index` |
| `is_stale: true` | [[indice-stale]] |
| `reindexing: true` | Lock ativo |

## Notas de Implementação

- Não indexa — apenas leitura
- Útil para diagnosticar misconfig de `ATLAS_INDEX_DIR`

## Links

- [[MOC-MCP-Server]]
