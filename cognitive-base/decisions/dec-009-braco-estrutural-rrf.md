---
id: dec-009
type: adr
title: "Braço estrutural opt-in na fusão RRF"
status: approved
created: "2026-08-30"
updated: "2026-08-30"
author: "@luiscarloslopes"
links:
  - id: dec-001
    rel: extends
  - id: dec-007
    rel: related-to
  - id: dec-008
    rel: related-to
tags: [busca, rrf, grafo, ranking, avaliacao]
source: greenfield
migration_status: ""
meta: {}
---

# Braço estrutural opt-in na fusão RRF

## Contexto

A busca híbrida de [[dec-001-busca-hibrida-rrf]] é chunk-level e sem memória de
estrutura. Se três dos dez primeiros resultados estão na mesma vizinhança do grafo,
um quarto chunk dessa vizinhança em 30º provavelmente também é resposta — e hoje
não sobe.

O roadmap condicionava este braço a §1.3 (denylist de ruído nos rankings de hub da
F1). Executar §1.3 inteiro dentro de F2 mudaria `atlas_brief` / `atlas_graph hubs`.
Ignorar o predicado entregaria explosão combinatória por hub.

## Decisão

- Terceiro `_fuse(..., "graph")` alimentado por spreading activation sobre
  `graph.json`. Só re-ranqueia chunks já recuperados pelos braços vetorial/FTS
  (não amplia recall; não consulta o LanceDB de novo).
- Opt-in **por chamada**: `atlas_search(..., structural=True)`, default `False`.
  Sem flag de ambiente enquanto o default for desligado.
- Junção chunk↔nó em memória: `sym:{file_path}#{scope_name}` (código) e
  `sec:{file_path}#{scope_name}` (markdown). Sem coluna e sem segundo índice FTS.
- `graph.json` ausente → `warnings: structural_arm_unavailable` e no-op.
- **`structural.is_noise_hub` é o ponto único do predicado de ruído.** F2 o aplica
  só na expansão do braço (nó de ruído não é expandido, mas continua elegível).
  F1 §1.3 deve **reutilizar esta função** nos dois rankings de hub
  (`graph._finalize_graph` e `brief._compute_hubs`) em vez de duplicar a regra.
  O predicado não vive em `graph.py` para `storage` não puxar `viewer` no import.

Baseline recapturada (mesmo índice de [[dec-008-cross-encoder-rerank]], 1644 chunks,
lexical ON, structural OFF): MRR total **0.4289**. A baseline antiga 0.3057 media
RRF puro, sem rerank lexical, noutro corpus.

2.2 (`structural=True`, lexical ON, sem CE, mesmo índice):

| Classe | MRR base | MRR 2.2 | Δ MRR | recall@5 Δ |
| ------ | -------- | ------- | ----- | ---------- |
| `exact_symbol` | 0.7143 | 0.8214 | **+0.1071** | = |
| `cross_file` | 0.0333 | 0.0500 | +0.0167 | +0.2000 |
| `natural` | 0.0804 | 0.0781 | −0.0023 | = |
| `partial_identifier` | 0.7750 | 0.7542 | **−0.0208** | = |
| **Total** | **0.4289** | **0.4521** | +0.0232 | +0.0357 |

`query_time_ms` médio: 25.42 → 25.52 (indistinguível).

**Veredicto: manter opt-in.** A classe que decidiu é `partial_identifier` (Δ −0.0208).
O modo de falha temido (rebaixar `exact_symbol`) **não ocorreu** — símbolo exato
subiu. Ainda assim o gate exige ≥ baseline em todas as classes; `natural` também
regrediu levemente (−0.0023). Promoção a padrão fica fora desta entrega.

## Alternativas Consideradas

| Alternativa | Prós | Contras |
| ----------- | ---- | ------- |
| Bloquear 2.2 até F1 §1.3 | Fronteira de fases literal | Trava um predicado de ~10 linhas |
| Implementar §1.3 inteiro em F2 | Predicado nos rankings de hub | Muda `atlas_brief`/`atlas_graph`; invade F1 |
| **Predicado só na expansão (escolhida)** | Sem divergência entre tools; F1 reutiliza | Rankings de hub continuam sem denylist até F1 |
| Materializar nós fora do pool | Recall potencial | Segunda consulta LanceDB por busca |
| Default-on | — | Proibido: rebaixaria acerto exato no caso geral |

## Consequências

- `SearchResult.match_arms` admite `"graph"`.
- `CURRENT_INDEX_VERSION` permanece `2.1.0`.
- F1, ao implementar §1.3, importa `structural.is_noise_hub` nos dois lados — não
  reescreve o predicado em `graph.py`/`brief.py`.
- Se uma promoção futura ligar o braço por padrão, aí entra `ATLAS_STRUCTURAL_ARM`,
  fora do escopo desta entrega.

## Notas Relacionadas

- [[dec-001-busca-hibrida-rrf]] — a fusão de braços que este estágio estende
- [[dec-007-rerank-pos-rrf]] — rerank lexical que permanece o default
- [[dec-008-cross-encoder-rerank]] — o outro estágio de F2, medido no mesmo índice

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-08-30 | @luiscarloslopes | Criação com números da recaptura F2 |
