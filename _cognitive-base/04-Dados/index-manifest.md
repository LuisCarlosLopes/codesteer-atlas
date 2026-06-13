---
tipo: entidade
titulo: "IndexManifest"
tabela: "manifest.json"
tags: [entidade, dados, manifest]
rls: false
relacionamentos: ["[[code-chunk]]"]
criado: 2026-06-13
---

# IndexManifest

## Schema

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `total_chunks` | int | Total no índice |
| `repos_indexed` | list[str] | Repos presentes |
| `embedding_model` | str | Nome do modelo |
| `embedding_dim` | int | 384 |
| `embedding_backend` | str | fastembed |
| `storage_backend` | str | lancedb |
| `last_indexed_at` | str | ISO |
| `git_head_sha` | str \| null | HEAD no index |
| `languages_indexed` | list[str] | Linguagens |
| `index_version` | str | 2.0.0 |
| `files` | dict | path → sha256 |
| `files_meta` | dict | path → [mtime, size] |

## Regras de Negócio

- Escrita atômica (tmp + rename)
- `files`/`files_meta` habilitam skip incremental
- Versão < `MIN_INDEX_VERSION` → erro acionável

## RLS

N/A

## Serviços que Escrevem

- [[storage-backend]] — `store_chunks`, `update_manifest_after_incremental`

## Serviços que Leem

- [[atlas-status]]
- [[index-workspace]]

## Migrações Relacionadas

- [[manifest-version-incompativel]] — reindex `--full` obrigatório
