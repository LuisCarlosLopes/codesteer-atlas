---
tipo: qualidade
titulo: "storage-testes"
arquivo: "tests/test_storage.py"
tags: [testes, storage, busca]
dominio: "[[armazenamento]]"
criado: 2026-06-13
---

# storage-testes

## Escopo

`tests/test_storage.py` — LanceDB + busca híbrida.

## Cenários cobertos

- store/get manifest atômico
- append preserva rows + atualiza FTS
- delete múltiplos paths
- hybrid search com filtros path/language
- prefilter retorna top_k completo
- escape aspas em path_prefix
- manifest incompatível → RuntimeError

## Relacionados

- [[storage-backend]]
- [[ADR-002-busca-hibrida-rrf]]
