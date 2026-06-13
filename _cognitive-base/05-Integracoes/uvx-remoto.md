---
tipo: integracao
titulo: "uvx remoto"
tags: [integracao, uvx, instalacao]
dominio: "[[deploy]]"
criado: 2026-06-13
---

# uvx remoto

Execução sem clone via `uvx --from git+https://github.com/.../codesteer-atlas.git`.

## Uso no Atlas

- `build_remote_server_config` em [[deploy-mcp]]
- README: `uvx ... atlas-index --workspace .`

## Contrato

- Sem caminhos absolutos no manifest
- Discovery automático de `.code-index`

## Links

- [[deploy]]
- [[ADR-002-resolucao-index-dir]]
