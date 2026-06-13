---
tipo: entidade
titulo: "CodeChunk"
tabela: "chunks (LanceDB)"
tags: [entidade, dados, chunk]
rls: false
relacionamentos: ["[[index-manifest]]"]
criado: 2026-06-13
---

# CodeChunk

## Schema

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | str | Hash único do chunk |
| `file_path` | str | Path POSIX relativo |
| `repo` | str | Nome do repositório |
| `start_line` / `end_line` | int | Linhas 1-indexed |
| `scope_type` | str | class \| function \| method \| module |
| `scope_name` | str | Nome qualificado |
| `language` | str | Linguagem detectada |
| `content` | str | Texto do fragmento |
| `indexed_at` | str | ISO timestamp |
| `vector` | list[float] \| null | Embedding 384-d |

## Regras de Negócio

- Gerado por [[ast-chunker]]
- Truncado se exceder `MAX_TOKENS_PER_CHUNK`
- Removido quando arquivo deletado do workspace

## RLS (Row Level Security)

N/A — índice local single-user.

## Serviços que Escrevem

- [[index-workspace]] via [[storage-backend]]

## Serviços que Leem

- [[storage-backend]] — search, map
- [[atlas-search]]

## Migrações Relacionadas

- `index_version` 2.0.0 em [[index-manifest]]
