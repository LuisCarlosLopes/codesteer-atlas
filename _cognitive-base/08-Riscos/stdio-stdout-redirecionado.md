---
tipo: risco
titulo: "stdio stdout redirecionado"
tags: [risco, mcp, stdio]
severidade: baixa
criado: 2026-06-13
---

# stdio stdout redirecionado

## Descrição

`server.py` redireciona `sys.stdout` → `stderr` na importação para proteger canal JSON-RPC stdio. Restaurado só em `main()`.

## Impacto

⚠️ Logs acidentais em stdout antes de `main()` corrompem MCP; libs barulhentas vão para stderr.

## Mitigação

- Padrão estabelecido no import do módulo
- Testes de integração MCP validam canal limpo

## Relacionados

- [[mcp-server]]
