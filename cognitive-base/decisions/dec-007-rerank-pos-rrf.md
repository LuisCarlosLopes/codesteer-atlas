---
id: dec-007
type: adr
title: "Ranking de busca: reordenação pós-RRF do pool de candidatos"
status: approved
created: "2026-08-19"
updated: "2026-08-19"
author: "@luiscarloslopes"
links:
  - id: dec-001
    rel: extends
  - id: sys-002
    rel: related-to
tags: [busca, rrf, ranking, avaliacao]
source: greenfield
migration_status: ""
meta: {}
---

# Ranking de busca: reordenação pós-RRF do pool de candidatos

## Contexto

A busca híbrida de [[dec-001-busca-hibrida-rrf]] funde dois braços e entrega o resultado
do RRF direto, sem reordenação. Um golden set de 28 queries sobre o próprio repositório,
separado em quatro classes, expôs três lacunas. Os números abaixo são do corpus de 1438
chunks; ver em Consequências por que eles não se sustentam em todo estado de corpus:

| Classe | MRR antes | recall@5 antes |
| ------ | --------- | -------------- |
| `exact_symbol` | 0.576 | 1.000 |
| `partial_identifier` | 0.479 | 0.750 |
| `natural` | 0.079 | 0.250 |
| `cross_file` | 0.040 | 0.200 |

1. **Identificador parcial não casa.** Um prefixo como `update_manifest_after_incr` não
   encontra `update_manifest_after_incremental`: o BM25
   tokenizado por palavra não faz substring e o MiniLM é fraco em identificador.
2. **Sem reordenação pós-fusão.** O RRF ignora se os termos aparecem no nome do símbolo e
   o quão próximos estão no corpo do chunk.
3. **Query crua no BM25.** Termos genéricos (`como`, `função`, `update`) diluem o score de
   todos os candidatos.

## Decisão

Uma mudança, validada por medição antes de ser mantida:

- **Reordenação pós-RRF** — `ranking.rerank` reordena um pool de
   `min(top_k * RERANK_POOL_MULTIPLIER, CANDIDATES_LIMIT)` por boost de título, proximidade
   e frase, com o score RRF como desempate, e só então corta em `top_k`.

- **A poda de stopwords nunca toca a query do embedding.** O braço semântico precisa da frase
  completa para capturar intenção.

## Alternativas Consideradas

| Alternativa | Prós | Contras |
| ----------- | ---- | ------- |
| Ngram sobre `content` | Recall máximo em substring | Índice explode; ganho real está no nome do símbolo |
| Ponderar RRF pelo boost (`rrf * (1 + boost)`) | Preserva o consenso entre braços | **Medido pior**: MRR total 0.565 contra 0.605 |
| Desligar rerank em prosa | Protegeria linguagem natural | **Medido pior ainda**: 0.550, e derruba `cross_file` a zero |
| **Boost dominante, RRF como desempate (escolhida)** | Melhor nas quatro classes | Menos interpretável que score único |
| Forçar reindexação (subir `MIN_INDEX_VERSION`) | Schema uniforme | Quebra índice existente sem necessidade |

## Consequências

- `SearchResult` ganha `match_arms`, exposto por `atlas_search`: diz se o acerto veio dos
  dois braços (consenso) ou de um só.
- `ATLAS_RERANK=0` desliga a reordenação para A/B e rollback sem redeploy.
- Sem mudança de schema: `CURRENT_INDEX_VERSION` segue em `2.1.0` e nenhum índice
  existente precisa ser reconstruído.

Resultado medido no mesmo índice (1482 chunks, 28 queries):

| Classe | MRR antes | MRR depois | Δ | recall@5 Δ |
| ------ | --------- | ---------- | - | ---------- |
| `partial_identifier` | 0.479 | 0.754 | **+0.275** | +0.250 |
| `exact_symbol` | 0.562 | 0.714 | +0.152 | +0.143 |
| `natural` | 0.078 | 0.156 | +0.078 | = |
| `cross_file` | 0.033 | 0.100 | +0.067 | +0.200 |
| **Total** | **0.306** | **0.457** | **+0.151** | +0.143 |

Nenhuma classe regride. O ganho é **todo do rerank**: podar stopwords do texto enviado ao
BM25 foi tentado e descartado — o efeito é de ±0.002, indistinguível de zero contra um IC de
bootstrap de ±0.07. A constante `QUERY_STOPWORDS` permanece porque `ranking.query_terms` precisa
dela para não dar boost de título a termo genérico.

## Armadilha de medição (custou duas conclusões erradas)

`table.search(q, query_type="fts")` **sem `fts_columns` busca em todas as colunas com índice
FTS**, não só em `content`. Um protótipo de terceiro braço criou um índice ngram sobre uma
coluna `symbol_text`; a partir daí o braço BM25 passou a consultá-la implicitamente, e toda
medição de "código antigo" feita contra aquele índice ficou inflada (baseline oscilando entre
0.306 e 0.547 sem explicação aparente).

Consequências práticas para qualquer trabalho futuro de ranking:

- Recapturar `baseline.json` **num índice sem os artefatos do experimento**, não apenas com o
  código revertido.
- Se um segundo índice FTS voltar a existir, o braço BM25 precisa de `fts_columns="content"`
  explícito, senão os dois braços se sobrepõem e o RRF conta o mesmo sinal duas vezes.

## Notas Relacionadas

- [[dec-001-busca-hibrida-rrf]] — a fusão de dois braços que esta decisão estende
- [[sys-002-storage-backend]] — implementação em `search_hybrid`
- [[dec-005-backend-embeddings-fastembed]] — origem da limitação de idioma do braço vetorial

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-08-19 | @luiscarloslopes | Criação   |
