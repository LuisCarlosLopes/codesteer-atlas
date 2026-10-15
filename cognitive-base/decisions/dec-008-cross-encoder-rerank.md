---
id: dec-008
type: adr
title: "Cross-encoder ONNX opt-in na reordenação pós-RRF"
status: approved
created: "2026-08-30"
updated: "2026-08-30"
author: "@luiscarloslopes"
links:
  - id: dec-007
    rel: extends
  - id: dec-005
    rel: related-to
tags: [busca, ranking, cross-encoder, avaliacao]
source: greenfield
migration_status: ""
meta: {}
---

# Cross-encoder ONNX opt-in na reordenação pós-RRF

## Contexto

[[dec-007-rerank-pos-rrf]] reordena o pool pós-RRF com heurística lexical (`ranking.rerank`).
É um teto bom para features cegas ao significado conjunto de query e documento. O passo
seguinte padrão em recuperação é um cross-encoder que pontua o par query×documento.

A baseline versionada (`tests/eval/baseline.json` = MRR **0.3057**) media o RRF **sem**
o rerank lexical. O `dec-007` registra **0.457** num corpus de 1482 chunks, e o roadmap
ancora o aceite de F2 em **0.605** — número de um índice contaminado por segundo FTS
(armadilha documentada no próprio `dec-007`). Nenhum desses valores é o gate desta
entrega.

## Decisão

- Recapturar `tests/eval/baseline.json` num índice `--full` limpo (1 FTS em `content`,
  1644 chunks) com os defaults de produção: rerank lexical **ligado**, braço estrutural
  **desligado**. Gate = este arquivo, nunca 0.3057 / 0.457 / 0.605.
- Cross-encoder via `fastembed.rerank.cross_encoder.TextCrossEncoder` já presente
  (zero dependência nova). Singleton lazy em `reranker.py`, espelhando `embeddings.py`.
- Opt-in por `ATLAS_RERANK_MODEL`. Ausente → `ranking.rerank` byte-a-byte. Presente →
  CE. `ATLAS_RERANK=0` desliga **toda** reordenação, inclusive a do CE.
- Falha de carga → `warnings: cross_encoder_unavailable` e fallback lexical.
- **Não promover a padrão nesta entrega**, mesmo com ganho global.

Baseline recapturada (1644 chunks, 28 queries, lexical ON, structural OFF):

| Classe | n | MRR | recall@5 |
| ------ | - | --- | -------- |
| `exact_symbol` | 7 | 0.7143 | 1.0000 |
| `partial_identifier` | 8 | 0.7750 | 1.0000 |
| `natural` | 8 | 0.0804 | 0.1250 |
| `cross_file` | 5 | 0.0333 | 0.0000 |
| **Total** | **28** | **0.4289** | **0.5714** |

2.1 (`ATLAS_RERANK_MODEL=Xenova/ms-marco-MiniLM-L-6-v2`, mesmo índice):

| Classe | MRR base | MRR 2.1 | Δ MRR | recall@5 Δ |
| ------ | -------- | ------- | ----- | ---------- |
| `natural` | 0.0804 | 0.2208 | **+0.1404** | +0.3750 |
| `exact_symbol` | 0.7143 | 0.6548 | **−0.0595** | = |
| `partial_identifier` | 0.7750 | 0.7292 | −0.0458 | −0.1250 |
| `cross_file` | 0.0333 | 0.0222 | −0.0111 | = |
| **Total** | **0.4289** | **0.4391** | +0.0102 | +0.0715 |

`query_time_ms` médio: 25.42 → **1511.4**.

**Veredicto: manter opt-in.** A classe que decidiu é `exact_symbol` (Δ −0.0595). O
roadmap e o IPD bloqueiam promoção se qualquer classe regredir; símbolo exato é metade
do golden set. Ganho em `natural` (pt-BR) não compensou as três regressões.

## Alternativas Consideradas

| Alternativa | Prós | Contras |
| ----------- | ---- | ------- |
| `sentence-transformers` CrossEncoder | API madura | Traz `torch` de volta — contradiz `dec-005` |
| `onnxruntime` cru + tokenizer | Sem lib nova | Reimplementa o que o fastembed já entrega |
| **`TextCrossEncoder` do fastembed (escolhida)** | Zero deps, mesmo cache ONNX | Modelo default treinado em inglês |
| Ligar por padrão após ganho global | Superfície mais simples | Constitution III: média global esconde regressão por classe |

## Consequências

- `CURRENT_INDEX_VERSION` permanece `2.1.0`; nenhum índice precisa ser reconstruído.
- `ranking.py` permanece o fallback medido e intocado.
- Promoção a padrão exige nova medição com ≥ baseline recapturada em **todas** as
  classes, e `query_time_ms` aceitável. O modelo é trocável por `ATLAS_RERANK_MODEL`
  sem mexer em código (candidato multilíngue se `natural` voltar a ser o limitante).

## Notas Relacionadas

- [[dec-007-rerank-pos-rrf]] — rerank lexical que este estágio estende e preserva
- [[dec-005-backend-embeddings-fastembed]] — origem do runtime ONNX reutilizado
- [[dec-009-braco-estrutural-rrf]] — o outro estágio de F2, medido no mesmo índice

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-08-30 | @luiscarloslopes | Criação com números da recaptura F2 |
