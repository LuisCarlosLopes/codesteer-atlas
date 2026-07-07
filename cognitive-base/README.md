# Base Cognitiva — CodeSteer Atlas

> 19 notas · última atualização: 2026-06-17

Base de conhecimento do projeto em Markdown com wikilinks, organizada em seis
quadrantes cognitivos. Navegação primária no **Obsidian** (Graph View + wikilinks).

## Encontre o que você precisa

| Pergunta | Quadrante | Notas | Comece por |
| -------- | --------- | ----- | ---------- |
| Por que algo é assim? | [[decisions/README]] | 6 | ADRs, regras de negócio |
| O que o sistema faz? | [[specs/README]] | 1 | Features, APIs, casos de uso |
| Como o sistema existe hoje? | [[system/README]] | 5 | Serviços, tabelas, infraestrutura |
| Como trabalhar dentro da arquitetura? | [[guides/README]] | 2 | How-tos, fluxos, componentes |
| Como operar em produção? | [[ops/README]] | 5 | Runbooks, incidentes, lições |
| O que significa este termo? | [[meta/glossary]] | — | Termos com âncoras |


## Novo no projeto? Comece aqui

1. [[gd-001-visao-geral-arquitetura|Visão geral da arquitetura]]
2. [[gd-030-primeiros-passos|Primeiros passos]]
3. [[meta/glossary|Glossário]] — termos do domínio (MCP, chunks, RRF, etc.)

## Notas mais conectadas

| ID | Título | Conexões |
| -- | ------ | -------- |
| [[dec-005-backend-embeddings-fastembed]] | Backend de embeddings fastembed (ONNX) em vez de PyTorch | 6 |
| [[gd-001-visao-geral-arquitetura]] | Visão geral da arquitetura do CodeSteer Atlas | 6 |
| [[gd-030-primeiros-passos]] | Primeiros passos — setup, indexação e uso do MCP | 6 |
| [[sys-005-mcp-server]] | Servidor MCP FastMCP — ferramentas atlas_* | 5 |
| [[dec-003-indexacao-incremental]] | Indexação incremental por hash sha256 de arquivos | 5 |


## Padrões mais violados pela IA

| Padrão | Por que importa | Onde documentar |
| ------ | --------------- | --------------- |
| Buscar código via grep antes do Atlas | Perde contexto semântico e símbolos AST | [[decisions/ai-corrections/README]] |
| Enviar código para APIs externas | Viola princípio 100% local | [[decisions/README]] |
| Ignorar indexação incremental | Reindexa desnecessariamente | [[guides/README]] |
| Poluir stdout no servidor MCP | Quebra o canal JSON-RPC stdio | [[ops-005-runbook-stdio-stdout]] |
| Chunkar arquivo inteiro ignorando AST | Degrada qualidade da busca | [[system/README]] |

## Como usar no Obsidian

1. Abra o Obsidian
2. **Open folder as vault**
3. Selecione esta pasta (`cognitive-base/`)
4. Use o **Graph View** — cores por quadrante já configuradas em `.obsidian/graph.json`
5. Links são sempre wikilinks no formato colchetes duplos com alias (ver [[CONTRIBUTING]])

## Manutenção

- `kb-note` — criar ou atualizar notas
- `kb-index` — regenerar [[index]] (grafo de links e estatísticas)
- `kb-audit` — auditar saúde da base (front matter, links, órfãs)

## Estrutura

```
cognitive-base/
├── decisions/          ← Por que é assim
├── specs/              ← O que o sistema faz
├── system/             ← Como existe hoje
├── guides/             ← Como trabalhar dentro
├── ops/                ← Como operar
└── meta/               ← Glossário, templates
```

Ver [[CONTRIBUTING]] para regras de contribuição e promoção de notas.
