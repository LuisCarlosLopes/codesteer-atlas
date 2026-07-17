Leia [CLAUDE.md](CLAUDE.md) para detalhes de arquitetura.

## Busca de código (MCP codesteer-atlas)

Use as tools do MCP **antes** de `grep`/`rg`/`find`/glob ou leitura em massa.

| Objetivo | Tool |
| --- | --- |
| Onde algo está implementado | `atlas_search` |
| Estrutura do projeto | `atlas_map` |
| Conectividade, hubs e rationale | `atlas_graph` |
| Diagnóstico do índice | `atlas_status` |
| (Re)indexar | `atlas_index` |

**`atlas_search` (2 passos):** retorna só metadados por padrão — localize com
`path_prefix`/`language`/`top_k` baixo; depois `Read` nas linhas ou
`include_content=true` nos poucos hits relevantes. Não chame `atlas_status` antes.

<!-- codesteer:constitution-precedence -->
## Precedência de governança (CodeSteer)

`.memory-bank/operational-memory.md` é **memória operacional** do repositório: problemas locais, gotchas e **como mitigar** — não substitui a camada normativa da Constitution.

Em **todas** as tarefas, as regras em `.memory-bank/constitution.md` prevalecem.
<!-- /codesteer:constitution-precedence -->
