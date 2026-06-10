Veja [CLAUDE.md](CLAUDE.md) para detalhes de arquitetura.


## Codebase search (MCP)

**Always use the `codesteer-atlas` MCP server** when you need to search or explore this codebase — do not rely on broad file reads, `grep`, or built-in semantic search as the primary discovery path.

| Goal | Tool |
|------|------|
| Find where a function, class, method, or concept is implemented | `atlas_search` |
| Understand project structure without reading full files | `atlas_map` |
| Check whether the index exists and is up to date | `atlas_status` |
| Reindex after large changes or when status reports stale | `atlas_index` |

Use `path_prefix`, `language`, and `include_content=false` on `atlas_search` to narrow scope and save tokens. Reserve exact literal matches (symbol names, error strings) for `grep`/`Read` only after Atlas has pointed you to the right files.
