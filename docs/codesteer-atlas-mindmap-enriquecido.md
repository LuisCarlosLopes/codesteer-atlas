# CodeSteer Atlas — Mindmap Enriquecido (Zettelkasten)

> Classificação semântica · máx. 4 níveis · fonte: MCP Atlas + código real
> Data: 2026-06-13

```mermaid
mindmap
  root((CodeSteer Atlas))
    🧩 Indexacao
      ⚙️ index_workspace
      ⚙️ ASTChunker
      ⚙️ should_ignore
      🗄️ CodeChunk
      📐 ADR-005 incremental sha256
      ⚠️ lock concorrente
    🧩 Busca
      📡 atlas_search
      ⚙️ search_hybrid
      📐 ADR-002 RRF
      📐 DECISAO-003 prefilter FTS
      🗄️ SearchResult
    🧩 Armazenamento
      ⚙️ StorageBackend
      🗄️ IndexManifest
      🗄️ LanceDB chunks
      📐 ADR-007 indice local
      ⚠️ MIN_INDEX_VERSION
    🧩 MCP Server
      📡 atlas_map
      📡 atlas_index
      📡 atlas_status
      ⚙️ resolve_index_dir
      📐 DECISAO-002 resolucao dir
      📐 DECISAO-004 async reindex
      🔒 stdio sem vazamento stdout
    🧩 Deploy
      ⚙️ deploy_mcp
      🔗 MCP Clients
      🔗 uvx remoto
      🔗 Claude Code plugin
    🧩 Embeddings
      ⚙️ EmbeddingEngine
      🔗 fastembed ONNX
      📐 ADR-008 all-MiniLM-L6-v2
      ⚠️ lazy load cold start
    🧪 Qualidade
      test_chunker
      test_storage
      test_server
      test_indexer
    ⚠️ Riscos
      indice stale git HEAD
      manifest legado torch
      reindex em progresso
```

## Legenda de prefixos

| Prefixo | Uso no Atlas |
|---------|----------------|
| 🧩 | Domínio funcional (indexação, busca, MCP…) |
| 📡 | Tool MCP / interface de rede |
| ⚙️ | Serviço / módulo Python |
| 🗄️ | Entidade Pydantic ou tabela LanceDB |
| 🔗 | Integração externa (fastembed, LanceDB, clientes) |
| 🔒 | Segurança / isolamento local |
| 🧪 | Suíte de testes |
| ⚠️ | Risco ou limitação conhecida |
| 📐 | ADR / decisão arquitetural |
| 💡 | Roadmap — *nenhum item explícito no input atual* |

## Vault Obsidian

Estrutura completa em [[docs/vault/00-MOC/MOC-Home|vault/00-MOC/MOC-Home.md]].
