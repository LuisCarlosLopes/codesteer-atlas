---
tipo: servico
titulo: "reindex_lock"
arquivo: "src/codesteer_atlas/locking.py"
dominio: "[[indexacao]]"
tags: [servico, concorrencia]
contrato: "bool (context manager)"
criado: 2026-06-13
---

# reindex_lock

## Propósito

Coordena reindexações concorrentes entre CLI, MCP e subprocess ([[ADR-001-reindex-lock]]).

## Funções Públicas

- `reindex_lock(index_dir)` — context manager, yields `True` se adquiriu
- `is_reindex_locked(index_dir) → bool`

## Contrato de Retorno

Lock file `.reindex.lock` em `index_dir`.

## Dependências

- `config.REINDEX_LOCK_FILENAME`

## Comportamento Offline

N/A

## Testes

- [[locking-testes]]
