---
tipo: servico
titulo: "EmbeddingEngine"
arquivo: "src/codesteer_atlas/embeddings.py"
dominio: "[[embeddings]]"
tags: [servico, embeddings]
contrato: "list[list[float]] | list[float]"
criado: 2026-06-13
---

# EmbeddingEngine

## Propósito

Singleton lazy que encapsula [[fastembed]] para vetorização de chunks e queries.

## Funções Públicas

- `encode(texts: list[str]) → list[list[float]]`
- `encode_single(text: str) → list[float]`

## Contrato de Retorno

Vetores 384-d float; modelo `sentence-transformers/all-MiniLM-L6-v2`.

## Dependências

- [[fastembed]]
- [[ADR-008-backend-embeddings]]

## Comportamento Offline

Modelo ONNX local após primeiro download.

## Testes

- [[embeddings-testes]]
