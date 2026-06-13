---
tipo: entidade
titulo: "IndexStats"
tabela: "N/A (DTO de resposta)"
tags: [entidade, dados, indexacao]
rls: false
relacionamentos: ["[[index-manifest]]"]
criado: 2026-06-13
---

# IndexStats

## Schema

| Campo | Tipo |
|-------|------|
| `files_processed` | int |
| `files_skipped_unchanged` | int |
| `files_removed` | int |
| `chunks_persisted` | int |
| `duration_s` | float |
| `git_head_sha` | str \| null |
| `skipped_reason` | str \| null |

## Regras de Negócio

- Retornado por [[index-workspace]] ([[ADR-005-indexacao-incremental]])
- `skipped_reason='reindex_in_progress'` quando lock ocupado

## RLS

N/A

## Serviços que Escrevem

- [[index-workspace]]

## Serviços que Leem

- [[atlas-index]]
- CLI `atlas-index`

## Migrações Relacionadas

N/A
