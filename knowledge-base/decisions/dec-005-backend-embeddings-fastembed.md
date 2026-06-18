---
id: dec-005
type: adr
title: "Backend de embeddings fastembed (ONNX) em vez de PyTorch"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: dec-004
    rel: depends-on
  - id: sys-003
    rel: related-to
tags: [embeddings, performance]
source: greenfield
migration_status: ""
meta: {}
---

# Backend de embeddings fastembed (ONNX) em vez de PyTorch

## Contexto

A geração anterior usava `sentence-transformers`/PyTorch — pesado para startup do
MCP e instalação. O servidor deve iniciar rápido (stdio MCP).

## Decisão

`EmbeddingEngine` usa `fastembed.TextEmbedding` com modelo
`sentence-transformers/all-MiniLM-L6-v2` (384 dimensões, ONNX). Carregamento
**lazy** — só no primeiro `encode()`.

## Alternativas Consideradas

| Alternativa | Contras |
| ----------- | ------- |
| sentence-transformers + torch | Startup lento, deps pesadas |
| Modelos maiores (e5-large) | Latência e RAM |
| **fastembed MiniLM** | Equilíbrio qualidade/velocidade; alinhado ao 100% local |

## Consequências

- Manifests de índices antigos levantam `RuntimeError` acionável pedindo reindex
- `MIN_INDEX_VERSION` em `config.py` guarda compatibilidade
- Singleton evita recarregar modelo por request

## Notas Relacionadas

- [[sys-003-embedding-engine]] — wrapper singleton
- [[dec-004-indice-100-local]] — requisito de privacidade

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
