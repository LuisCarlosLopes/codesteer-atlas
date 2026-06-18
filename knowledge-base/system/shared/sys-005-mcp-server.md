---
id: sys-005
type: service
title: "Servidor MCP FastMCP — ferramentas atlas_*"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: dec-002
    rel: depends-on
tags: [mcp, server, fastmcp]
source: greenfield
migration_status: ""
meta:
  module: "src/codesteer_atlas/server.py"
---

# Servidor MCP FastMCP — ferramentas atlas_*

## Responsabilidade

Servidor MCP (`FastMCP("CodeSteer Atlas")`) via transporte
[[meta/glossary#stdio-transport|stdio]]. Expõe:

| Ferramenta | Função |
| ---------- | ------ |
| `atlas_search` | Busca híbrida (metadados por padrão) |
| `atlas_map` | Mapa estrutural do projeto |
| `atlas_index` | Indexação (com `dry_run`) |
| `atlas_status` | Saúde e staleness do índice |

Recurso: `atlas://status`

## Dependências

- Redirecionamento de `sys.stdout` → `stderr` até `main()` (canal JSON-RPC limpo)
- [[dec-002-resolucao-index-dir]] — localização do índice
- [[spc-001-api-ferramentas-mcp|Contrato MCP]] — ferramentas `atlas_*`

## SLA

- Startup rápido (deps pesadas após redirect de stdout)
- MCP roots upgrade quando resolução cai em fallback

## Donos

Equipe CodeSteer Atlas · módulo `server.py`

## Notas Relacionadas

- [[gd-030-primeiros-passos]] — registro no editor
- [[gd-001-visao-geral-arquitetura]] — visão do sistema

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
