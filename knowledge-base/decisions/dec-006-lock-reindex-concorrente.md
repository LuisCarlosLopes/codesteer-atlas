---
id: dec-006
type: adr
title: "Lock de arquivo para reindexações concorrentes"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: ops-004
    rel: related-to
  - id: sys-004
    rel: related-to
tags: [locking, indexacao, concorrencia]
source: greenfield
migration_status: ""
meta: {}
---

# Lock de arquivo para reindexações concorrentes

## Contexto

Reindex em background (subprocesso) e chamadas MCP/CLI simultâneas podem corromper
manifest ou tabela LanceDB se escreverem ao mesmo tempo.

## Decisão

`reindex_lock(index_dir)` em `locking.py` usa `filelock.FileLock` em
`.code-index/.reindex.lock` com **timeout=0** (não-bloqueante). Se o lock não for
adquirido, `index_workspace()` retorna `skipped_reason="reindex_in_progress"`.
`atlas_status` expõe `reindexing` via `is_reindex_locked()`.

## Alternativas Consideradas

| Alternativa | Contras |
| ----------- | ------- |
| Sem coordenação | Corrupção de índice |
| Lock bloqueante com fila | MCP tools não devem esperar indefinidamente |
| **Lock não-bloqueante + skip** | Segunda chamada falha graciosamente; operador poll status |

## Consequências

- Operadores seguem [[ops-004-runbook-reindex-em-progresso]]
- Locks órfãos após crash exigem remoção manual do arquivo
- Probe do lock no Windows pode falhar com `OSError` — status assume não bloqueado e loga em stderr

## Notas Relacionadas

- [[ops-004-runbook-reindex-em-progresso]] — procedimento operacional
- [[dec-003-indexacao-incremental]] — escrita incremental sob lock

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
