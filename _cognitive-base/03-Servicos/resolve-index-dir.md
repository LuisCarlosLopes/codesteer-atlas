---
tipo: servico
titulo: "resolve_index_dir"
arquivo: "src/codesteer_atlas/server.py"
dominio: "[[mcp-server]]"
tags: [servico, config]
contrato: "Path"
criado: 2026-06-13
---

# resolve_index_dir

## Propósito

Resolve localização de `.code-index` seguindo [[ADR-002-resolucao-index-dir]].

## Funções Públicas

- `resolve_index_dir(cli_arg, cwd) → Path`

## Contrato de Retorno

`Path` absoluto; ordem: `--index-dir` → `ATLAS_INDEX_DIR` → discovery ascendente → `DEFAULT_INDEX_DIR`.

## Dependências

- `config.DEFAULT_INDEX_DIR`

## Comportamento Offline

Filesystem local apenas.

## Testes

- [[server-testes]] — precedência, discovery, fallback
