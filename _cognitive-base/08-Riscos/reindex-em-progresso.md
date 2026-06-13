---
tipo: risco
titulo: "Reindex em progresso"
tags: [risco, concorrencia]
severidade: baixa
criado: 2026-06-13
---

# Reindex em progresso

## Descrição

Segunda tentativa de indexação enquanto [[reindex-lock]] está ativo retorna sem processar.

## Impacto

`IndexStats.skipped_reason = 'reindex_in_progress'` — usuário pode achar que indexou quando não indexou.

## Mitigação

- Consultar `atlas_status.reindexing`
- Aguardar conclusão do processo em background

## Relacionados

- [[ADR-001-reindex-lock]]
- [[atlas-index]]
