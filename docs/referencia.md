# Referência do operador

Contrato das opções, medições e variáveis de ambiente. Para instalar o Atlas no editor e fazer a primeira busca, use o [README](../README.md).

Os comandos de avaliação abaixo rodam na raiz deste repositório, com o índice e o workspace congelados.

## Relevância com o modelo Jev

O [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) é o modelo System One da TypeSafe: devolve, por candidato, uma decisão tipada com score e confiança. No Atlas, `ATLAS_RELEVANCE` usa essa decisão para reordenar o pool já recuperado. A chamada vai à API System One do OpenRouter.

`ATLAS_RELEVANCE` é **False** por padrão (`ausente`/`0`/`false`). Não liga por
presença de chave, modelo ou `ATLAS_SEMANTIC`. Aceite `1`/`true` (sem distinção
de maiúsculas) para enviar a consulta e os candidatos do pool pós-RRF à API
System One do OpenRouter (`POST /api/v1/systemone`, modelo
`~typesafe/jev-latest`). `ATLAS_RERANK=0` continua desligando **toda** reordenação,
inclusive Jev.

A URL default só é derivada de `ATLAS_SEMANTIC_API_URL` quando essa URL é
exatamente o endpoint HTTPS de chat do OpenRouter; a chave
`ATLAS_SEMANTIC_API_KEY` só é reutilizada nesse caso. Para um endpoint próprio,
use `ATLAS_RELEVANCE_API_URL` (apenas `https://openrouter.ai/api/v1/systemone`)
e, se quiser, `ATLAS_RELEVANCE_API_KEY`. `ATLAS_RELEVANCE_MODEL` não herda
`ATLAS_SEMANTIC_MODEL`. Default `~typesafe/jev-latest`; também aceita o alias
sem `~` e pins versionados `typesafe/jev-[0-9]…`.

