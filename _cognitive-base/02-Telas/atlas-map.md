---
tipo: tela
titulo: "atlas_map"
dominio: "[[busca]]"
rota: "MCP tool → codesteer-atlas/atlas_map"
tags: [tela, mcp, mapa]
status: implementado
relacionados: ["[[storage-backend]]"]
criado: 2026-06-13
---

# atlas_map

## Propósito

Visão hierárquica compacta de classes, métodos e funções — sem carregar arquivos inteiros.

## Fluxo de Entrada

1. Cliente envia `path_prefix`, `max_depth`, `repo` opcionais
2. `StorageBackend.get_symbols` consulta manifest/LanceDB
3. Árvore textual retornada

## Componentes Principais

- `max_depth` default 3
- Seções markdown indexadas como `section {título}`

## Serviços Consumidos

- [[storage-backend]]

## Estados da Tela

| Estado | Comportamento |
|--------|---------------|
| Índice ausente | Erro acionável |
| Filtro vazio | Mapa do workspace inteiro |

## Notas de Implementação

- Complementa [[atlas-search]]: map para estrutura, search para conteúdo

## Links

- [[MOC-Busca]]
