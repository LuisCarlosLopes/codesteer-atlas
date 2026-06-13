---
tipo: integracao
titulo: "fastembed"
tags: [integracao, embeddings, onnx]
dominio: "[[embeddings]]"
criado: 2026-06-13
---

# fastembed

SDK local ONNX para embeddings. Backend escolhido em [[ADR-008-backend-embeddings]] — substitui `sentence-transformers`/PyTorch.

## Uso no Atlas

- [[embedding-engine]] — `TextEmbedding(model_name)`
- Modelo: `sentence-transformers/all-MiniLM-L6-v2`

## Contrato

- 384 dimensões
- Lazy load no primeiro encode

## Links

- [[embeddings]]
- [[fastembed]] ← pacote Python `fastembed`
