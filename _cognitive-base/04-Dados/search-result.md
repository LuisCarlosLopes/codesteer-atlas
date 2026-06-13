---
tipo: entidade
titulo: "SearchResult"
tabela: "N/A (DTO de resposta)"
tags: [entidade, dados, busca]
rls: false
relacionamentos: ["[[code-chunk]]"]
criado: 2026-06-13
---

# SearchResult

## Schema

| Campo | Tipo |
|-------|------|
| `file_path` | str |
| `start_line` / `end_line` | int |
| `scope_type` | str |
| `scope_name` | str |
| `language` | str |
| `content` | str \| null |
| `score` | float |
| `repo` | str |

## Regras de Negócio

- `score` = RRF fusion ([[ADR-002-busca-hibrida-rrf]])
- `content` omitido quando `include_content=false`
- Chunks MD podem incluir `markdown_references` (campo extra na serialização MCP)

## RLS

N/A

## Serviços que Escrevem

- [[storage-backend]].`search_hybrid`

## Serviços que Leem

- [[atlas-search]] → cliente MCP

## Migrações Relacionadas

N/A
