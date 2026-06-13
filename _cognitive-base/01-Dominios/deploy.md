---
tipo: dominio
titulo: "Deploy"
aliases: [deploy, instalacao]
tags: [dominio, deploy, mcp-clients]
status: ativo
relacionados: ["[[mcp-server]]"]
criado: 2026-06-13
atualizado: 2026-06-13
---

# Deploy

## Visão Geral

Registro do servidor MCP em editores e CLIs de agentes. Suporta modo **local** (clone + `uv run`) e **remoto** (`uvx` do GitHub).

## Responsabilidades

- Gerar configs MCP para Cursor, Claude Desktop, Cline, Claude Code CLI
- Backup de JSON corrompido antes de merge
- `deploy_mcp.py --check` valida imports críticos no setup

## Telas

- N/A — domínio operacional, sem UI

## Serviços

- [[deploy-mcp]] — `deploy_mcp.py`

## Entidades

- N/A

## Riscos e Limitações

- Caminhos absolutos em modo local; modo remoto usa discovery de índice
- ⚠️ Clientes sem suporte a mindmap Mermaid não afetam o servidor

## Links Relacionados

- [[MOC-Deploy]]
- [[mcp-clients]]
- [[deploy-testes]]
