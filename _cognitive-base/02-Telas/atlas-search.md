---
tipo: tela
titulo: "atlas_search"
dominio: "[[busca]]"
rota: "MCP tool → codesteer-atlas/atlas_search"
tags: [tela, mcp, busca]
status: implementado
relacionados: ["[[storage-backend]]", "[[embedding-engine]]"]
criado: 2026-06-13
---

# atlas_search

## Propósito

Busca semântica híbrida sobre o índice local. Tool principal para agentes encontrarem implementações e conceitos.

## Fluxo de Entrada

1. Cliente MCP envia `query` + filtros opcionais
2. Servidor embeda query via [[embedding-engine]]
3. [[storage-backend]].`search_hybrid` executa vector + FTS → RRF
4. JSON com lista de [[search-result]]

## Componentes Principais

- Parâmetros: `query`, `top_k`/`limit`, `path_prefix`, `language`, `include_content`, `repo`
- Campo `references` em chunks markdown com links resolvidos ([[markdown-links]])

## Serviços Consumidos

- [[storage-backend]]
- [[embedding-engine]]

## Estados da Tela

| Estado | Comportamento |
|--------|---------------|
| Índice ausente | Erro acionável com instrução de `atlas_index` |
| Sucesso | Lista ranqueada por score RRF |
| Sem matches | Array vazio |

## Notas de Implementação

- Docstring instrui uso proativo pelo agente (`server.py`)
- `include_content=false` economiza tokens mantendo metadados

## Links

- [[MOC-Busca]]
- [[server-testes]]
