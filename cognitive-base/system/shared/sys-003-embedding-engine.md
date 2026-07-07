---
id: sys-003
type: service
title: "EmbeddingEngine — wrapper singleton fastembed"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: dec-005
    rel: depends-on
tags: [embeddings, fastembed]
source: greenfield
migration_status: ""
meta:
  module: "src/codesteer_atlas/embeddings.py"
  class: "EmbeddingEngine"
---

# EmbeddingEngine — wrapper singleton fastembed

## Responsabilidade

Encapsular `fastembed.TextEmbedding` (`all-MiniLM-L6-v2`, 384 dims). Expõe
`encode()` e `encode_single()`. Modelo carregado apenas no primeiro uso.

## Dependências

- fastembed (ONNX runtime)
- [[dec-004-indice-100-local]] — sem chamadas externas

## SLA

- Startup do MCP sem carregar modelo
- Vetores consistentes entre indexação e query do mesmo manifest

## Donos

Equipe CodeSteer Atlas · módulo `embeddings.py`

## Notas Relacionadas

- [[dec-005-backend-embeddings-fastembed]] — ADR do backend
- [[meta/glossary#embedding|embedding]]

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
