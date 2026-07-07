---
id: ops-004
type: runbook
title: "Runbook — reindex em progresso (lock concorrente)"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: ops-001
    rel: related-to
  - id: sys-004
    rel: triggered-by
tags: [runbook, locking, concorrencia]
source: greenfield
migration_status: ""
meta:
  lock_file: ".code-index/.reindex.lock"
---

# Runbook — reindex em progresso (lock concorrente)

## Contexto

Múltiplos processos não podem reindexar o mesmo `.code-index/` simultaneamente.
O lock `.reindex.lock` (DECISAO-001, `filelock` com `timeout=0`) coordena
acesso. Segunda chamada retorna `skipped_reason: "reindex_in_progress"`.

## Pré-requisitos

- `atlas_status.reindexing: true`, ou
- Resposta de `atlas_index` com `status: "skipped"` / `skipped_reason`

## Diagnóstico

```text
atlas_status() → reindexing: true
```

```bash
ls -la .code-index/.reindex.lock
# Verificar processo em background (reindex assíncrono)
ps aux | grep atlas-index
```

Causas comuns:

- `atlas_index(full=true)` ou indexação de workspace inteiro (subprocesso)
- CLI `atlas-index` rodando em paralelo
- Lock órfão após crash do processo

## Procedimento

### Caso normal — aguardar

1. Poll `atlas_status` a cada 10–30s
2. Quando `reindexing: false`, validar `total_chunks` e `is_stale`
3. Não iniciar segundo reindex enquanto `true`

### Lock órfão (processo morto, lock persistiu)

1. Confirmar que nenhum `atlas-index` está ativo
2. Remover lock manualmente:

```bash
rm -f .code-index/.reindex.lock
```

3. Reindexar se necessário: [[ops-001-runbook-reindex]]

### Indexação parcial bloqueada

Use caminho **síncrono** com escopo restrito:

```text
atlas_index(paths=["src/codesteer_atlas"], full=false)
```

Respeita o mesmo lock — se ainda bloqueado, aguardar ou remover lock órfão.

## Rollback

Remover `.code-index/` só se o índice estiver corrompido após crash — ver
[[ops-003-runbook-manifest-incompativel]].

## Notas Relacionadas

- [[spc-001-api-ferramentas-mcp#atlas_index|atlas_index]] — modos sync/async
- [[sys-004-index-workspace]] — adquire lock internamente

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
