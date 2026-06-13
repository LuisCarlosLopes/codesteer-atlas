---
tipo: adr
titulo: "ADR-007 — Índice local"
numero: 7
tags: [adr, privacidade]
status: aceito
criado: 2026-06-13
---

# ADR-007 — Índice local

## Contexto

Código-fonte não pode ser enviado a serviços externos (constitution.md).

## Decisão

Índice em `.code-index/` local com [[lancedb]] embutido.

## Consequências

- Zero dependência de cloud para busca
- Backup/`.gitignore` responsabilidade do usuário

## Afeta

- [[armazenamento]]
- [[index-manifest]]

## Fonte

- `tests/md-search/decisions.md` — Decisão 007
