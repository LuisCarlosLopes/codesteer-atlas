---
tipo: risco
titulo: "Índice stale"
tags: [risco, git, staleness]
severidade: media
criado: 2026-06-13
---

# Índice stale

## Descrição

`atlas_status.is_stale = true` quando `git_head_sha` do [[index-manifest]] difere do HEAD atual do workspace.

## Impacto

Agente pode buscar código desatualizado sem perceber.

## Mitigação

- Rodar [[atlas-index]] após mudanças significativas
- Checar [[atlas-status]] antes de sessões longas

## Relacionados

- [[mcp-server]]
- [[index-manifest]]
