---
id: ops-001
type: runbook
title: "Runbook — reindexar o workspace"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: spc-001
    rel: depends-on
  - id: dec-003
    rel: depends-on
tags: [runbook, indexacao, reindex]
source: greenfield
migration_status: ""
meta: {}
---

# Runbook — reindexar o workspace

## Contexto

Procedimento para construir ou atualizar o índice em `.code-index/`. Aplica-se
quando o índice não existe, após mudanças grandes no código, ou quando
[[ops-002-runbook-indice-stale]] indica staleness.

## Pré-requisitos

- Ambiente bootstrapado (`./setup.sh`)
- Workspace acessível
- Sem outro reindex em andamento (ver [[ops-004-runbook-reindex-em-progresso]])

## Diagnóstico

```bash
# Via CLI
ls -la .code-index/manifest.json

# Via MCP
atlas_status → index_exists, is_stale, reindexing, index_path
```

## Procedimento

### Indexação incremental (padrão)

```bash
uv run atlas-index --workspace .
```

Via MCP (subpasta, síncrono):

```text
atlas_index(paths=["src"], full=false)
```

### Rebuild completo

```bash
uv run atlas-index --workspace . --full
```

Via MCP (workspace inteiro, **assíncrono**):

```text
atlas_index(full=true)
# Poll: atlas_status até reindexing=false
```

### Primeira indexação (agente MCP)

1. `atlas_index(dry_run=true)` — apresentar `candidates` ao usuário
2. Confirmar escopo (tudo ou `paths` específicos)
3. `atlas_index(paths=[...])` ou `atlas_index()` para workspace inteiro
4. `atlas_status` — validar `index_exists: true`, `is_stale: false`

## Rollback

- Índice corrompido: remover `.code-index/` e reindexar com `--full`
- Manifest incompatível: seguir [[ops-003-runbook-manifest-incompativel]]

## Notas Relacionadas

- [[spc-001-api-ferramentas-mcp#atlas_index|atlas_index]]
- [[dec-003-indexacao-incremental]] — comportamento incremental

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