Jev **refina a ordem lexical** do pool e, no perfil compacto, corta o `top_k`
([detalhes](#corte-do-top_k-por-relevância)). Uma chamada leva o pool inteiro:
o lote cabe em 192.000 bytes UTF-8, abaixo do limite do modelo (64 mil tokens
por requisição), com até duas chamadas só em overflow e timeout compartilhado
de 3 s. Timeout, HTTP ou schema inválido caem no reranker local para o lote
afetado; baixa confiança isola o candidato sem invalidar os demais. Reinicie o
MCP depois de mudar o `env`.

Cada `atlas_search` emite **uma** linha JSON de custo em stderr (conhecido,
parcialmente conhecido, zero sem chamada, ou desconhecido). Isso **não** é o
tokenizer da observabilidade nem o custo de indexação da camada semântica.
`ATLAS_OBSERVABILITY=1` só copia o ledger para o evento JSONL já existente.
Logs nunca incluem query, código, paths ou a chave.

### Como o Jev atua na resposta

O Jev recebe a consulta e os candidatos do pool pós-RRF, já na ordem do rerank
lexical, e devolve, por candidato, um score de relevância e uma confiança. Isso
acontece antes do corte `top_k` e do merge com commits. O `score` que a tool
devolve continua sendo o RRF. O cross-encoder (`ATLAS_RERANK_MODEL`) não roda
junto: só volta no fallback.

1. **Refina a ordem lexical.** Avaliação confiante ocupa as posições livres, da
   maior para a menor. Correspondência exata de símbolo permanece na frente.
   Avaliação incerta ou ausente fica onde o rerank lexical a colocou. Até
   24/09 ela ficava na posição RRF crua, e isso derrubava identificadores
   parciais (MRR 0,781 → 0,400 no replay).
2. **No perfil compacto, corta o `top_k` sem repor.** Entre os `top_k`
   primeiros, sai quem tem score abaixo da metade da escala; detalhes em
   [corte do top_k](#corte-do-top_k-por-relevância). Com
   `ATLAS_RELEVANCE_CUT=0`, vale a seleção conservadora: sai só o candidato com
   score ≤ 1/8 da escala (0,25 na v1, 0,375 na v2) e confiança ≥ 0,90, e a vaga
   é reposta pelo pool. Nos dois
   modos, se tudo sair, volta o primeiro candidato do ranking local, com o
   aviso `relevance_unconfirmed_fallback`.
3. **Falha devolve o ranking local** daquele lote (timeout, HTTP ou schema
   inválido). `ATLAS_RERANK=0` impede a chamada.

O agente passa a ver primeiro o trecho que o Jev julgou mais útil para a
consulta. `ranking_changed` registra mudança de ordem e
`removed_by_relevance` conta os descartes. `status=success` significa que o
contrato da resposta foi aceito.

### Rubrica do score

A rubrica default é a **v2**. Ela tem 4 níveis concretos, cada um com `what` e
`examples`: não relacionado, menção de passagem, contexto de apoio e evidência
direta. As instruções dizem que a consulta pode estar em português e ser
descrição, identificador exato ou parcial. Cada candidato leva `kind` e
`language` no estado. `ATLAS_RELEVANCE_RUBRIC=v1` volta aos 3 níveis antigos
(irrelevante, contexto relacionado, evidência direta). Valor inválido mantém a
v2 e aparece em `atlas_status.relevance.rubric.reason` como `invalid_rubric`.

Os limiares valem como fração do nível mais alto da rubrica, então o mesmo
corte fica em 1,0 na v1 (escala 0–2) e em 1,5 na v2 (escala 0–3). A v2 custa
cerca de 2,3× mais por chamada, porque os critérios com exemplos se repetem em
cada pergunta: ~US$ 0,001 por busca com `top_k=10` e ~US$ 0,0005 com
`top_k=5`, contra ~US$ 0,00045 e ~US$ 0,0002 da v1.

No replay de 24/09 no caminho de produção (28 consultas, `top_k=10`,
compacto, com o corte), trocar a v1 pela v2 levou o MRR de 0,500 a 0,613.
Linguagem natural foi de 0,073 a 0,312, identificador parcial de 0,781 a
0,938, os alvos entregues de 18 a 19 de 28, e os tokens de metadados de
15.012 a 14.163. Nos 12 cenários com expansão, os completos passaram de 7 para
8, com 24.184 tokens contra 26.329. Detalhes em
[`tests/eval/jev_curation_study_20260924.md`](../tests/eval/jev_curation_study_20260924.md).

Validação fora da amostra, com o Jev chamado de verdade pelo caminho de produção
em 38 consultas novas ([`tests/eval/golden_queries_holdout.yaml`](../tests/eval/golden_queries_holdout.yaml)):
com `top_k=10`, a v2 com o corte teve MRR 0,697 contra 0,507 do ranking local
(IC95 da diferença +0,094 a +0,299) e 0,558 da v1, com 39% menos tokens de
metadados que o local e nenhum alvo perdido. Com `top_k=5`: 0,640 contra 0,505
(local) e 0,564 (v1), com 27% menos tokens. Latência HTTP p50 da v2: 975 ms com
20 candidatos e 1.254 ms com 40; da v1, ~660 ms. Dados em
[`tests/eval/jev_holdout_validation_20260924.json`](../tests/eval/jev_holdout_validation_20260924.json).

### Corte do top_k por relevância

Com o Jev ligado, o perfil compacto faz do `top_k` um teto. O Atlas
deduplica o pool na ordem do Jev, fica com os `top_k` primeiros e remove quem
tem score abaixo da metade da escala: 1,5 na rubrica v2 (default) e 1,0 na v1,
onde isso equivale a p(irrelevante) > p(evidência direta). A vaga não é reposta
pelo próximo candidato nem cedida a commits; commits só ocupam vagas que o pool
já não preenchia. Correspondência
exata e candidato sem avaliação ficam. Candidato incerto também sai: exigir
confiança ≥ 0,70 anularia o corte, porque ~70% dos candidatos ficam abaixo
disso. Se tudo sair, volta o primeiro do ranking local com
`relevance_unconfirmed_fallback`.

A resposta pode ter menos itens que `top_k`; `omitted.relevance` conta os
cortados. O perfil `full` não muda. Sem `ATLAS_RELEVANCE=1`, sem avaliação
válida ou fora do perfil compacto, não há corte.

O corte é o **default**: `ATLAS_RELEVANCE_CUT` ausente ou `1`/`true` liga;
`0`/`false` volta à seleção conservadora (item 2 acima). Valor inválido mantém
o default e aparece como `reason=invalid_flag`. `atlas_status.relevance.cut`
declara configuração, política (`top_k_no_refill_v1`) e limiar.

No caminho de produção, com as notas Jev da captura de 24/09 reaplicadas sem
rede (28 consultas, rubrica v2), o corte levou os metadados de `top_k=10` de
22.681 para 14.163 tokens (−38%) e os de `top_k=5` de 13.421 para 9.933
(−26%). Nenhum alvo se perdeu; o MRR subiu de 0,577 para 0,613 em `top_k=10` e
de 0,589 para 0,607 em `top_k=5`. Nos 12 cenários com expansão, os tokens
caíram de 35.389 para 24.184 (−32%), com os mesmos 8 completos. O score oscila
com o lote, então um alvo pode sair; detalhes em
[`tests/eval/jev_curation_study_20260924.md`](../tests/eval/jev_curation_study_20260924.md).

### Dispensa de Jev para símbolo exato

`ATLAS_RELEVANCE_GATE=1` dispensa a chamada remota quando a consulta é um nome
exato e existe apenas uma correspondência de função, método ou classe no pool
recuperado. Não significa unicidade no repositório inteiro. O símbolo exato é
promovido ao primeiro lugar; os demais candidatos mantêm a ordenação local.

O default é **desligado**. Consultas naturais, identificadores parciais, nomes
ambíguos e documentos continuam seguindo a política anterior. A regra existente
para pools com menos de dois candidatos permanece. Os limiares Jev não mudam.
`atlas_status.relevance.gate` declara configuração e versão da política; buscas
dispensadas registram `status=skipped`, `reason=unique_exact_symbol`, nenhuma
chamada remota e `cost_status=not_incurred`. `ATLAS_RERANK=0` continua prevalecendo.

`ATLAS_RELEVANCE_GATE=identifier` estende a dispensa a toda consulta que seja
um identificador, com ou sem pontos (`search_hyb`, `StorageBackend.search_hybrid`).
A consulta segue com a ordem lexical e a promoção do exato, registra
`reason=identifier_query` e não chama o Jev. O custo é real: sem avaliação, o
corte do `top_k` e a reordenação da v2 não agem nessas consultas. No replay de
24/09 (`top_k=10`), as chamadas caíram de 28 para 13, mas o MRR foi de 0,613
para 0,569 (parciais de 0,938 para 0,781) e os tokens de metadados subiram de
14.163 para 18.737. Use só quando custo e latência pesarem mais que tokens.
`atlas_status.relevance.gate.policy` mostra `identifier_query_v1`.

Para avaliação pareada com Jev habilitado, usando as credenciais já configuradas
no ambiente, sem duplicar chamadas remotas entre variantes:

```bash
ATLAS_OBSERVABILITY=1 uv run python scripts/eval_context_policy.py \
  --workspace . --max-cost-usd 0.025 --out /tmp/atlas-context-policy.json
```

Mantenha índice e workspace congelados durante a medição. O harness verifica
os hashes antes de iniciar chamadas pagas e rejeita divergências.

O relatório separa custo real da baseline e custo contrafactual evitável pelo
gate. Compara expansões em lotes de cinco, dois e um; usa os alvos do cenário
somente para verificar cobertura/parar, nunca para reordenar os candidatos.
Interrompe novas consultas ao atingir o teto conhecido ou receber custo
parcialmente conhecido/desconhecido. O teto é verificado após cada consulta.

## RRF e MRR

**RRF** (Reciprocal Rank Fusion) junta as duas listas da busca híbrida: a do
vetor (cosseno dos embeddings) e a do BM25 (texto). Os scores dessas listas
estão em escalas diferentes, então a fusão usa só a posição de cada trecho:

```text
RRF(trecho) = soma, em cada lista, de 1 / (60 + posição)
```

A posição começa em 0: o 1º lugar de uma lista vale 1/60, o 2º vale 1/61.
Um trecho em 1º nas duas listas soma as duas contribuições; um que aparece
só numa lista recebe só a dela. O 60 é a constante `RRF_K`. O `score` que
`atlas_search` devolve é esse valor. O Jev reordena o pool depois da fusão.

**MRR** (Mean Reciprocal Rank) mede se o trecho certo apareceu perto do topo.
Para cada consulta do conjunto de teste, a contribuição é `1 / posição` do
primeiro acerto:

| Posição do acerto | Contribuição |
| --- | ---: |
| 1º | 1,00 |
| 2º | 0,50 |
| 5º | 0,20 |
| ausente | 0 |

O MRR é a média dessas contribuições. **1,0** significa que o alvo foi sempre
o primeiro. **0,43**, como nas 28 consultas abaixo, significa que o alvo
costuma aparecer, em geral fora do topo. O **recall@5** registra só se o alvo
entrou nos cinco primeiros; o MRR também penaliza quando ele entra tarde.

## Evidência: ordem da resposta e tamanho do contexto

A queda de tokens reproduzida no golden set é da **projeção compacta**, com
o Jev desligado. O Jev muda a ordem (e, no compacto, pode tirar um irrelevante
de alta confiança). As duas coisas se medem separadas.

**Contexto, Jev desligado.** 28 consultas, 2.480 chunks, mesma recuperação,
`relevance.requests = 0`. Fonte:
[`tests/eval/context_delivery_20260921.json`](../tests/eval/context_delivery_20260921.json).

| Entrega de `atlas_search` | Tokens | MRR | Recall@5 |
| --- | ---: | ---: | ---: |
| Metadados, perfil full | 32.336 | 0,4307 | 0,5714 |
| Metadados, projeção compacta | 21.977 | 0,4307 | 0,5714 |
| Conteúdo incluso, perfil full | 79.784 | 0,4307 | 0,5714 |
| Conteúdo incluso, projeção compacta | 69.403 | 0,4307 | 0,5714 |

A projeção compacta em metadados entregou **32,0% menos tokens** (32.336 →
21.977) com MRR e recall@5 iguais. Deduplicação e seleção ficaram ambas em
22.100 tokens: sem chamada ao Jev, a seleção não cortou candidato além da
deduplicação. O menor contexto é metadados compactos mais `atlas_expand` só
no símbolo que falta.

**Jev ligado, mesma avaliação para todas as variantes.** Modelo
`typesafe/jev-1.13-20260917`, rubrica `jev-relevance-score-v1`. Fonte:
[`tests/eval/context_policy_20260921.md`](../tests/eval/context_policy_20260921.md).
Nas 28 consultas e nos 12 cenários, baseline e gate tiveram o mesmo MRR e o
mesmo recall@5:

| Classe | MRR | Recall@5 |
| --- | ---: | ---: |
| Linguagem natural | 0,1292 | 0,375 |
| Símbolo exato | 1,0000 | 1,000 |
| Identificador parcial | 0,4345 | 0,750 |
| Entre arquivos | 0,0000 | 0,000 |

O gate evita a chamada quando a consulta já é um símbolo exato único no pool:
14 de 56 chamadas nas 28 consultas, US$ 0,003150882 contrafactuais (25,5% do
custo dessa rodada). MRR e recall permaneceram os da tabela. Linguagem natural
e relação entre arquivos continuam as classes fracas. A mediana do estágio
remoto foi 1,26 s (p50 1.264,666 ms; p95 1.371,144 ms).

Nessa mesma rodada, o encolhimento adicional veio do lote de expansão. Em
metadados, abrir dois símbolos por vez em vez de cinco passou de 42.268 para
39.260 tokens (−7,12%), com expansões de 16 para 31 e os mesmos 6/12 cenários
completos. Três execuções somaram 106 chamadas e **US$ 0,023391144**, com
todos os custos conhecidos.

A comparação publicada é gate contra Jev sempre ligado, no mesmo ranking.
O Jev contra o rerank local, no mesmo índice, foi medido em 24/09 por replay
das notas capturadas (28 consultas, `top_k=10`, perfil compacto). Rerank local
sem Jev: MRR 0,431. Jev sobre a ordem RRF crua (comportamento até então): 0,375.
Jev sobre a ordem lexical, com o corte do top_k: 0,500 com a rubrica v1 e 0,613
com a v2 (default). Detalhes em
[`tests/eval/jev_curation_study_20260924.md`](../tests/eval/jev_curation_study_20260924.md).

## Otimização de contexto

`ATLAS_CONTEXT_OPTIMIZATION` é **False** por padrão e é independente de
`ATLAS_RELEVANCE`. Com `1`/`true`, `response_profile` default resolve para
`compact` em `atlas_search` e `atlas_context` (ainda dá para forçar `full` por
chamada). Compacto: menos campos por hit, `ref` de expansão, deduplicação
conservadora, orçamento a 70% do teto. `atlas_expand(refs)` recupera até 5
chunks por id sem nova busca. Sem Jev, a compactação é só determinística.
Comece expandindo um ou dois símbolos diretamente ligados à pergunta. Leia o
resultado antes de abrir auxiliares ou continuar páginas; cinco refs é o limite
da chamada, não uma recomendação de lote. Parar quando já existe evidência evita
transferir código adicional. Lotes menores podem aumentar o número de chamadas:
essa troca também deve ser medida, não apenas o tamanho da primeira resposta.

`ref` e `covered_refs` usam IDs curtos do índice atual. A expansão consulta o
manifesto e valida o hash atual e o caminho dentro do workspace. Referências
Base64 antigas continuam aceitas com sua validação de hash. IDs curtos não
representam snapshots históricos após reindexação.

A expansão lê somente o intervalo de linhas do símbolo no arquivo original,
mesmo quando o conteúdo indexado foi truncado. O hash é validado sobre os mesmos
bytes usados na leitura. `content_range` informa offsets em caracteres Unicode
(início inclusivo, fim exclusivo). `content_complete=false` inclui `next_ref`:
passe essa referência a `atlas_expand` se precisar do restante. A última página
traz `content_complete=true`; para reconstituir o símbolo, concatene todas as
partes desde o offset zero. A continuação é vinculada ao hash do arquivo e não
usa cache; se o arquivo mudar, ela fica obsoleta mesmo após reindexação.

Classe com mais de 6.000 caracteres (~1.500 tokens) e métodos indexados abre
como **resumo**: `content` traz só o cabeçalho (assinatura, docstring e
atributos até o primeiro método), e `outline` lista os métodos diretos, cada
um com `ref`, `symbol`, `type` e `lines` (até 100; o excedente aparece em
`outline_omitted`). A resposta traz o aviso `expand_class_outline`, e
`next_ref` continua do fim do cabeçalho para quem precisar do corpo inteiro.
Medido no índice deste repositório: `StorageBackend` foi de 4.819 tokens na
primeira página (19.065 com as 4 páginas) para 1.965; `ASTChunker`, de 4.528
(13.453 em 3 páginas) para 1.433. `ATLAS_EXPAND_OUTLINE=0` volta à classe
paginada. Classe menor que o limiar abre inteira, porque ali ler tudo sai mais
barato que abrir métodos um a um.

Os tetos de bytes e tokens valem para cada resposta inteira, incluindo referências
e metadados. A paginação prefere quebras de linha; linhas muito longas podem ser
divididas por caracteres. Logs de expansão incluem `continuations_returned` e
`content_complete_results` (páginas que chegaram ao fim do símbolo).

A seleção conservadora e a deduplicação acontecem no pool antes do `top_k`.
Correspondências exatas e avaliações incertas são preservadas; se todos forem
rejeitados, retorna o primeiro candidato do ranking local com aviso. Commits
ocupam apenas vagas restantes, seguindo a política existente.

Nos logs, `bytes_recovered` e `bytes_selected` medem conteúdo interno;
`bytes_delivered` mede o JSON final. Os dois primeiros não demonstram economia
serializada. `ranking_changed` registra mudança de ordem e
`confident_evaluations` conta avaliações confiantes. `status=success` significa
processamento válido, não melhoria de qualidade. Custos desconhecidos continuam
explicitamente desconhecidos.

O benchmark usa a mesma montagem do MCP e a mesma recuperação para `full`,
projeção compacta, deduplicação e seleção. A chamada Jev é contabilizada uma vez.
Para comparar as 28 consultas e os cenários com busca mais expansões:

```bash
ATLAS_OBSERVABILITY=1 uv run python scripts/eval_search.py --delivery \
  --tasks tests/eval/task_scenarios.yaml --workspace . --out /tmp/atlas-delivery.json
```

Os cenários expandem resultados em ordem até cobrir a evidência ou esgotar as
referências, seguindo também `next_ref` (limite de 100 chamadas por cenário).
É uma política determinística de avaliação, não uma simulação de um agente.
A evidência paginada só conta quando a cadeia é contígua desde o offset zero.
Conteúdo incompleto ou obsoleto e relações não verificadas impedem classificar
a tarefa como completa. Use `ATLAS_RELEVANCE=0` para medir apenas a
compactação local; o benchmark respeita a configuração do avaliador.

### Resultado observado com expansão progressiva

Em duas execuções da mesma tarefa no Cursor, o agente passou a abrir dois
símbolos e depois mais um, em vez de cinco de uma vez. A medição controlada
das 28 consultas está em
[Evidência: ordem da resposta e tamanho do contexto](#evidência-ordem-da-resposta-e-tamanho-do-contexto).

| Métrica | Execução anterior | Expansão progressiva |
| --- | ---: | ---: |
| Tokens entregues por todas as ferramentas Atlas | 8.433 | 5.712 |
| Buscas | 5 | 2 |
| Símbolos expandidos | 5 em uma chamada | 2 + 1 em duas chamadas |
| Tokens das expansões | 4.681 | 4.304 |
| Chamadas remotas Jev | 8 | 3 |
| Mediana do tempo das buscas | 2,15 s | 1,04 s |
| Timeouts Jev | 1 | 0 |

A redução observada foi **32,3% dos tokens entregues**, principalmente por menos
operações; as expansões isoladamente reduziram **8,1%**. Os contadores usam o
tokenizador do Atlas e medem respostas das ferramentas, não o consumo total ou
o faturamento do agente. Não é uma comparação controlada nem uma economia
garantida: consultas, candidatos e operações podem variar entre execuções.

A execução custou **US$ 0,000553644** em Jev, com todos os custos
conhecidos e três símbolos entregues completos, sem truncamento ou referências
obsoletas. A anterior teve uma tentativa com custo desconhecido, portanto não
permite calcular a redução percentual do custo total. Nenhuma busca registrou
`unique_exact_symbol`: este teste não valida a dispensa por símbolo exato nem
atribui a ela a economia. Os logs também não comprovam a qualidade da resposta final.

## Observabilidade de tokens por consulta

`ATLAS_OBSERVABILITY=1` liga o registro local da string JSON final devolvida por
`atlas_search`, `atlas_context`, `atlas_brief` e `atlas_graph`: chars/bytes e tokens
exatos segundo o **tokenizer padrão do Atlas**, já incluído no pacote.
**Isto mede o texto retornado pela tool, não o prompt inteiro do cliente MCP,
geração do modelo ou faturamento.**

O padrão é o tokenizer de **HuggingFaceTB/SmolLM2-135M**, Apache-2.0, revisão
`93efa2f097d58c2a74874c7e644dbc9b0cee75a2`. O arquivo (~2,1 MB) vem no wheel/sdist,
com licença, origem e SHA-256 fixos em [assets](../src/codesteer_atlas/assets/README.md).
Não há download na primeira consulta nem acesso à rede para contar tokens.
O carregamento acontece apenas quando a primeira medição precisar dele.

Para ativar, acrescente ao ambiente do servidor MCP e reinicie-o:

```json
"env": {
  "ATLAS_OBSERVABILITY": "1"
}
```

Desligado (padrão): nenhum arquivo de eventos ou histórico em memória é criado,
e o bloco `observability` fica ausente de `atlas_status`. O contador continua
sendo usado pelos limites de resposta. Ligado: cada tool grava o último evento
em memória (`atlas_status.observability.last_by_tool`) e tenta persistir em
`.code-index/observability/events.jsonl` (rotação em até 3 arquivos de 1 MiB,
~3 MiB no total). Contenção do lock ou falha de E/S descarta a persistência
**daquele** evento (nunca a consulta), incrementa `dropped_events` e avisa uma
vez por transição em stderr.

Para substituir o padrão, configure também `ATLAS_TOKENIZER_PATH` com o caminho
para um `tokenizer.json` compatível com a lib
[`tokenizers`](https://huggingface.co/docs/tokenizers). Variável ausente ou vazia
seleciona o embarcado. Os eventos identificam `tokenizer_source` (`bundled` ou
`custom`), `tokenizer_name`, `tokenizer_revision` e `tokenizer_sha256`. No override,
nome é `custom`, revisão é `null` e o hash identifica o arquivo sem expor o path.
**O tokenizer escolhido não é necessariamente o do cliente MCP**: mantenha a
mesma revisão para comparar medições reproduzíveis.

Arquivo ausente/inválido, recurso embarcado com hash incorreto ou biblioteca
indisponível: degrada para estimativa `ceil(chars/4)`, com
`tokenizer_status: "unavailable"`, e informa o motivo em stderr (`[atlas]`).
Um override inválido não é substituído silenciosamente pelo embarcado.
A falha é memorizada; reinicie o servidor após corrigir ou trocar o arquivo.

O teto de resposta é aplicado **independentemente** da observabilidade.
Search corta resultados inteiros da cauda; context/brief/graph mantêm suas
prioridades de corte. O bloco `budget` declara `mode`, `max_chars`, `max_bytes`,
`max_tokens`, `tokenizer_sha256` e `used_chars`. Nas respostas compactas
(`atlas_search` e `atlas_context` no perfil compacto, e `atlas_expand`), o
bloco só aparece quando algo foi cortado para caber. Sem corte, ele custaria
~121 tokens por resposta, 26% de uma busca compacta com 5 itens. Com o padrão
carregado, `mode="tokenizer_exact"` e os tetos de tokens passam a valer sem configuração
manual. Isso pode cortar respostas que antes cabiam apenas em chars/bytes.
Se o contador estiver indisponível, o limite passa a chars/bytes:
`mode="byte_bpe_upper_bound"`, `max_tokens=null`, sem garantia de tokens exatos.

## Camada semântica

Para gerar propósito por símbolo e sumários hierárquicos, habilite explicitamente
`ATLAS_SEMANTIC=1`. A cadeia usa sampling apenas no caminho MCP síncrono, depois um
endpoint local configurado por `ATLAS_SEMANTIC_LOCAL_URL` e, por último, uma API cujo URL
foi declarado em `ATLAS_SEMANTIC_API_URL`. Sem origem, o índice estrutural continua completo.

APIs OpenAI-compatible, incluindo OpenRouter, usam também `ATLAS_SEMANTIC_MODEL`.
Exemplo de URL e modelo: `ATLAS_SEMANTIC_API_URL=https://openrouter.ai/api/v1/chat/completions`
e `ATLAS_SEMANTIC_MODEL=openai/gpt-4.1-mini`. A chave fica somente em
`ATLAS_SEMANTIC_API_KEY`. Com o modelo definido, o Atlas envia `model` + `messages`;
sem ele, preserva o payload genérico legado para endpoints customizados.

O índice novo usa formato `2.3.0` e grava `purpose`, `purpose_hash`, `purpose_vector` e o
sidecar `.code-index/semantic.json`; o vetor estrutural não muda. Índices `2.0.x`–`2.2.x`
continuam buscáveis como legados. Para convertê-los, use somente `atlas-index --full` sem
`--paths`; recortes incrementais não fazem migration nem misturam schemas. Consulte
`atlas_status` para auditar `origin`, `egress`, `index` e `last_generation`.

Uma busca degradada inclui `warnings`: `semantic_layer_unavailable` indica camada ligada
sem índice pronto e `semantic_arm_unavailable` indica falha do vetor semântico; em ambos
os casos vector+FTS continuam disponíveis, mas a recuperação semântica está incompleta.

## Onde fica o `.code-index`

Ordem de resolução:

1. `--index-dir` (CLI)
2. `ATLAS_INDEX_DIR` (env)
3. Busca ascendente a partir do CWD
4. Busca a partir da raiz do editor (`CLAUDE_PROJECT_DIR`, `WORKSPACE_FOLDER_PATHS`)
5. Fallback `.code-index` relativo à raiz conhecida (ou ao CWD)

Com MCP ligado **ao projeto** (plugin project/local ou `mcp.json` na raiz), o item 3 ou 4 costuma bastar depois de `atlas-index --workspace .`, quando o processo nasce na raiz ou o editor exporta `CLAUDE_PROJECT_DIR` / `WORKSPACE_FOLDER_PATHS`.

No Cursor o processo do MCP nasce na pasta pessoal e essa variável de workspace não entra no ambiente do servidor. A recuperação por roots só roda na primeira ferramenta, e só se a partida não tiver travado outro `.code-index` no caminho. A reindexação de abertura usa o caminho resolvido na partida. Por isso o manifest do Cursor define `ATLAS_INDEX_DIR=${workspaceFolder}/.code-index`: o editor substitui `${workspaceFolder}` pela pasta que contém `.cursor/mcp.json`.

Se o servidor nascer com CWD errado (caso típico de instalação **global**), o Atlas tenta recuperar via MCP `roots/list` quando o cliente suporta. Mesmo assim, **prefira instalação por projeto** — é o caminho estável.

Para forçar um caminho explícito no `mcp.json` do projeto:

```json
{
  "mcpServers": {
    "codesteer-atlas": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git",
        "atlas-serve"
      ],
      "env": {
        "ATLAS_INDEX_DIR": "${workspaceFolder}/.code-index"
      }
    }
  }
}
```

> No Cursor, `${workspaceFolder}` é a forma mais segura de amarrar o índice ao projeto aberto. Veja [CONTRIBUTING.md — Cursor](../CONTRIBUTING.md#cursor).

Diagnóstico: `atlas_status` → `index_resolution`.

## Configuração e variáveis de ambiente

Todas as flags abaixo são **opt-in ou de override**. Sem elas, o Atlas indexa, busca e serve o grafo no computador local.

| Variável | Default | Efeito |
|---|---|---|
| `ATLAS_INDEX_DIR` | discovery / fallback `.code-index` | Caminho explícito do índice (prioridade 2 da resolução). |
| `ATLAS_RERANK` | ligado | `0` desliga **toda** reordenação pós-RRF (lexical e cross-encoder). |
| `ATLAS_RERANK_MODEL` | ausente | Presente → cross-encoder ONNX (o valor é o slug do modelo; default `Xenova/ms-marco-MiniLM-L-6-v2`). Falha de carga: `warnings: cross_encoder_unavailable`. |
| `ATLAS_WATCH` | desligado | `1` observa o workspace e dispara reindex incremental em subprocesso após 2 s. Extra: `codesteer-atlas[watch]` (`watchdog`). Sem o extra: `watch: "unavailable"`. |
| `ATLAS_SCIP` | desligado | `1` invoca o indexador SCIP da linguagem (`scip-python`, `scip-typescript`, `scip-go`, `rust-analyzer`) e produz arestas `calls`. Sem toolchain: `scip_status: "toolchain_missing"`. |
| `ATLAS_SEMANTIC` | desligado | `1` liga a camada de propósito por símbolo. Sem origem configurada, o índice estrutural continua completo. Detalhes: [Camada semântica](#camada-semântica). |
| `ATLAS_SEMANTIC_LOCAL_URL` | ausente | Endpoint local (segunda origem, depois do sampling MCP). |
| `ATLAS_SEMANTIC_API_URL` | ausente | URL explícita de API (terceira origem). Sem host default. |
| `ATLAS_SEMANTIC_API_KEY` | ausente | Só no header da API. |
| `ATLAS_SEMANTIC_MODEL` | ausente | Contrato OpenAI-compatible (`model` + `messages`). Sem ele, payload genérico legado. |
| `ATLAS_OBSERVABILITY` | desligado | `1` grava eventos de medição de resposta (chars/bytes/tokens) em memória + `.code-index/observability/events.jsonl` e expõe `atlas_status.observability`. Sem ele, nada é criado. Detalhes: [Observabilidade de tokens por consulta](#observabilidade-de-tokens-por-consulta). |
| `ATLAS_RELEVANCE` | desligado | `1`/`true` julga relevância com o modelo Jev (TypeSafe) antes do `top_k`, pela API System One do OpenRouter. Default False. `ATLAS_RERANK=0` vence. Detalhes: [Relevância com o modelo Jev](#relevância-com-o-modelo-jev). |
| `ATLAS_RELEVANCE_API_URL` | derivado só de OpenRouter chat | Endpoint HTTPS System One. Sem host implícito genérico. |
| `ATLAS_RELEVANCE_API_KEY` | reuso condicional | Se ausente, reutiliza `ATLAS_SEMANTIC_API_KEY` somente quando a URL semântica é OpenRouter HTTPS. |
| `ATLAS_RELEVANCE_MODEL` | `~typesafe/jev-latest` | Não herda `ATLAS_SEMANTIC_MODEL`. Alias OpenRouter ou pin `typesafe/jev-[0-9]…`. |
| `ATLAS_RELEVANCE_GATE` | desligado | `1`/`true` dispensa Jev para símbolo exato único no pool; preserva promoção local do exato. `identifier` dispensa para toda consulta-identificador (menos chamadas, mais tokens). |
| `ATLAS_RELEVANCE_RUBRIC` | `v2` | Rubrica do score Jev: `v2` (4 níveis com exemplos) ou `v1` (3 níveis, mais barata). Detalhes: [Rubrica do score](#rubrica-do-score). |
| `ATLAS_RELEVANCE_CUT` | ligado | Com o Jev e o perfil compacto, faz do `top_k` um teto: remove, sem repor, quem tem score Jev abaixo da metade da escala (1,5 na v2, 1,0 na v1) entre os `top_k` primeiros. `0`/`false` volta à seleção conservadora. Detalhes: [Corte do top_k por relevância](#corte-do-top_k-por-relevância). |
| `ATLAS_CONTEXT_OPTIMIZATION` | desligado | `1`/`true` faz `response_profile=default` resolver para compacto em search/context. Independente do Jev. Detalhes: [Otimização de contexto](#otimização-de-contexto). |
| `ATLAS_EXPAND_OUTLINE` | ligado | Classe acima de ~1.500 tokens abre em `atlas_expand` como cabeçalho + `outline` dos métodos. `0`/`false` volta à classe inteira paginada. |
| `ATLAS_TOKENIZER_PATH` | ausente | Caminho de um `tokenizer.json` local (lib `tokenizers`) para contagem exata de tokens e teto de tokens no orçamento de resposta. Independente de `ATLAS_OBSERVABILITY`. Sem ele (ou inválido), estimativa `ceil(chars/4)` identificada como tal — `max_tokens` fica `null`; isso é esperado. |

História de Git **não tem variável de ambiente**. A janela é teto interno (até 100 commits por arquivo e 24 meses).

No `mcp.json` do projeto:

```json
{
  "mcpServers": {
    "codesteer-atlas": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git",
        "atlas-serve"
      ],
      "env": {
        "ATLAS_INDEX_DIR": "${workspaceFolder}/.code-index",
        "ATLAS_WATCH": "1"
      }
    }
  }
}
```
