---
id: sys-002
type: service
title: "StorageBackend — LanceDB, FTS e busca híbrida"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: dec-001
    rel: depends-on
tags: [lancedb, storage, busca]
source: greenfield
migration_status: ""
meta:
  module: "src/codesteer_atlas/storage.py"
  class: "StorageBackend"
---

# StorageBackend — LanceDB, FTS e busca híbrida

## Responsabilidade

Toda interação com LanceDB e `manifest.json`: persistência de chunks,
`search_hybrid` (vetor + BM25 + RRF), delete/append incremental e validação de
`MIN_INDEX_VERSION`.

## Dependências

- LanceDB embedded em `.code-index/`
- [[sys-003-embedding-engine]] — vetores na indexação e query
- `config.RRF_K`, `CANDIDATES_LIMIT`

## SLA

- Busca híbrida com prefiltros (`path_prefix`, `language`)
- Manifest incompatível → `RuntimeError` com instrução de reindex

## Donos

Equipe CodeSteer Atlas · módulo `storage.py`

## Notas Relacionadas

- [[dec-001-busca-hibrida-rrf]] — decisão de fusão RRF
- [[meta/glossary#fts|FTS]] e [[meta/glossary#lancedb|LanceDB]]

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
