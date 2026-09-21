# CodeSteer Atlas

O assistente do seu editor encontra o trecho certo do seu projeto, na sua máquina. O Atlas indexa o código uma vez e entrega ao agente o símbolo, o contexto da tarefa e o alcance de uma mudança.

Na configuração padrão, indexação e busca acontecem no seu computador. O código permanece local. A instalação vem do GitHub pelo [uv](https://github.com/astral-sh/uv); clonar este repositório fica para quem desenvolve o Atlas.

**Primeira vez?** O [guia de primeiros passos](https://luiscarloslopes.github.io/codesteer-atlas/primeiros-passos.html) acompanha a preparação, a conexão e a primeira busca, com exemplos para copiar. [Abrir a versão local](docs/primeiros-passos.html).

| Recurso | Para quê |
| --- | --- |
| [Documentação visual](https://luiscarloslopes.github.io/codesteer-atlas/) | Conceitos, busca e indexação |
| [Guia de indexação, grafo e MCP](docs/guia-indexacao-grafo-mcp.md) | Pipeline, diagramas e o mapa `graph.html` |

## O que você ganha

Varrer o repositório com busca textual gasta o contexto do agente e ainda pode abrir o arquivo errado. O Atlas muda esse caminho.

- **O trecho é um símbolo.** Classe, função e método entram no índice como unidades. Um arquivo vira um mapa, não uma fatia de linhas:

  ```text
  src/auth/service.py
    ├── class AuthService
    ├── AuthService.login
    └── AuthService.logout
  ```

- **A busca junta sentido e texto.** Uma pergunta em linguagem natural e o nome exato de uma função usam o mesmo índice.
- **A tarefa cabe em poucas chamadas.** Um briefing apresenta o projeto. Um pacote reúne o que importa para editar, depurar, revisar ou entender um símbolo. O grafo mostra o que uma mudança alcança.
- **A atualização acompanha o que mudou.** A primeira passagem lê o projeto; as seguintes reindexam arquivos novos ou alterados. Ao reabrir o editor, se o índice já existir, essa atualização roda em segundo plano.
- **Várias linguagens no mesmo índice.** Python, JavaScript, TypeScript, Go, Java, C#, Dart, Pascal, VB6, Razor, XML, Markdown e outras. Notas `NOTE`/`WHY`, decisões e a história recente do Git do próprio projeto também entram na busca.

## Começar

Pré-requisitos: Python 3.11–3.13 e [uv](https://github.com/astral-sh/uv).

O índice fica em `.code-index/` na raiz **do projeto que você quer consultar**. Acrescente essa pasta ao `.gitignore`.

### 1. Conectar o MCP no seu projeto

> **Instale no projeto atual.**
>
> Um MCP global inicia na pasta pessoal, sem a raiz do projeto aberto, e pode gravar ou achar o índice no lugar errado.
>
> Use o plugin no projeto atual (escopo *project* ou *local*) ou um `mcp.json` / `.mcp.json` **na raiz do projeto**.

#### Opção A — Plugin no projeto atual (Claude Code)

```text
/plugin marketplace add LuisCarlosLopes/codesteer-atlas

/plugin install codesteer-atlas
```

Quando o Claude Code pedir o escopo, escolha **Project** (compartilhado no repo) ou **Local** (só neste workspace).

Pela CLI:

```bash
claude plugin install codesteer-atlas --scope project
# ou: --scope local
```

#### Opção B — `mcp.json` na raiz do projeto

Recomendado para Cursor, VS Code, Kiro e OpenCode. Copie o manifest para o projeto e reinicie o cliente.

| Cliente | Copiar de | Para |
| --- | --- | --- |
| Cursor | [`examples/clients/cursor/mcp.json`](examples/clients/cursor/mcp.json) | `.cursor/mcp.json` |
| GitHub Copilot (VS Code) | [`examples/clients/vscode/mcp.json`](examples/clients/vscode/mcp.json) | `.vscode/mcp.json` |
| Kiro | [`examples/clients/kiro/settings/mcp.json`](examples/clients/kiro/settings/mcp.json) | `.kiro/settings/mcp.json` |
| OpenCode | [`examples/clients/opencode/opencode.json`](examples/clients/opencode/opencode.json) | `opencode.json` |
| Claude Code | [`.mcp.json`](.mcp.json) | `.mcp.json` na raiz do projeto |

O `atlas-index` grava `.code-index` na raiz do repositório. Ao abrir, o servidor procura essa pasta subindo a partir da pasta em que o processo nasceu.

No Cursor esse processo nasce na pasta pessoal. A subida a partir dali não alcança o `.code-index` do repositório aberto, e o servidor não recebe a raiz do projeto por conta própria. A linha `ATLAS_INDEX_DIR` fecha esse caminho: o Cursor troca `${workspaceFolder}` pela pasta que contém o `.cursor/mcp.json`, a raiz do repositório. Sem essa linha, as ferramentas olham outro índice, ou nenhum.

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

O manifest pronto está em [`examples/clients/cursor/mcp.json`](examples/clients/cursor/mcp.json). Copie-o para `.cursor/mcp.json` e reinicie o Cursor. A linha que amarra o índice é `ATLAS_INDEX_DIR`.

No Claude Code, com o plugin no escopo do projeto, o editor informa a raiz e o bloco sem `ATLAS_INDEX_DIR` basta. Se `atlas_status` apontar o índice para fora do repositório, use a mesma variável com o caminho absoluto de `.code-index`. Outros editores: [CONTRIBUTING.md](CONTRIBUTING.md#configuração-manual-em-outros-clientes).

#### Outros canais, também por projeto

- **Kiro Power**: Add Custom Power → Import from GitHub → `https://github.com/LuisCarlosLopes/codesteer-atlas.git`, associado ao workspace atual.
- **Copilot CLI**: instale no repositório em que você vai trabalhar.

```bash
copilot plugin install LuisCarlosLopes/codesteer-atlas
```

### 2. Indexar o projeto

Na raiz do **seu** projeto:

```bash
cd /caminho/para/seu-projeto

uv tool install git+https://github.com/LuisCarlosLopes/codesteer-atlas.git

atlas-index --workspace .
```

Sem instalar no PATH, o mesmo comando baixa o pacote a cada execução:

```bash
uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace .
```

Ao terminar, a mensagem é `Indexação Concluída com Sucesso!`. A pasta `.code-index/` passa a ter o índice, o grafo e o mapa `graph.html`, que abre no navegador.

A primeira indexação baixa o modelo de busca. As seguintes, na configuração padrão, seguem sem rede.

Para atualizar o programa depois: `uv tool upgrade codesteer-atlas`.

### 3. Usar

Com o MCP conectado e o índice criado, peça ao agente para procurar no projeto. Nas próximas vezes, a atualização incremental basta:

```bash
atlas-index --workspace .
atlas-index --workspace . --full
atlas-index --workspace . --paths src --paths docs
```

O agente também pode chamar a ferramenta `atlas_index`. A primeira indexação continua manual. A partir daí, abrir o editor dispara uma atualização incremental em segundo plano. O registro fica em `.code-index/background_reindex.log`.

## Como a indexação funciona

O `atlas-index` percorre o projeto, separa cada arquivo em trechos, gera um vetor local para cada trecho e grava tudo em `.code-index/`. A primeira passagem lê o que é elegível. As seguintes comparam o hash de cada arquivo e só refazem o que mudou ou foi apagado. `--full` reconstrói o índice inteiro.

O resultado é a pasta com o índice de busca, o grafo (`graph.json` e `graph.html`) e a história recente do Git do próprio repositório (`history.json`).

### Código

Arquivos com parser (Python, JavaScript, TypeScript, Go, Java, C# e outras linguagens da lista) são lidos pela árvore sintática. Cada classe, função e método vira um trecho com nome, como `AuthService.login`, e com as linhas de origem. Se o arquivo não tem símbolo reconhecido, ele entra inteiro como um módulo.

Um trecho grande demais guarda o começo (assinatura) e o fim, e marca o miolo como cortado.

### SQL

Cada comando vira um trecho: `CREATE TABLE`, `CREATE VIEW`, `SELECT` e os demais. O nome segue a tabela, a view ou a função. Um `SELECT` sem nome próprio usa a tabela do `FROM`. Comando longo demais é partido por linhas.

### Markdown

Cada título (`#`, `##`, …) abre uma seção. Seção longa é partida em parágrafos, em blocos de cerca de 1000 caracteres.

### Texto e formatos sem símbolo

XML, Razor, texto puro e os demais formatos sem classe ou função entram por parágrafo, nos mesmos blocos de cerca de 1000 caracteres. O nome do trecho é o do arquivo, com um índice quando há mais de um bloco.

### O que fica de fora

A varredura ignora `.git`, `node_modules`, `.venv`, `__pycache__`, a própria pasta `.code-index`, arquivos ocultos, extensões que o Atlas não lê e arquivos acima de 2 MB. Um `.atlasignore` na raiz soma regras no formato do `.gitignore`.

## O que o agente passa a fazer

| Ferramenta | Para que serve |
| --- | --- |
| `atlas_brief` | Apresentar um projeto ainda desconhecido: linguagens, camadas e pontos de entrada. Uma vez basta. |
| `atlas_search` | Localizar onde uma ideia ou um nome está implementado. A resposta traz arquivo e linhas. |
| `atlas_context` | Montar o pacote de um símbolo ou arquivo para editar, depurar, revisar ou entender. |
| `atlas_expand` | Abrir o código de um resultado compacto, um ou dois símbolos por vez. |
| `atlas_graph` | Ver conexões, o caminho entre dois pontos e o que uma mudança alcança. |
| `atlas_index` | Criar ou atualizar o índice e o mapa. |
| `atlas_status` | Conferir se o índice está em dia. |

Há também o recurso somente leitura `atlas://status`.

## Configuração do MCP

As variáveis ficam em `env` no `mcp.json` do projeto. Reinicie o editor depois de salvar. O valor `1` liga as flags abaixo; onde a coluna de exemplo diz outra coisa, use esse texto.

### Menor contexto, com logs

Este bloco entrega a busca e o pacote da tarefa no perfil compacto e grava, na sua máquina, o tamanho de cada resposta. O perfil compacto foi o que reduziu cerca de 32% dos tokens de metadados nas 28 consultas (32.336 para 21.977). Os logs ficam em `.code-index/observability/events.jsonl` e medem o texto devolvido pela ferramenta, não o faturamento do editor.

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
        "ATLAS_CONTEXT_OPTIMIZATION": "1",
        "ATLAS_OBSERVABILITY": "1"
      }
    }
  }
}
```

O Jev fica de fora: ele reordena e pode retirar um irrelevante, e também envia a pergunta e os trechos para fora da máquina. Para ligá-lo, use as três variáveis da seção [Jev](#jev-o-trecho-mais-útil-na-frente).

### Parâmetros

| Variável | Exemplo | Função |
| --- | --- | --- |
| `ATLAS_INDEX_DIR` | `${workspaceFolder}/.code-index` | Pasta do índice. No Cursor, amarra o servidor ao `.code-index` da raiz do repositório. |
| `ATLAS_CONTEXT_OPTIMIZATION` | `1` | Resposta compacta em `atlas_search` e `atlas_context`. Também aceita `true`. É o que reduz o contexto. |
| `ATLAS_OBSERVABILITY` | `1` | Log local de caracteres, bytes e tokens por resposta. Só o valor `1` liga. |
| `ATLAS_TOKENIZER_PATH` | `/caminho/para/tokenizer.json` | Troca o contador de tokens. Vazio usa o tokenizer que já vem no pacote. |
| `ATLAS_RELEVANCE` | `1` | Liga o Jev. Sozinha não basta: a URL e a chave abaixo também entram. Também aceita `true`. |
| `ATLAS_RELEVANCE_GATE` | `1` | Dispensa o Jev quando a consulta já é o nome exato de uma única função, método ou classe entre os candidatos. |
| `ATLAS_RELEVANCE_API_URL` | `https://openrouter.ai/api/v1/systemone` | Endpoint do Jev. Só essa URL HTTPS. |
| `ATLAS_RELEVANCE_API_KEY` | a chave do OpenRouter | Credencial do Jev. Se vazia, reutiliza `ATLAS_SEMANTIC_API_KEY` quando a URL semântica é o chat HTTPS do OpenRouter. |
| `ATLAS_RELEVANCE_MODEL` | `~typesafe/jev-latest` | Modelo do Jev. Não herda `ATLAS_SEMANTIC_MODEL`. |
| `ATLAS_RERANK` | `0` | Desliga toda reordenação, inclusive o Jev e o cross-encoder. Ausente, a reordenação local fica ligada. |
| `ATLAS_RERANK_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` | Cross-encoder local no lugar da reordenação por texto. Ausente, permanece a reordenação local. |
| `ATLAS_WATCH` | `1` | Reindexa sozinho depois que você salva. Exige o extra `codesteer-atlas[watch]`. Só o valor `1` liga. |
| `ATLAS_SCIP` | `1` | Registra quem chama quem, se o indexador da linguagem estiver instalado. Só o valor `1` liga. |
| `ATLAS_SEMANTIC` | `1` | Gera o propósito de cada símbolo. Sem URL configurada, o índice estrutural segue completo. Só o valor `1` liga. Envia conteúdo para a origem configurada. |
| `ATLAS_SEMANTIC_LOCAL_URL` | `http://127.0.0.1:8080/v1/chat/completions` | Endpoint local de propósito. Entra depois do sampling do MCP. |
| `ATLAS_SEMANTIC_API_URL` | `https://openrouter.ai/api/v1/chat/completions` | API de propósito. Não há host padrão. |
| `ATLAS_SEMANTIC_API_KEY` | a chave da API | Vai somente no header. Não entra em log. |
| `ATLAS_SEMANTIC_MODEL` | `openai/gpt-4.1-mini` | Liga o contrato `model` + `messages`. Sem ele, a API recebe o payload genérico. |

## Jev: o trecho mais útil na frente

O Jev é o avaliador de relevância da OpenRouter (System One). Ele vem **desligado**. Com ele desligado, busca e índice continuam na sua máquina.

Ligado, o Jev lê a pergunta e os candidatos que o Atlas já recuperou. Coloca na frente o trecho que julgou mais útil para aquela pergunta. No perfil compacto, pode retirar um candidato claramente irrelevante. Quem encontra os candidatos continua sendo a busca local.

A entrega compacta, com o Jev desligado, reduziu cerca de **32%** dos tokens de metadados nas 28 consultas de avaliação (32.336 para 21.977), com o alvo na mesma posição. Numa sessão no Cursor, abrir menos símbolos por vez levou o total das ferramentas de 8.433 para 5.712 tokens. O Jev muda a ordem desses resultados. A comparação da qualidade dele com o ranking local ainda está por medir.

### O que precisa estar no `env`

Três variáveis ligam o Jev. Com a flag sozinha, sem URL e sem chave, o avaliador permanece desconfigurado.

| Variável | Valor |
| --- | --- |
| `ATLAS_RELEVANCE` | `1` (ou `true`) |
| `ATLAS_RELEVANCE_API_URL` | `https://openrouter.ai/api/v1/systemone` |
| `ATLAS_RELEVANCE_API_KEY` | a chave da sua conta no OpenRouter |

```json
"env": {
  "ATLAS_INDEX_DIR": "${workspaceFolder}/.code-index",
  "ATLAS_RELEVANCE": "1",
  "ATLAS_RELEVANCE_API_URL": "https://openrouter.ai/api/v1/systemone",
  "ATLAS_RELEVANCE_API_KEY": "sua-chave-openrouter"
}
```

`ATLAS_INDEX_DIR` aponta o índice no Cursor; não faz parte do Jev. Reinicie o editor depois de salvar.

Se `ATLAS_SEMANTIC_API_URL` já for exatamente `https://openrouter.ai/api/v1/chat/completions`, a URL do Jev sai dessa e a chave pode ser a mesma `ATLAS_SEMANTIC_API_KEY`. Fora desse caso, URL e chave do Jev são obrigatórias.

O modelo padrão é `~typesafe/jev-latest`. `ATLAS_RELEVANCE_MODEL` só entra para trocar por um pin `typesafe/jev-…`. `ATLAS_RELEVANCE_GATE=1` é opcional: dispensa a chamada quando a consulta já é o nome exato de uma única função, método ou classe entre os candidatos. `ATLAS_RERANK` precisa continuar ausente ou diferente de `0`; com `0`, o Jev não roda.

Nesse modo, a consulta e os trechos candidatos seguem para o OpenRouter. Cada busca escreve uma linha de custo no log do servidor, sem a pergunta, o código ou a chave. Na rodada medida, o gasto ficou em frações de centavo.

## Menos contexto na conversa

`ATLAS_CONTEXT_OPTIMIZATION=1` faz a resposta padrão de `atlas_search` e `atlas_context` vir compacta: menos campos, sem repetir o mesmo símbolo, e um identificador para abrir só o que faltar com `atlas_expand`. Funciona com ou sem o Jev. A redução de tokens citada acima veio dessa entrega. O bloco pronto, já com os logs, está em [Menor contexto, com logs](#menor-contexto-com-logs).

## Instruções para agentes de IA (AGENTS.md / CLAUDE.md)

Copie o bloco para as instruções do projeto em que o Atlas está conectado.

| Cliente | Onde colar |
| --- | --- |
| Cursor, Copilot (VS Code), Codex e editores genéricos | `AGENTS.md` na raiz do projeto |
| Claude Code | `CLAUDE.md`, que pode importar o `AGENTS.md` |
| Kiro | regras do Power ou instruções do agente |
| GitHub Copilot CLI | instruções do plugin ou regras do projeto |

O [AGENTS.md](AGENTS.md) deste repositório é o das pessoas que desenvolvem o Atlas. No seu projeto, o bloco abaixo é o suficiente:

```markdown
# Busca de código com o Atlas

Use o Atlas antes de varrer o repositório para entender, planejar ou investigar
código e documentos deste projeto.

- Símbolo ou arquivo já conhecido: `atlas_context(target, intent)` com
  `edit`, `debug`, `review` ou `understand`.
- Projeto desconhecido: `atlas_brief` uma vez.
- Localizar uma implementação: `atlas_search` com metadados e `top_k` baixo.
- Abrir o código de um resultado compacto: `atlas_expand` com uma ou duas
  refs ligadas à pergunta. Leia a resposta antes de pedir mais.
- Alcance de uma mudança: `atlas_graph` com `mode="affected"`.
- Índice ausente ou desatualizado: `atlas_index`.

Omita `response_profile` para respeitar a configuração do projeto. Pare quando
a evidência bastar. Se o MCP estiver indisponível, use as ferramentas do editor
e registre essa limitação.
```

## Quando quiser ir além

- **Mapa visual.** Abra `.code-index/graph.html` no navegador.
- **Reindexar ao salvar.** `ATLAS_WATCH=1` observa o projeto e atualiza o índice depois de uma pausa curta. Requer o extra `codesteer-atlas[watch]`.
- **Quem chama quem.** `ATLAS_SCIP=1` usa o indexador da linguagem, quando ele está instalado, e registra chamadas entre símbolos.
- **Resumo do propósito de cada símbolo.** `ATLAS_SEMANTIC=1` consulta uma API que você configurar. Sem essa origem, o índice estrutural segue completo. Esse modo também envia conteúdo para fora da máquina.
- **Medir o tamanho das respostas.** `ATLAS_OBSERVABILITY=1` grava, na sua máquina, caracteres, bytes e tokens do que cada ferramenta devolveu.
- **Deixar pastas de fora.** Um `.atlasignore` na raiz do projeto usa a mesma sintaxe do `.gitignore`. Pastas como `.git`, `node_modules`, `.venv` e o próprio `.code-index` já ficam de fora.

```gitignore
*.log
fixtures/
/dist
```

## Detalhe técnico

Contrato do Jev, medições, tokenizer e a ordem de resolução de `.code-index` estão na [referência do operador](docs/referencia.md). Os valores para colar no `mcp.json` estão na [configuração](#configuração-do-mcp).

O pipeline de indexação, para quem altera o Atlas, está em [CONTRIBUTING.md](CONTRIBUTING.md#pipeline-de-indexação-detalhado).

## Contribuindo

Clonar o repositório, testes e lint: [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

Veja [LICENSE](LICENSE).
