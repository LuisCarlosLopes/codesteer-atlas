---
id: dec-002
type: adr
title: "Resolução do diretório .code-index em múltiplos contextos"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: sys-005
    rel: related-to
tags: [mcp, deploy, indexacao]
source: greenfield
migration_status: ""
meta: {}
---

# Resolução do diretório .code-index em múltiplos contextos

## Contexto

O servidor MCP pode ser lançado com CWD = HOME (plugin global), com variáveis de
ambiente do editor, ou via CLI com `--index-dir`. Sem regra clara, o índice cairia
fora do projeto ou em local errado.

## Decisão

`resolve_index_dir()` em `server.py` resolve `.code-index` nesta ordem:

1. `--index-dir` (CLI)
2. `ATLAS_INDEX_DIR` (env)
3. Descoberta ascendente a partir do CWD (estilo git)
4. Descoberta ascendente a partir da raiz do editor (`CLAUDE_PROJECT_DIR`, `WORKSPACE_FOLDER_PATHS`)
5. Fallback para `DEFAULT_INDEX_DIR` relativo ao CWD ou raiz do editor

Quando o startup cai em fallback, ferramentas MCP fazem upgrade via **MCP roots**
(`roots/list`) para apontar o índice ao workspace real do cliente.

## Alternativas Consideradas

| Alternativa | Contras |
| ----------- | ------- |
| Índice fixo em HOME | Poluição entre projetos |
| Só env var | Fricção de configuração manual |
| Só CWD | Quebra com plugins globais |
| **Cadeia + MCP roots** | Mais complexo, mas funciona em todos os clientes |

## Consequências

- `atlas_status` reporta `index_resolution.source` para diagnóstico
- Clientes sem suporte a `roots` degradam graciosamente com timeout guard
- Nunca sobrescreve resolução explícita (`cli-arg`, `env`, `discovery`)

## Notas Relacionadas

- [[sys-005-mcp-server]] — onde a resolução ocorre
- [[gd-030-primeiros-passos]] — indexação no projeto correto

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
