---
id: dec-004
type: adr
title: "Índice e embeddings 100% locais — sem envio de código externo"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: dec-005
    rel: depends-on
tags: [seguranca, privacidade, constitution]
source: greenfield
migration_status: ""
meta: {}
---

# Índice e embeddings 100% locais — sem envio de código externo

## Contexto

O Atlas indexa código-fonte proprietário. Enviar chunks para APIs de embedding
ou busca na nuvem violaria confidencialidade e dependência de rede.

## Decisão

Toda a pipeline — parsing, embedding, armazenamento e busca — roda **localmente
e offline**. Nenhum código-fonte é transmitido a serviços externos. Ver também
`.memory-bank/constitution.md`.

## Alternativas Consideradas

| Alternativa | Contras |
| ----------- | ------- |
| OpenAI/Cohere embeddings | Vazamento de IP, latência, custo |
| Busca SaaS (Sourcegraph cloud) | Mesmos riscos |
| **fastembed ONNX local** | Modelo menor; qualidade suficiente para código |

## Consequências

- Backend de embeddings fixado em fastembed ([[dec-005-backend-embeddings-fastembed]])
- LanceDB embedded, não servidor remoto
- `MIN_INDEX_VERSION` força reindex ao trocar backend de embedding

## Notas Relacionadas

- [[dec-005-backend-embeddings-fastembed]] — escolha do modelo ONNX
- [[gd-001-visao-geral-arquitetura]] — princípio transversal

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
