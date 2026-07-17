---
id: ops-002
type: runbook
title: "Runbook — índice stale (git HEAD divergente)"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: ops-001
    rel: triggered-by
  - id: dec-003
    rel: depends-on
tags: [runbook, stale, git]
source: greenfield
migration_status: ""
meta: {}
---

# Runbook — índice stale (git HEAD divergente)

## Contexto

`atlas_status` retorna `is_stale: true` quando o `git_head_sha` gravado no
[[meta/glossary#manifest|manifest]] difere do HEAD atual do workspace pai do
índice. O índice ainda funciona, mas pode não refletir commits recentes.

## Pré-requisitos

- Repositório git com HEAD válido
- Acesso a `atlas_status` ou leitura de `manifest.json`

## Diagnóstico

```text
atlas_status()
→ is_stale: true
→ git_head_sha: "abc..." (indexado)
→ compare com: git rev-parse HEAD
```

Causas comuns:

- `git pull`, merge ou rebase após última indexação
- Checkout de branch sem reindex
- Indexação feita em commit anterior

**Nota:** se não houver git, `git_head_sha` pode ser `null` e `is_stale` fica `false`.

## Procedimento

1. Confirmar que mudanças no código justificam reindex (não é obrigatório a cada commit)
2. Executar [[ops-001-runbook-reindex|reindex incremental]]:

```bash
uv run atlas-index --workspace .
```

ou via MCP:

```text
atlas_index(paths=["src"])   # escopo menor, síncrono
```

3. Validar:

```text
atlas_status() → is_stale: false
```

## Rollback

Não aplicável — reindex incremental é seguro; arquivos inalterados são pulados
([[dec-003-indexacao-incremental]]).

## Notas Relacionadas

- [[spc-001-api-ferramentas-mcp#atlas_status|atlas_status]]
- [[gd-030-primeiros-passos]] — fluxo recomendado para agentes

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
