---
id: ops-005
type: runbook
title: "Runbook — canal MCP stdio poluído por stdout"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: sys-005
    rel: triggered-by
tags: [runbook, mcp, stdio]
source: greenfield
migration_status: ""
meta: {}
---

# Runbook — canal MCP stdio poluído por stdout

## Contexto

O transporte [[meta/glossary#stdio-transport|MCP stdio]] usa stdout exclusivamente
para JSON-RPC. Logs, warnings ou prints em stdout corrompem o protocolo e quebram
a comunicação com o editor.

## Pré-requisitos

- MCP Atlas conecta mas tools falham ou retornam JSON inválido
- Cliente reporta erro de parse no canal stdio

## Diagnóstico

Sintomas:

- Servidor "conecta" mas nenhuma tool responde corretamente
- Saída misturada de log + JSON na stdout

Causa raiz conhecida: dependências (`lancedb`, `fastembed`) ou código imprimindo
em stdout durante import ou execução.

## Procedimento

### Design atual (prevenção)

`server.py` redireciona `sys.stdout` → `sys.stderr` **no import**, antes de
carregar deps pesadas. Restaura stdout real apenas em `main()` imediatamente
antes de `app.run()`.

### Ao adicionar código novo

1. **Nunca** usar `print()` ou logging para stdout no caminho do servidor MCP
2. Usar `print(..., file=sys.stderr)` ou logging configurado para stderr
3. Testar com `uv run atlas-serve` e invocar uma tool via cliente MCP

### Recuperação

1. Reiniciar o servidor MCP no editor
2. Se persistir após mudança recente: revisar imports e prints no startup path
3. Rodar `uv run pytest tests/test_server.py -v` se existir cobertura de stdio

## Rollback

Reverter commit que introduziu print/log em stdout no módulo do servidor.

## Notas Relacionadas

- [[sys-005-mcp-server]] — redirect no import
- [[spc-001-api-ferramentas-mcp]] — contrato das tools

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
