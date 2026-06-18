---
id: sys-004
type: service
title: "index_workspace — núcleo de indexação do workspace"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: dec-003
    rel: depends-on
  - id: sys-001
    rel: depends-on
  - id: sys-002
    rel: depends-on
tags: [indexacao, indexer]
source: greenfield
migration_status: ""
meta:
  module: "src/codesteer_atlas/indexer.py"
  function: "index_workspace"
---

# index_workspace — núcleo de indexação do workspace

## Responsabilidade

Core reutilizável de indexação (CLI `atlas-index` e MCP `atlas_index`): scan do
workspace, validação anti-traversal de `paths`, hash sha256, chunk, embed,
persistência full ou incremental.

## Dependências

- [[sys-001-ast-chunker]]
- [[sys-003-embedding-engine]]
- [[sys-002-storage-backend]]
- `should_ignore()`, `get_git_head_sha()`

## SLA

- Incremental por padrão; `--full` força rebuild
- `dry_run` no MCP para estimar trabalho sem persistir

## Donos

Equipe CodeSteer Atlas · módulo `indexer.py`

## Notas Relacionadas

- [[dec-003-indexacao-incremental]] — estratégia de hashes
- [[gd-030-primeiros-passos]] — comandos CLI

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
