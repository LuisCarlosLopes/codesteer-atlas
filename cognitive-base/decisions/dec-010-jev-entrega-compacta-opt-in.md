---
id: dec-010
type: adr
title: "Jev e entrega compacta opt-in, com o ranking local de v3.0 como default"
status: draft
created: "2026-09-21"
updated: "2026-09-24"
author: "@luiscarloslopes"
links:
  - id: dec-004
    rel: depends-on
  - id: dec-007
    rel: related-to
  - id: dec-008
    rel: related-to
  - id: sys-005
    rel: related-to
tags: [busca, ranking, jev, contexto, avaliacao]
source: greenfield
migration_status: ""
meta: {}
---

# Jev e entrega compacta opt-in, com o ranking local de v3.0 como default

## Contexto

`feat/v3.0-jev` está cinco commits à frente de `v3.0` (`2257be24`). A
[[meta/glossary#busca-hibrida|busca híbrida]] e a reordenação local de
[[dec-007-rerank-pos-rrf]] e [[dec-008-cross-encoder-rerank]] já existiam em
`v3.0`. O diff acrescenta um avaliador remoto de relevância e um caminho de
entrega mais curto para o agente.

[[dec-004-indice-100-local]] fixa a pipeline padrão como local e offline.
Qualquer envio de consulta e de [[meta/glossary#chunk|chunks]] para fora do
host precisa continuar explícito, desligado e visível em `atlas_status`.

## Decisão

O default de `v3.0` permanece. Três flags nascem desligadas e não se promovem
nesta entrega:

- **`ATLAS_RELEVANCE=1`** faz o modelo Jev (System One da TypeSafe, chamado
  pela API System One do OpenRouter) refinar a reordenação lexical, no lugar do
  cross-encoder, no pool pós-[[meta/glossary#rrf|RRF]], antes do merge tipado e
  do corte `top_k`. O pool chega ao Jev na ordem lexical. Uma chamada leva o
  pool inteiro (lote ≤ 192 KB; duas chamadas só em overflow), timeout
  compartilhado de 3 s. A rubrica default é a v2 (4 níveis concretos com
  exemplos, `kind`/`language` no estado); `ATLAS_RELEVANCE_RUBRIC=v1` volta à
  anterior, e os limiares valem como fração da escala. Confiança abaixo de
  0,70 isola o candidato, que fica na ordem lexical.
  Falha de contrato ou timeout devolve o lote ao reranker local.
  `ATLAS_RERANK=0` desliga Jev e o rerank local. O score do resultado continua
  o do RRF; `match_arms` não ganha braço `jev`. Modelo default
  `~typesafe/jev-latest`. Custo sai em uma linha JSON em stderr; o bloco
  `atlas_status.relevance` declara egresso sem rede.

- **`ATLAS_RELEVANCE_GATE=1`** só dispensa o Jev quando a consulta é um nome
  exato único de função, método ou classe dentro do pool recuperado. O alvo
  sobe e o restante conserva a ordem local. A unicidade vale no pool, não no
  repositório. `ATLAS_RELEVANCE_GATE=identifier` dispensa para toda
  consulta-identificador; fica fora do default porque essas consultas perdem o
  corte e a reordenação da v2.

- **`ATLAS_CONTEXT_OPTIMIZATION=1`** é independente do Jev. `response_profile=default`
  passa a `compact`: projeção enxuta, deduplicação por cobertura verificável,
  orçamento a 70% do teto e `atlas_expand` por id. Com Jev ligado, a entrega
  compacta faz do `top_k` um teto: entre os `top_k` primeiros sai, sem repor,
  quem tem score < 1,0. É o default (`ATLAS_RELEVANCE_CUT`); com
  `ATLAS_RELEVANCE_CUT=0`, a seleção só remove candidato com score ≤ 0,25 e
  confiança ≥ 0,90.
  `response_profile=full` mantém o formato de `v3.0`. `atlas_expand` lê o
  intervalo original do [[meta/glossary#simbolo|símbolo]], valida o hash e
  pagina com `next_ref` sem cache de sessão. A orientação ao agente é começar
  por uma ou duas refs. As respostas compactas só trazem o bloco `budget`
  quando algo foi cortado, e classe acima de ~1.500 tokens abre como cabeçalho
  + `outline` dos métodos (`ATLAS_EXPAND_OUTLINE=0` desliga).

## Alternativas Consideradas

| Alternativa | Prós | Contras |
| ----------- | ---- | ------- |
| Promover o Jev a padrão | Reordena por evidência do par consulta×candidato | Egresso de código; estágio p50 ~1,3 s; sem gate de MRR por classe contra o rerank lexical na baseline versionada |
| Promover o perfil compacto a padrão | Menos tokens na resposta entregue, com MRR/recall iguais sobre os mesmos candidatos | Muda o contrato que o agente já consome; a flag deixa o operador escolher |
| Uma única chamada Jev, sem gate | Superfície menor na primeira entrega | Pool que estoura 24 KB ficava sem avaliação; nomes exatos únicos pagavam a chamada à toa |
| **Três flags desligadas (escolhida)** | Default local de [[dec-004-indice-100-local]] intacto; cada estágio é reversível por ambiente | Quem não liga a flag não vê o ganho medido de tokens nem a dispensa de chamadas |

## Consequências

- Módulos novos: `relevance.py`, `context_optimization.py`, `http_transport.py`.
  A tool nova é `atlas_expand`, descrita em [[spc-001-api-ferramentas-mcp]]
  apenas até o contrato anterior a este diff — o comportamento vigente está
  em [[sys-005-mcp-server]].
- `CURRENT_INDEX_VERSION` não muda. Índice de `v3.0` continua buscável.
- `AGENTS.md` passa a ser a fonte única das instruções de agente; `CLAUDE.md`
  só importa esse arquivo.
- Custo remoto e latência do Jev não entram no MRR histórico de
  `tests/eval/baseline.json`. Comparar classes entre corpus diferente, ou entre
  rodada com Jev e rodada sem Jev, mistura mudança de índice com mudança de
  ranking.

Medição do compacto com Jev desligado (cópia congelada, 2.480 chunks, 28
consultas, mesmos candidatos): metadados 32.336 → 22.100 tokens (−31,66% após
deduplicação); conteúdo 79.784 → 70.040 (−12,21%). MRR e recall@5 iguais nas
quatro classes. Nenhum corte de orçamento nessa amostra. A expansão do arquivo
original elevou cenários com evidência completa de 2/12 para 6/12; no fluxo
novo, o compacto ficou 7,72% abaixo do full em metadados e 6,88% com conteúdo.
Esses totais incluem tentativas incompletas.

Medição pareada do gate com Jev ligado (modelo `typesafe/jev-1.13-20260917`):
nas 28 consultas, 7 eram elegíveis e 14 de 56 chamadas seriam evitadas
(US$ 0,003150882, 25,5% daquela rodada). MRR e recall@5 ficaram iguais entre
sempre avaliar e dispensar. Linguagem natural (MRR 0,1292) e entre arquivos
(MRR 0,0000) continuam lacunas. Em metadados, gate mais lote de duas refs
chegou a 39.041 tokens, 7,63% abaixo do lote de cinco sem gate, com o dobro
de chamadas de expansão (16 → 31). Três execuções pagas somaram 106 chamadas
e US$ 0,023391144. O p50 do estágio remoto na primeira rodada foi 1.264,666 ms.

Revisão de 24/09. A captura paga de 34 consultas gravou as notas por
candidato, que depois foram reaplicadas sem rede no caminho de produção (28
consultas, `top_k=10`, compacto). A queda de identificadores parciais vinha da
ordem de base: o pool chegava ao Jev em ordem RRF, o rerank lexical não rodava
e ~70% dos candidatos, incertos, ficavam na posição RRF. Nessa configuração, o
MRR ficava em 0,375, contra 0,431 sem Jev. Com o Jev refinando a ordem lexical
e o corte do `top_k`, o MRR vai a 0,500 e os metadados ficam 32% abaixo do
compacto sem Jev (22.096 → 15.012 tokens), sem perder alvo. Com a rubrica v2
no lugar da v1, o MRR vai a 0,613 (linguagem natural 0,073 → 0,312) com 14.163
tokens, e os cenários completos passam de 7 para 8. O gate `identifier` cortaria
as chamadas de 28 para 13, mas levaria o MRR a 0,569 e os tokens a 18.737. Fora
da amostra (38 consultas novas, Jev chamado de verdade), a v2 com o corte teve MRR
0,697 contra 0,507 do local e 0,558 da v1 (`top_k=10`), com 39% menos tokens. Dados em
[`tests/eval/jev_curation_study_20260924.md`](../../tests/eval/jev_curation_study_20260924.md).

## Notas Relacionadas

- [[dec-004-indice-100-local]] — o default continua local; o Jev só envia
  consulta e candidatos quando `ATLAS_RELEVANCE` está ligado
- [[dec-007-rerank-pos-rrf]] — reordenação lexical que o Jev refina com a flag
  ligada, e que recebe o lote de volta em falha
- [[dec-008-cross-encoder-rerank]] — o cross-encoder, que o Jev substitui
  quando ligado; os dois permanecem fora do default
- [[sys-005-mcp-server]] — `atlas_search`, `atlas_context` e `atlas_expand`
  aplicam o perfil e a expansão

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-09-21 | @luiscarloslopes | Criação a partir do diff `v3.0...feat/v3.0-jev` |
| 1.1.0  | 2026-09-24 | @luiscarloslopes | Jev passa a refinar a ordem lexical em vez de substituí-la; corte do `top_k` vira default no compacto com Jev (`ATLAS_RELEVANCE_CUT=0` desliga) |
| 1.2.0  | 2026-09-24 | @luiscarloslopes | Rubrica v2 default (`ATLAS_RELEVANCE_RUBRIC=v1` volta); uma chamada por busca; gate `identifier` opcional |
| 1.3.0  | 2026-09-24 | @luiscarloslopes | Bloco `budget` só em corte nas respostas compactas; resumo de classe grande em `atlas_expand` |
