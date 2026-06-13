---
tipo: adr
titulo: "ADR-002 — Resolução do diretório de índice"
numero: 2
aliases: [DECISAO-002]
tags: [adr, config]
status: aceito
criado: 2026-06-13
---

# ADR-002 — Resolução do diretório de índice (DECISAO-002)

## Contexto

Clientes MCP rodam com CWD variável; o índice deve ser encontrado sem config manual.

## Decisão

Cadeia de resolução em [[resolve-index-dir]]:

1. `--index-dir` CLI
2. `ATLAS_INDEX_DIR` env
3. Discovery ascendente (estilo git) por `.code-index`
4. Fallback `DEFAULT_INDEX_DIR` relativo ao CWD

## Consequências

- `atlas_status.index_resolution` diagnostica origem
- Modo `uvx` funciona sem paths absolutos

## Afeta

- [[mcp-server]]
- [[atlas-status]]
- [[deploy]]
