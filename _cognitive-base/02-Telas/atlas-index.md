---
tipo: tela
titulo: "atlas_index"
dominio: "[[indexacao]]"
rota: "MCP tool → codesteer-atlas/atlas_index"
tags: [tela, mcp, indexacao]
status: implementado
relacionados: ["[[index-workspace]]"]
criado: 2026-06-13
---

# atlas_index

## Propósito

Dispara indexação incremental ou completa do workspace via MCP (espelha CLI `atlas-index`).

## Fluxo de Entrada

1. Parâmetros: `workspace`, `paths[]`, `full`, `dry_run`
2. `paths` vazio + `full=false` → async background ([[ADR-004-async-reindex]])
3. `paths` específicos → síncrono incremental parcial
4. Retorna [[index-stats]] ou status async

## Componentes Principais

- `dry_run` — estimativa sem persistir
- Validação anti-traversal em `paths`
- Docstring pede confirmação do usuário antes de indexar

## Serviços Consumidos

- [[index-workspace]]
- [[reindex-lock]]

## Estados da Tela

| Estado | Retorno |
|--------|---------|
| `reindex_in_progress` | `skipped_reason` em [[index-stats]] |
| `dry_run` | Contagem sem escrita |
| Sucesso | Stats com `chunks_persisted` |

## Notas de Implementação

- Lock compartilhado com CLI e background startup

## Links

- [[MOC-Indexacao]]
- [[indexer-testes]]
