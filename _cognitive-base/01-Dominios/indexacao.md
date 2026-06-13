---
tipo: dominio
titulo: "Indexação"
aliases: [indexacao]
tags: [dominio, indexacao, ast]
status: ativo
relacionados: ["[[armazenamento]]", "[[embeddings]]"]
criado: 2026-06-13
atualizado: 2026-06-13
---

# Indexação

## Visão Geral

Domínio responsável por varrer o workspace, ignorar arquivos irrelevantes, fatiar código via Tree-sitter e persistir chunks com embeddings no índice local `.code-index/`.

## Responsabilidades

- Scan recursivo com `IGNORE_DIRS` e `.atlasignore`
- Hash sha256 para indexação incremental ([[ADR-005-indexacao-incremental]])
- Coordenação de lock entre processos ([[ADR-001-reindex-lock]])
- Extração de referências markdown em chunks MD ([[markdown-links]])

## Telas

- [[atlas-index]] — tool MCP e CLI `atlas-index`

## Serviços

- [[index-workspace]] — núcleo `index_workspace()`
- [[ast-chunker]] — parsing AST
- [[reindex-lock]] — exclusão mútua
- [[markdown-links]] — refs `[[wikilink]]` em MD

## Entidades

- [[code-chunk]]
- [[index-stats]]

## Riscos e Limitações

- [[reindex-em-progresso]] — segunda indexação pode ser ignorada
- Arquivos > 2MB ignorados (`MAX_FILE_SIZE` em `config.py`)
- ⚠️ Chunks oversized truncados preservando assinatura

## Links Relacionados

- [[MOC-Indexacao]]
- [[indexer-testes]]
