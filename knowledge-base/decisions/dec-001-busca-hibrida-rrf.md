---
id: dec-001
type: adr
title: "Busca híbrida com fusão RRF (vetorial + BM25)"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: sys-002
    rel: related-to
tags: [busca, rrf, arquitetura]
source: greenfield
migration_status: ""
meta: {}
---

# Busca híbrida com fusão RRF (vetorial + BM25)

## Contexto

Agentes precisam encontrar código por **intenção** ("onde autentica o usuário?")
e por **termos exatos** (nome de símbolo, mensagem de erro). Busca só vetorial
perde matches lexicais precisos; busca só textual perde paráfrase semântica.

## Decisão

Combinar similaridade vetorial (cosseno sobre embeddings) com BM25 full-text no
LanceDB, fundindo rankings via **Reciprocal Rank Fusion** ([[meta/glossary#rrf|RRF]])
com constante `RRF_K` definida em `config.py`.

## Alternativas Consideradas

| Alternativa | Prós | Contras |
| ----------- | ---- | ------- |
| Só vetorial | Simples, boa paráfrase | Falha em nomes exatos |
| Só BM25/FTS | Ótimo para símbolos | Fraco em linguagem natural |
| Score linear ponderado | Controle fino dos pesos | Requer tuning por projeto |
| **RRF (escolhida)** | Robusto sem calibrar pesos | Menos interpretável que score único |

## Consequências

- `StorageBackend.search_hybrid()` executa ambas as queries com prefiltros
- Resultados retornam como `SearchResult` com score fusionado
- Embeddings devem ser consistentes entre indexação e query ([[dec-005-backend-embeddings-fastembed]])

## Notas Relacionadas

- [[sys-002-storage-backend]] — implementação da busca híbrida
- [[gd-001-visao-geral-arquitetura]] — posição no pipeline

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
