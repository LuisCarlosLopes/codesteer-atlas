---
tipo: adr
titulo: "ADR-005 — Indexação incremental"
numero: 5
aliases: [DECISAO-005]
tags: [adr, indexacao]
status: aceito
criado: 2026-06-13
---

# ADR-005 — Indexação incremental (DECISAO-005)

## Contexto

Reindexar workspace inteiro a cada mudança é lento em projetos grandes.

## Decisão

- `manifest.files`: path → sha256
- `manifest.files_meta`: path → [mtime, size] para skip sem re-hash
- Arquivos alterados: `delete_by_file_paths` + `append_chunks`
- `--full` ou sem manifest: `store_chunks` overwrite

## Consequências

- [[index-stats]].`files_skipped_unchanged` reporta economia
- Arquivos deletados removidos do índice

## Afeta

- [[index-workspace]]
- [[index-manifest]]
- [[atlas-index]]
