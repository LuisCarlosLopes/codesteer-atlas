---
tipo: risco
titulo: "Manifest version incompatível"
tags: [risco, migracao, legado]
severidade: alta
criado: 2026-06-13
---

# Manifest version incompatível

## Descrição

Manifests com `index_version < MIN_INDEX_VERSION` (2.0.0) usavam backend `sentence-transformers`/torch incompatível com [[fastembed]].

## Impacto

`StorageBackend.get_manifest()` levanta `RuntimeError` acionável exigindo `atlas-index --full`.

## Mitigação

- Reindexação completa obrigatória na migração
- Documentado em README e CLAUDE.md

## Relacionados

- [[ADR-008-backend-embeddings]]
- [[storage-testes]]
