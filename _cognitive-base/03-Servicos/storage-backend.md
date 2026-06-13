---
tipo: servico
titulo: "StorageBackend"
arquivo: "src/codesteer_atlas/storage.py"
dominio: "[[armazenamento]]"
tags: [servico, storage, lancedb]
contrato: "list[SearchResult] | IndexManifest"
criado: 2026-06-13
---

# StorageBackend

## Propósito

Camada de persistência e busca sobre [[lancedb]] + `manifest.json`.

## Funções Públicas

- `search_hybrid(query_vector, query_text, ...) → list[SearchResult]`
- `store_chunks(chunks)` — full rebuild
- `append_chunks(chunks)` / `delete_by_file_paths(paths)`
- `get_manifest()` / `update_manifest_after_incremental()`
- `get_symbols(path_prefix, max_depth)`
- `get_sections_by_file_path(path)`

## Contrato de Retorno

[[search-result]] com `score` RRF; manifest como [[index-manifest]].

## Dependências

- [[lancedb]]
- [[ADR-002-busca-hibrida-rrf]]
- [[ADR-003-fts-prefilter]]

## Comportamento Offline

Banco embutido em `.code-index/`.

## Testes

- [[storage-testes]]
