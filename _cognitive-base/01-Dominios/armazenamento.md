---
tipo: dominio
titulo: "Armazenamento"
aliases: [armazenamento, storage]
tags: [dominio, lancedb, persistencia]
status: ativo
relacionados: ["[[indexacao]]", "[[busca]]"]
criado: 2026-06-13
atualizado: 2026-06-13
---

# Armazenamento

## Visão Geral

Persistência local em LanceDB embutido + `manifest.json` na pasta `.code-index/` ([[ADR-007-indice-local]]). Nenhum dado sai da máquina.

## Responsabilidades

- `store_chunks` — rebuild completo
- `append_chunks` / `delete_by_file_paths` — incremental
- Índice FTS BM25 atualizado incrementalmente
- Validação `MIN_INDEX_VERSION` ao ler manifest

## Telas

- [[atlas-status]] — metadados do índice

## Serviços

- [[storage-backend]]

## Entidades

- [[index-manifest]]
- [[code-chunk]] — linhas na tabela LanceDB

## Riscos e Limitações

- [[manifest-version-incompativel]] — manifests < 2.0.0 exigem `--full`
- Escrita atômica de manifest via arquivo temporário

## Links Relacionados

- [[MOC-Armazenamento]]
- [[lancedb]]
