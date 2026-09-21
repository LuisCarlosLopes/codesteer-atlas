# Expansão integral — 21/09/2026

A expansão agora entrega o intervalo de linhas do símbolo no arquivo original,
validado pelo hash dos mesmos bytes. Respostas grandes usam `next_ref` vinculado
ao hash e `content_range`; o harness só considera cadeias completas desde zero.
Não há cache de sessão, alteração dos limiares Jev ou ativação de bypass.

## Método

Índice congelado com 2499 chunks; 28 consultas de ranking e 12 cenários.
Jev desligado, nenhum envio remoto. Tokens medidos pelo tokenizer embarcado,
não pelo faturamento do agente. Em cada cenário, a mesma recuperação alimentou
todos os perfis e as duas políticas de expansão; fingerprints estão no JSON.

A referência experimental reproduz a política antiga de entregar apenas o
conteúdo indexado, com corte de resultados inteiros, usando a validação atual.
Não é execução de um binário histórico. O harness expande em ordem, em lotes de
cinco, até obter evidência completa ou esgotar referências (máximo 100 chamadas).

[Dados completos](expansion_delivery_20260921.json).

## Busca mais expansões

| Política de expansão | Modo inicial | Tokens full | Tokens compact | Cenários completos | Chamadas de expansão compact |
| --- | --- | ---: | ---: | ---: | ---: |
| Conteúdo indexado (referência) | metadata | 40.798 | 37.439 | 2/12 | 20 |
| Conteúdo indexado (referência) | content | 39.920 | 36.550 | 2/12 | 7 |
| Arquivo original (novo) | metadata | 43.829 | 40.445 | 6/12 | 16 |
| Arquivo original (novo) | content | 48.978 | 45.608 | 6/12 | 7 |

Com a nova expansão, o compacto consumiu **7.72% menos** que full
no fluxo iniciado por metadados e **6.88% menos** com conteúdo.
Os totais incluem tentativas incompletas: não representam apenas tarefas resolvidas.

A correção aumentou o consumo comparado à entrega indexada incompleta, mas elevou
os cenários completos de **2 para 6**. Sete cenários receberam o código exigido;
um continua incompleto por uma relação não verificada. Os outros cinco não
recuperaram o símbolo necessário, mesmo após expandir os resultados disponíveis.
Essas lacunas pertencem à recuperação/relações e não são resolvidas pela paginação.

Nenhuma referência obsoleta e nenhum limite de chamadas atingido. As continuações
não foram necessárias nestes 12 cenários; foram verificadas em testes com conteúdo
grande, Unicode, linha única, mudanças de hash e orçamento restrito.

## Ranking nas 28 consultas

MRR e recall@5, full → compacto com seleção; resultados iguais em metadados e
conteúdo. Nenhuma regressão entre perfis sobre os mesmos candidatos.

| Classe | MRR | Recall@5 |
| --- | ---: | ---: |
| cross_file | 0.0286 → 0.0286 | 0.0000 → 0.0000 |
| exact_symbol | 0.7143 → 0.7143 | 1.0000 → 1.0000 |
| natural | 0.0875 → 0.0875 | 0.2500 → 0.2500 |
| partial_identifier | 0.7812 → 0.7812 | 1.0000 → 1.0000 |

A baseline histórica usa outro corpus. A diferença cross_file (0,0333 histórico
versus 0,0286 atual) já existia antes desta correção, conforme o relatório anterior;
a expansão não altera ranking. Não atribuímos mudanças do corpus à expansão.

## Latência, custo e Jev

- Recuperação local: p50 27.78 ms, p95 34.17 ms.
- Montagem compacta mais expansões, sem recuperação: p50 26.70 ms
  no fluxo de metadados e 7.68 ms no fluxo com conteúdo.
- Zero chamadas remotas e nenhum custo Jev incorrido nesta medição.
- Sondagem de bypass: 7/28 consultas têm símbolo exato único no pool; nenhuma
  tem pool de até três candidatos. Isso indica elegibilidade potencial, não
  equivalência de qualidade ao Jev. Bypass permanece desativado até comparação
  pareada com Jev habilitado. Os limiares de confiança continuam iguais.

## Verificação

**712 testes passaram, 1 ignorado**. Ruff 0.16.1 e mypy 2.3.0 passaram.
Seis avisos do parser nativo sendo descartado em outra thread na suíte.
Testes cobrem paginação sem lacunas, limites finais, UTF-8/CRLF, linha longa,
leitura só do símbolo, hash obsoleto após reindexação e evidência incompleta.

Para repetir a avaliação corrente:

```bash
ATLAS_RELEVANCE=0 ATLAS_SEMANTIC=0 ATLAS_OBSERVABILITY=1 \
  uv run python scripts/eval_search.py --delivery \
  --tasks tests/eval/task_scenarios.yaml --workspace . \
  --out /tmp/atlas-expansion.json
```

Reinicie a conexão MCP do Cursor para carregar o novo código e a descrição de
continuação da ferramenta. Não é preciso mudar o arquivo de configuração.
