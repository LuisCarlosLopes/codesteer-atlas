---
tipo: servico
titulo: "deploy_mcp"
arquivo: "deploy_mcp.py"
dominio: "[[deploy]]"
tags: [servico, deploy]
contrato: "int (exit code)"
criado: 2026-06-13
---

# deploy_mcp

## Propósito

Registra servidor `codesteer-atlas` nos configs MCP de múltiplos clientes.

## Funções Públicas

- `main()` — deploy interativo
- `run_check()` — valida `CRITICAL_MODULES`
- `build_local_server_config` / `build_remote_server_config`
- `save_mcp_config` — merge com backup
- `get_mcp_config_paths()` — paths por OS

## Contrato de Retorno

Exit 0/1 em `--check`.

## Dependências

- [[mcp-clients]]
- [[uvx-remoto]] (modo remoto)

## Comportamento Offline

Modo local não requer rede; remoto usa `uvx` + git.

## Testes

- [[deploy-testes]]
