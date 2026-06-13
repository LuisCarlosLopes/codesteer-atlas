---
tipo: adr
titulo: "ADR-008 — Backend de embeddings"
numero: 8
tags: [adr, embeddings]
status: aceito
criado: 2026-06-13
---

# ADR-008 — Backend de embeddings

## Contexto

Embeddings precisam ser locais, leves e com startup rápido.

## Decisão

[[fastembed]] ONNX com `all-MiniLM-L6-v2` (384-d). Descartados:

- `sentence-transformers` + PyTorch (peso/startup)
- APIs remotas (viola offline)

## Consequências

- `embedding_backend: fastembed` no manifest
- `MIN_INDEX_VERSION = 2.0.0` rejeita índices torch legados

## Afeta

- [[embedding-engine]]
- [[embeddings]]
- [[manifest-version-incompativel]]

## Fonte

- `tests/md-search/decisions.md` — Decisão 008
