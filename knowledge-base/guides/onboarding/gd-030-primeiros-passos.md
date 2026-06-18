---
id: gd-030
type: how-to
title: "Primeiros passos — setup, indexação e uso do MCP"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: gd-001
    rel: depends-on
  - id: sys-005
    rel: related-to
tags: [onboarding, mcp, setup]
source: greenfield
migration_status: ""
meta: {}
---

# Primeiros passos — setup, indexação e uso do MCP

## Contexto

Guia para desenvolvedores e agentes que vão usar o Atlas pela primeira vez no
workspace. Pressupõe a visão arquitetural em [[gd-001-visao-geral-arquitetura]].

## Estrutura obrigatória

- Python 3.12+ com [uv](https://docs.astral.sh/uv/)
- Workspace com código-fonte indexável
- Editor com suporte MCP (Cursor, Claude Desktop, Cline, etc.)

## Passo a passo

### 1. Bootstrap do ambiente

```bash
./setup.sh          # macOS/Linux — uv sync + validação de imports
uv run python deploy_mcp.py --check
```

### 2. Indexar o workspace

```bash
uv run atlas-index --workspace .              # incremental (padrão)
uv run atlas-index --workspace . --full       # rebuild completo
uv run atlas-index --workspace . --paths src  # subárvore específica
```

O índice é gravado em `.code-index/` ([[dec-002-resolucao-index-dir]]).

### 3. Registrar o servidor MCP no editor

```bash
uv run python deploy_mcp.py
```

### 4. Usar as ferramentas MCP

| Objetivo | Ferramenta | Dica |
| -------- | ---------- | ---- |
| Encontrar implementação | `atlas_search` | Use `path_prefix` e `include_content=false` primeiro |
| Mapa do projeto | `atlas_map` | Visão estrutural sem ler arquivos inteiros |
| Verificar índice | `atlas_status` | `is_stale: true` → rodar `atlas_index` |
| Reindexar | `atlas_index` | Após mudanças grandes no código |

Fluxo recomendado para agentes: **descoberta** (`atlas_search` metadados) →
**detalhe** (`Read` nas linhas indicadas) → **confirmação literal** (`grep`).

### 5. Rodar testes

```bash
uv run pytest -v
uv run ruff check
```

## Checklist

- [ ] `setup.sh` concluiu sem erro
- [ ] `.code-index/manifest.json` existe após indexação
- [ ] `atlas_status` retorna índice válido e não stale
- [ ] MCP registrado no editor e `atlas_search` responde
- [ ] Regras do projeto (`CLAUDE.md`) orientam uso do Atlas antes de grep

## Notas Relacionadas

- [[gd-001-visao-geral-arquitetura]] — mapa dos componentes
- [[sys-005-mcp-server]] — contrato das ferramentas MCP
- [[dec-004-indice-100-local]] — princípio offline

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
