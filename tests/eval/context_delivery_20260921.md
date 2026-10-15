# Contexto compacto — medição de 21/09/2026

Implementação e medição sobre uma cópia congelada do índice local (2.480 chunks).
28 consultas, `top_k=10`, uma recuperação por consulta compartilhada por todas
as variantes. Ranking local, Jev e camada semântica desligados. Tokenizer
embarcado; estes tokens não são faturamento do modelo do agente. Dados completos:
[context_delivery_20260921.json](context_delivery_20260921.json).

## Respostas efetivamente entregues

| Variante | Tokens metadados | Redução | Tokens conteúdo | Redução |
| --- | ---: | ---: | ---: | ---: |
| Full | 32.336 | 0,00% | 79.784 | 0,00% |
| Compacto: projeção | 21.977 | 32,04% | 69.403 | 13,01% |
| Compacto: deduplicação | 22.100 | 31,66% | 70.040 | 12,21% |
| Compacto: seleção | 22.100 | 31,66% | 70.040 | 12,21% |

Não houve cortes por orçamento nas 28 consultas. A projeção é a comparação sem
descarte de candidatos. A deduplicação acrescenta alguns tokens nesta amostra:
símbolos cobertos são declarados e vagas abertas recebem candidatos posteriores.
A seleção coincide com a deduplicação porque Jev estava desligado.

## Qualidade por classe

MRR e recall@5, full → compacto com seleção; metadados e conteúdo tiveram os
mesmos resultados.

| Classe | Consultas | MRR | Recall@5 |
| --- | ---: | ---: | ---: |
| cross_file | 5 | 0.0286 → 0.0286 | 0.0000 → 0.0000 |
| exact_symbol | 7 | 0.7143 → 0.7143 | 1.0000 → 1.0000 |
| natural | 8 | 0.0833 → 0.0833 | 0.1250 → 0.1250 |
| partial_identifier | 8 | 0.7812 → 0.7812 | 1.0000 → 1.0000 |

A baseline versionada tem MRR cross_file 0,0333; este índice tem 0,0286.
Para separar mudança de corpus de regressão, o código de recuperação anterior
aos ajustes (extraído do stage do usuário) foi executado sobre o mesmo índice
congelado: **os 28 ranks são idênticos**, incluindo essa diferença histórica.
A preservação demonstrada é relativa aos mesmos candidatos, não uma melhoria
na baixa cobertura de consultas naturais ou entre arquivos.

## Busca mais expansões

12 cenários; expansão determinística em lotes de até cinco referências, em ordem.
Full e compacto atingiram evidência completa em **2/12**, os mesmos cenários de
localização. Conteúdo não recuperado integralmente, incluindo conteúdo truncado,
não foi contado como evidência completa. Relações não verificadas permanecem
pendentes. Portanto, os totais seguintes incluem tentativas incompletas e não
comprovam economia para tarefas resolvidas em geral.

| Modo inicial | Full: tokens acumulados | Compacto com seleção | Expansões por variante |
| --- | ---: | ---: | ---: |
| Metadados | 40.561 | 37.378 | 20 |
| Conteúdo | 39.869 | 36.490 | 7 |

Nenhuma referência obsoleta nesta rodada. A expansão lê o chunk indexado e não
recupera automaticamente o restante de um chunk truncado; esta limitação já
existente foi contabilizada como falta de evidência.

## Latência, custo e validação

- Recuperação compartilhada: p50 **28,42 ms**, p95 **31,80 ms**.
- Montagem full: p50 **1,87 ms** em metadados / **4,27 ms** com conteúdo.
- Montagem compacta com seleção: p50 **1,32 ms** / **3,40 ms**;
  p95 **1,56 ms** / **4,32 ms**. Medidas locais de uma rodada, não SLA.
- **Zero chamadas remotas**; nenhum custo Jev incorrido. O campo de custo
  conhecido continua `null` quando não há cobrança reportada. Esta rodada não
  mede latência, qualidade ou economia incremental de Jev habilitado.
- **701 testes passaram, 1 ignorado**; lint Ruff 0.16.1 e mypy 2.3.0 passaram.
  A suíte emitiu sete avisos do parser nativo sendo descartado em outra thread.
- Defaults continuam desligados, perfil full e limiares Jev preservados.

## Reprodução

```bash
ATLAS_RELEVANCE=0 ATLAS_SEMANTIC=0 ATLAS_OBSERVABILITY=1 \
  uv run python scripts/eval_search.py --index-dir <copia-do-indice> \
  --workspace <repositorio> --delivery --tasks tests/eval/task_scenarios.yaml \
  --baseline tests/eval/baseline.json --out /tmp/atlas-delivery.json
```

Mantenha índice e arquivos estáveis durante a medição. Resultados mudam com o
corpus; compare sempre as variantes sobre uma única recuperação.
