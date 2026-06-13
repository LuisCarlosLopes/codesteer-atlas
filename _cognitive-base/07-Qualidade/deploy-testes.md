---
tipo: qualidade
titulo: "deploy-testes"
arquivo: "tests/test_deploy.py"
tags: [testes, deploy]
dominio: "[[deploy]]"
criado: 2026-06-13
---

# deploy-testes

## Escopo

`tests/test_deploy.py`

## Cenários cobertos

- Config paths por OS (darwin, linux, windows)
- Local vs remote server config com index_dir
- Merge preserva servers existentes
- Backup em JSON corrompido
- run_check exit codes

## Relacionados

- [[deploy-mcp]]
- [[mcp-clients]]
