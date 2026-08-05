# Manifests de cliente MCP

Manifests prontos para registrar o CodeSteer Atlas em cada cliente MCP. Todos usam o modo remoto
(`uvx` a partir do repositório git), sem caminhos absolutos — copie o arquivo para o destino
indicado abaixo e reinicie o cliente.

| Cliente | Arquivo | Destino no seu projeto |
| --- | --- | --- |
| Cursor | [`cursor/mcp.json`](cursor/mcp.json) | `.cursor/mcp.json` |
| GitHub Copilot (VS Code) | [`vscode/mcp.json`](vscode/mcp.json) | `.vscode/mcp.json` |
| Kiro | [`kiro/settings/mcp.json`](kiro/settings/mcp.json) | `.kiro/settings/mcp.json` |
| OpenCode | [`opencode/opencode.json`](opencode/opencode.json) | `opencode.json` |

Não é necessário configurar `--index-dir` nem `ATLAS_INDEX_DIR`: o servidor descobre a pasta
`.code-index` na raiz do projeto por busca ascendente a partir do CWD e, quando o cliente suporta
a capability `roots`, também a partir do workspace informado. Consulte `atlas_status` →
`index_resolution` para ver qual origem foi usada.

Depois de registrar o servidor, indexe o workspace uma vez:

```bash
uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace .
```

Para Claude Code, Claude Desktop e Cline, veja as seções correspondentes no
[README](../../README.md#instalação) e em
[CONTRIBUTING.md](../../CONTRIBUTING.md#configuração-manual-em-outros-clientes).
