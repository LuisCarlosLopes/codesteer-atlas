---
tipo: dominio
titulo: "Embeddings"
aliases: [embeddings, vetorizacao]
tags: [dominio, embeddings, fastembed]
status: ativo
relacionados: ["[[indexacao]]", "[[busca]]"]
criado: 2026-06-13
atualizado: 2026-06-13
---

# Embeddings

## Visão Geral

Geração local de vetores 384-d com `all-MiniLM-L6-v2` via [[fastembed]] ([[ADR-008-backend-embeddings]]).

## Responsabilidades

- Singleton lazy — modelo carrega só no primeiro `encode`
- `encode` batch e `encode_single` para queries
- Dimensão fixa 384 alinhada a [[code-chunk]].vector

## Telas

- N/A — consumido internamente por [[atlas-search]] e [[index-workspace]]

## Serviços

- [[embedding-engine]]

## Entidades

- Campo `vector` em [[code-chunk]]

## Riscos e Limitações

- ⚠️ Cold start no primeiro encode (download/carregamento ONNX)
- Manifests de backend `sentence-transformers/torch` incompatíveis

## Links Relacionados

- [[embeddings-testes]]
- [[ADR-008-backend-embeddings]]
