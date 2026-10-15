# Avaliação de dispensa do Jev e expansão seletiva — 2026-09-21

## Implementação e método

A opção `ATLAS_RELEVANCE_GATE=1` dispensa Jev somente para nome exato de função,
método ou classe com uma correspondência no pool recuperado. Promove o alvo e
mantém a ordem local dos demais candidatos. A unicidade não é global no repositório.
Default desligado; configuração pessoal do Cursor e limiares Jev não foram alterados.
A orientação das ferramentas recomenda começar por um ou dois símbolos e ampliar
somente quando faltar evidência; o limite da API continua sendo cinco referências.

O harness reutiliza uma recuperação e uma avaliação Jev por consulta em todas as
variantes. A dispensa é contrafactual: as chamadas da baseline foram efetivamente
pagas. Mede o texto entregue pela montagem compartilhada com o servidor, incluindo
busca e todas as expansões. Alvos de cenários determinam cobertura e parada, sem
reordenar candidatos. Isto não mede tokens de raciocínio, histórico acumulado,
faturamento ou latência total do agente.

Fontes preservadas:
- [28 consultas e 12 cenários](context_policy_20260921.json).
- [Comparação de lotes em 12 cenários](context_policy_batches_20260921.json).
- [Repetição com workspace congelado](context_policy_freshness_20260921.json).

## Dispensa de chamadas e qualidade

Nas 28 consultas, 7 eram elegíveis: 14 de 56 chamadas seriam evitadas, com
US$ 0,003150882 de custo evitável (25,5% do custo dessa rodada).
Incluindo os 12 cenários, 13 de 40 consultas eram elegíveis: 26 de 80 chamadas
(32,5%) e US$ 0,005809902 (32,9%) seriam evitados.

MRR e recall@5 foram iguais entre baseline e gate, tanto em metadados quanto
com conteúdo:

| Classe | MRR | Recall@5 |
| --- | ---: | ---: |
| Linguagem natural | 0,1292 | 0,375 |
| Símbolo exato | 1,0000 | 1,000 |
| Identificador parcial | 0,4345 | 0,750 |
| Entre arquivos | 0,0000 | 0,000 |

Preservação destas métricas não significa qualidade suficiente em todas as
classes: linguagem natural e relações entre arquivos ainda têm lacunas.
As cinco consultas naturais do teste recente do usuário não seriam dispensadas.

Na primeira rodada, o estágio Jev teve p50 de 1.264,666 ms e p95 de 1.371,144 ms.
As consultas elegíveis somaram 16.616,536 ms de estágio remoto potencialmente
evitável. Não é uma medição de latência do agente com a opção ativada.

## Expansões: busca mais conteúdo efetivamente entregue

A tabela usa a segunda rodada, substituindo apenas `debug-fts-unavailable` pela
terceira. Durante a segunda rodada houve alterações no workspace e referências
obsoletas nesse cenário; seus números originais não servem para comparar economia.
A repetição usou o mesmo índice e uma cópia congelada com todos os hashes do
manifesto conferidos. Os dados brutos permanecem intactos e o custo da repetição
entra no gasto total. A composição abaixo não contém referências obsoletas.

| Modo | Política | Lote | Tokens totais | Chamadas de expansão | Cenários completos |
| --- | --- | ---: | ---: | ---: | ---: |
| metadata | always | 5 | 42,268 | 16 | 6/12 |
| metadata | always | 2 | 39,260 | 31 | 6/12 |
| metadata | always | 1 | 40,696 | 57 | 6/12 |
| metadata | exact_gate | 5 | 42,861 | 16 | 6/12 |
| metadata | exact_gate | 2 | 39,041 | 31 | 6/12 |
| metadata | exact_gate | 1 | 40,638 | 57 | 6/12 |
| content | always | 5 | 47,084 | 9 | 6/12 |
| content | always | 2 | 45,512 | 10 | 6/12 |
| content | always | 1 | 43,656 | 11 | 6/12 |
| content | exact_gate | 5 | 49,309 | 9 | 6/12 |
| content | exact_gate | 2 | 45,590 | 10 | 6/12 |
| content | exact_gate | 1 | 44,050 | 11 | 6/12 |

Em metadados, lote dois sozinho reduz 42.268 para 39.260 tokens (7,12%).
Com gate e lote dois, são 39.041 tokens (7,63% abaixo da baseline de lote cinco).
O custo operacional é aumentar expansões de 16 para 31. Lote um não foi melhor
nesse modo: respostas adicionais também têm overhead. Com conteúdo, lote um
consumiu menos tokens que lote dois. Não há um tamanho universalmente ótimo.

Todas as variantes pareadas mantiveram 7/12 cenários com evidência completa e
6/12 completos considerando também relações. A primeira rodada havia concluído
7/12: a repetição Jev perdeu o alvo de `review-manifest-atomic` em ambas as
políticas. Isso indica variabilidade entre rodadas, não regressão causada pelo
gate na comparação pareada. Totais incluem tentativas incompletas; não representam
custo por tarefa resolvida. Mais chamadas ao agente podem aumentar a latência.

## Custo real, validação e uso

As três execuções realizaram 106 chamadas remotas ao modelo
`typesafe/jev-1.13-20260917`, por **US$ 0,023391144**, sem custos desconhecidos.
Nenhuma economia contrafactual foi descontada desse gasto.

Validação: **726 testes passaram, 1 ignorado**; Ruff 0.16.1, mypy 2.3.0 e
`git diff --check` passaram. A suíte emitiu seis avisos de destruição do parser
nativo em outra thread, sem falhas de teste.

O harness agora rejeita divergência de hashes antes de iniciar chamadas pagas.
O workspace também deve permanecer congelado durante toda a medição; a expansão
continua verificando hashes para detectar alterações posteriores.

Reinicie o MCP para carregar o código e a orientação atualizados. Para experimentar
a dispensa, configure `ATLAS_RELEVANCE_GATE=1` junto da relevância já habilitada.
A flag permanece opcional e desligada por padrão. A expansão seletiva é orientação
ao agente, não restrição automática de quais símbolos ele pode abrir.
