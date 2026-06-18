---
id: ops-003
type: runbook
title: "Runbook — manifest com versão incompatível"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: dec-005
    rel: depends-on
  - id: ops-001
    rel: triggered-by
tags: [runbook, manifest, migracao]
source: greenfield
migration_status: ""
meta: {}
---

# Runbook — manifest com versão incompatível

## Contexto

`StorageBackend.get_manifest()` levanta `RuntimeError` quando
`manifest.index_version` é menor que `MIN_INDEX_VERSION` (`2.0.0` em
`config.py`). Típico após migração do backend sentence-transformers/torch para
[[dec-005-backend-embeddings-fastembed|fastembed ONNX]].

## Pré-requisitos

- Mensagem de erro mencionando versão do índice ou reindex obrigatório
- Backup opcional de `.code-index/` se houver dados a preservar para auditoria

## Diagnóstico

```bash
cat .code-index/manifest.json | grep index_version
# Se < 2.0.0 → incompatível

uv run atlas-index --workspace .  # falha com RuntimeError acionável
```

Sintomas:

- `atlas_search` / `atlas_map` falham ao abrir storage
- `atlas_status` pode retornar campo `error` no diagnóstico

## Procedimento

1. **Parar** processos MCP que usem o índice
2. **Remover** o índice legado (embeddings incompatíveis não são reutilizáveis):

```bash
rm -rf .code-index/
```

3. **Reindexar** do zero:

```bash
uv run atlas-index --workspace . --full
```

4. Validar:

```text
atlas_status()
→ index_exists: true
→ embedding_backend: "fastembed"
→ total_chunks > 0
```

## Rollback

Restaurar backup de `.code-index/` só se for aceitável manter índice legado —
ainda exigirá downgrade de código ou reindex de qualquer forma.

## Notas Relacionadas

- [[dec-005-backend-embeddings-fastembed]] — causa da incompatibilidade
- [[ops-001-runbook-reindex]] — rebuild completo

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
