---
id: sys-001
type: service
title: "ASTChunker — extração de chunks por símbolo Tree-sitter"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: sys-004
    rel: triggered-by
tags: [chunker, tree-sitter, indexacao]
source: greenfield
migration_status: ""
meta:
  module: "src/codesteer_atlas/chunker.py"
  class: "ASTChunker"
---

# ASTChunker — extração de chunks por símbolo Tree-sitter

## Responsabilidade

Parsear arquivos com `tree_sitter_language_pack`, percorrer a AST e emitir
`CodeChunk`s em granularidade de classe, função ou método. Fallback para chunk
de módulo inteiro quando não há parser ou símbolos.

## Dependências

- `tree_sitter_language_pack` — parsers por extensão (`SUPPORTED_EXTENSIONS`)
- `config.MAX_TOKENS_PER_CHUNK` — truncamento preservando assinatura

## SLA

- Deve produzir chunks indexáveis para todas as extensões suportadas
- Chunks oversized são truncados, nunca descartados silenciosamente

## Donos

Equipe CodeSteer Atlas · módulo `chunker.py`

## Notas Relacionadas

- [[gd-001-visao-geral-arquitetura]] — posição no pipeline de indexação
- [[meta/glossary#simbolo|Símbolo]] e [[meta/glossary#chunk|chunk]]

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
