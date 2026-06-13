---
tipo: qualidade
titulo: "locking-testes"
arquivo: "tests/test_locking.py"
tags: [testes, locking]
dominio: "[[indexacao]]"
criado: 2026-06-13
---

# locking-testes

## Escopo

`tests/test_locking.py`

## Cenários cobertos

- Acquire/release lock
- Lock externo detectado
- Criação de index_dir se ausente
- OSError no probe → false + log

## Relacionados

- [[reindex-lock]]
- [[ADR-001-reindex-lock]]
