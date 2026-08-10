# Manifests de cliente MCP

Manifests prontos para registrar o CodeSteer Atlas **na raiz do seu projeto**.
Todos usam modo remoto (`uvx` a partir do GitHub), sem caminhos absolutos.

> **Não copie para a configuração global do editor.** Plugins/MCP globais
> costumam iniciar o servidor fora da raiz do projeto, e o Atlas não consegue
> inferir de forma confiável a pasta `.code-index`. Prefira sempre o destino
> por projeto na tabela abaixo (ou plugin com escopo *project*/*local*).

| Cliente | Arquivo | Destino no seu projeto |
| --- | --- | --- |
| Cursor | [`cursor/mcp.json`](cursor/mcp.json) | `.cursor/mcp.json` |
| GitHub Copilot (VS Code) | [`vscode/mcp.json`](vscode/mcp.json) | `.vscode/mcp.json` |
| Kiro | [`kiro/settings/mcp.json`](kiro/settings/mcp.json) | `.kiro/settings/mcp.json` |
| OpenCode | [`opencode/opencode.json`](opencode/opencode.json) | `opencode.json` |

Depois de registrar, indexe o workspace uma vez na raiz do projeto:

```bash
uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace .
```

Se o índice não for encontrado (ex.: Cursor com CWD = `$HOME`), defina
`ATLAS_INDEX_DIR` no manifest do projeto — o exemplo do Cursor já inclui
`${workspaceFolder}/.code-index`. Consulte `atlas_status` → `index_resolution`.

Para Claude Code (plugin com `--scope project|local` ou `.mcp.json` na raiz),
veja o [README](../../README.md#1-conectar-o-mcp-no-seu-projeto). Detalhes
avançados: [CONTRIBUTING.md](../../CONTRIBUTING.md#configuração-manual-em-outros-clientes).
