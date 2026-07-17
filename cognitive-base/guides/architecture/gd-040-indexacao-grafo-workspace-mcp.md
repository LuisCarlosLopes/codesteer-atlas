---
id: gd-040
type: how-to
title: "Indexação, grafo e workspace multi-repo — guia didático"
status: draft
created: "2026-07-07"
updated: "2026-07-07"
author: "@luiscarloslopes"
links:
  - id: gd-001
    rel: depends-on
  - id: gd-030
    rel: related-to
  - id: sys-004
    rel: related-to
  - id: dec-003
    rel: related-to
tags: [indexacao, grafo, mcp, workspace, guia]
source: greenfield
migration_status: ""
meta:
  doc_completo: "../../docs/guia-indexacao-grafo-mcp.md"
---

# Indexação, grafo e workspace multi-repo

## Contexto

Este guia resume como o Atlas indexa código, gera o grafo de conectividade e expõe
tudo via MCP. Para a versão completa com todos os diagramas, consulte
[Guia didático — Indexação, Grafo e MCP](../../docs/guia-indexacao-grafo-mcp.md).

Pressupõe [[gd-001-visao-geral-arquitetura]] e [[gd-030-primeiros-passos]].

## Pipeline em uma frase

`index_workspace()` varre arquivos → detecta mudanças por hash → chunk AST →
embed local → persiste no LanceDB → reconstrói `graph.json` + `graph.html`.

Ver [[dec-003-indexacao-incremental]] e [[sys-004-index-workspace]].

## Artefatos

| Arquivo | Uso |
|---------|-----|
| `.code-index/manifest.json` | Metadados e hashes ([[dec-002-resolucao-index-dir]]) |
| `.code-index/lancedb/` | Busca híbrida ([[dec-001-busca-hibrida-rrf]]) |
| `.code-index/graph.json` | `atlas_graph` |
| `.code-index/graph.html` | Viewer offline |

## Tools MCP — decisão rápida

| Pergunta | Tool |
|----------|------|
| Onde está X? | `atlas_search` |
| Estrutura do projeto | `atlas_map` |
| Conexões / rationale | `atlas_graph` |
| Saúde do índice | `atlas_status` |
| Reindexar | `atlas_index` |

## Grafo — três modos

```text
atlas_graph(mode="hubs")                              # nós centrais
atlas_graph(mode="explain", target="MeuSimbolo")     # vizinhança
atlas_graph(mode="path", source="A", target="B")    # caminho BFS
```

Arestas principais: `contains`, `imports`, `links_to`, `cites`, `annotates`.

## Multi-pasta e multi-repo

- **Várias subpastas:** `--paths repo-a --paths repo-b` no mesmo workspace.
- **Filtro na busca:** `path_prefix="repo-a/"` (não o campo `repo`).
- **MCP:** um `.code-index` por processo — prefira índice unificado na pasta pai.

## Notas Relacionadas

- [[gd-030-primeiros-passos|Primeiros passos]]
- [[ops-001-runbook-reindex|Runbook de reindex]]
- [[spc-001-api-ferramentas-mcp|API das tools MCP]]
