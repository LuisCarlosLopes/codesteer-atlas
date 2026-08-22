from pathlib import Path

# Nome do modelo de embedding local (all-MiniLM-L6-v2) - Requisito do ARD [GA-02]
MODEL_NAME = "all-MiniLM-L6-v2"

# Diretório padrão para salvar os arquivos do banco de dados LanceDB e manifest
DEFAULT_INDEX_DIR = Path(".code-index")

# Tamanho máximo de arquivo de código a ser indexado (2MB)
MAX_FILE_SIZE = 2 * 1024 * 1024

# Limite recomendado de tokens por chunk para o modelo all-MiniLM-L6-v2
MAX_TOKENS_PER_CHUNK = 256

# Constante de suavização para o algoritmo Reciprocal Rank Fusion (RRF)
RRF_K = 60

# Limite de candidatos buscados em cada braço (vetorial e FTS) antes da fusão RRF.
# Aplicado COM prefilter (where) para garantir top_k completos mesmo com filtros seletivos [E]
CANDIDATES_LIMIT = 50

# Quantos candidatos além de `top_k` entram na reordenação pós-RRF. Reordenar
# exatamente `top_k` só embaralharia o que já estava correto; o ganho vem de
# promover um acerto da posição ~12 para dentro do top 5. Limitado por
# CANDIDATES_LIMIT, que é o teto do que cada braço trouxe.
RERANK_POOL_MULTIPLIER = 4

# Variável de ambiente que desliga a reordenação pós-RRF (valor "0"), voltando ao
# RRF puro. Existe para A/B no harness de avaliação e para rollback sem redeploy.
RERANK_ENV_FLAG = "ATLAS_RERANK"

# Termos ignorados pela reordenação pós-RRF ao calcular boost de título e proximidade:
# aparecem em quase todo chunk e dariam boost a candidato irrelevante. Cobre pt-BR,
# inglês e genéricos de código. Comparação sobre o token normalizado (minúsculo, sem
# acento) por `ranking.fold`.
#
# Deliberadamente NÃO são podados da query enviada ao BM25: medido, isso não melhora
# nada (−0.002 de MRR total) e piora a classe de linguagem natural.
QUERY_STOPWORDS = frozenset(
    {
        # pt-BR — artigos, preposições, conectivos, interrogativos
        "a", "as", "o", "os", "um", "uma", "uns", "umas",
        "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas",
        "por", "para", "pra", "com", "sem", "sob", "sobre", "entre", "ate",
        "ao", "aos", "e", "ou", "que", "se", "ser", "sao", "eh", "esta", "estao",
        "como", "qual", "quais", "quando", "onde", "quem", "porque", "qro",
        "isso", "isto", "esse", "essa", "este", "aquilo", "seu", "sua",
        "mais", "menos", "muito", "pouco", "todo", "toda", "todos", "todas",
        "ja", "nao", "sim", "tambem", "so", "apenas", "cada", "outro", "outra",
        # inglês — "a"/"as"/"do" já entraram na seção pt-BR acima
        "the", "an", "and", "or", "of", "in", "on", "at", "to", "for",
        "from", "with", "without", "by", "is", "are", "was", "were",
        "be", "been", "it", "its", "this", "that", "these", "those", "there",
        "how", "what", "which", "when", "where", "who", "why", "does",
        "did", "can", "should", "would", "into", "over", "than", "then",
        # genéricos de código — presentes em quase todo chunk, sem poder discriminante
        "def", "class", "function", "funcao", "method", "metodo", "return",
        "retorna", "self", "value", "valor", "data", "dado", "dados",
        "code", "codigo", "file", "arquivo", "arquivos", "test", "teste",
        "update", "atualiza", "fix", "corrige", "add", "adiciona",
        "get", "set", "new", "novo", "nova", "usar", "usa", "using",
    }
)

# Versão mínima de manifest aceita pelo server; manifests anteriores usam backend
# de embeddings incompatível (sentence-transformers/torch) e exigem reindexação
MIN_INDEX_VERSION = "2.0.0"
CURRENT_INDEX_VERSION = "2.1.0"

# Versão do PRODUTOR de chunks (ASTChunker), separada de CURRENT_INDEX_VERSION de
# propósito: o formato do índice pode ficar parado enquanto a extração melhora. É
# comparada na INDEXAÇÃO (nunca na leitura — ver RF06) e uma divergência força
# re-chunk completo, porque o loop incremental só olha hash de conteúdo e por isso
# nunca alcançaria arquivos inalterados cuja extração mudou. [ADR-001]
# Bump obrigatório sempre que a saída do chunker mudar — inclusive ao mexer em
# CALL_NOISE_NAMES ou em MAX_CALLS_PER_CHUNK.
CHUNKER_VERSION = "1.0.0"

# Nome do arquivo de exclusão declarativa por workspace (sintaxe .gitignore)
ATLASIGNORE_FILENAME = ".atlasignore"

# Nome do arquivo de lock entre processos para coordenar reindexações concorrentes (DECISAO-001)
REINDEX_LOCK_FILENAME = ".reindex.lock"

GRAPH_FILENAME = "graph.json"
GRAPH_HTML_FILENAME = "graph.html"
# Teto do `metrics.top_hubs` pré-computado em graph.json. NÃO limita mais a resposta
# de `hubs()`, que ordena todos os nós no query-time (ADR-005); segue alimentando
# viewer.py, que destaca os hubs no grafo visual.
GRAPH_TOP_HUBS_LIMIT = 25
GRAPH_PATH_MAX_HOPS = 10
GRAPH_VIEWER_MAX_FULL_NODES = 3000
BACKGROUND_REINDEX_MIN_INTERVAL_S = 300

# Tetos de `explain`. Sem eles a resposta cresce com o nó: o pior caso medido neste
# repositório tem 398 vizinhos (~81 KB de JSON numa única resposta MCP). O corte é
# aplicado DEPOIS da ordenação determinística, para a resposta ser estável entre
# chamadas, e o que sobrou de fora é reportado em `omitted`. [ADR-005]
GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND = 25
GRAPH_EXPLAIN_MAX_NOTES = 15

# Máximo de nomes de chamada guardados por chunk. Existe para o caso patológico —
# um único símbolo de .js minificado chega a >900 nomes distintos — e por isso o
# filtro de ruído roda ANTES do corte: builtins expulsariam chamadas de domínio.
MAX_CALLS_PER_CHUNK = 32

# Nomes descartados na extração de chamadas, antes da escada de resolução. Critério
# de admissão (as duas condições juntas): o nome é builtin, método de protocolo, de
# coleção/string ou idioma canônico de stdlib de alguma das seis linguagens AST — e,
# por isso, um símbolo homônimo no índice quase nunca é o alvo real da chamada.
#
# Sem o filtro, o degrau "único no grafo" quase sempre acha alguém: 847 de 885 nomes
# curtos distintos deste repositório (95,7%) são únicos. Casos reais que viram aresta
# errada: `encode` (portador único `EmbeddingEngine.encode`, alvo real `str.encode`),
# `exists` (`StorageBackend.exists` × `Path.exists`) e `split`/`update`/`set`/`delete`,
# cujos portadores únicos são símbolos minificados de vendor/.
#
# Custo aceito e declarado: como o filtro precede a escada, uma chamada verdadeira
# para símbolo de domínio homônimo some em TODOS os degraus. O extrator não distingue
# os dois usos (`storage.exists()` e `Path(...).exists()` deduplicam para uma entrada
# só), então deixá-los passar não recupera a aresta certa — produz uma aresta `exact`
# verdadeira por coincidência. [R-CALL-04]
#
# Comparação por casefold: `ToString`/`toString`/`tostring` são uma entrada só.
# Alterar esta lista exige bump de CHUNKER_VERSION.
CALL_NOISE_NAMES = frozenset(
    {
        # builtins e protocolo Python
        "print", "len", "str", "int", "float", "bool", "list", "dict", "set",
        "tuple", "type", "repr", "range", "enumerate", "zip", "sorted", "sum",
        "min", "max", "abs", "round", "isinstance", "issubclass", "getattr",
        "setattr", "hasattr", "super", "open", "next", "iter", "format",
        "dumps", "loads", "dump", "load",
        # coleções e strings (multi-linguagem)
        "append", "extend", "insert", "pop", "remove", "clear", "copy", "keys",
        "values", "items", "entries", "get", "add", "put", "push", "shift",
        "unshift", "sort", "reverse", "index", "indexof", "count", "size",
        "length", "contains", "has", "join", "split", "slice", "splice",
        "concat", "replace", "strip", "trim", "startswith", "endswith",
        "lower", "upper", "tolower", "toupper", "tolowercase", "touppercase",
        "isempty", "update", "delete",
        # E/S e serialização
        "read", "write", "close", "exists", "encode", "decode", "stringify",
        "tostring", "log",
        # JS/TS
        "map", "filter", "reduce", "foreach", "find", "includes", "then",
        "catch", "bind", "call", "apply", "require",
        # Go
        "make", "new", "cap", "panic", "recover", "printf", "println",
        "sprintf", "errorf", "error",
        # C#/LINQ
        "writeline", "any", "all", "select", "where", "orderby", "tolist",
        "toarray", "firstordefault", "dispose", "gethashcode",
        # Java
        "equals", "hashcode", "valueof", "getmessage", "printstacktrace",
        "getclass", "stream", "collect", "of",
    }
)

# Briefing pré-computado do projeto (atlas_brief). Todos os limites abaixo existem para
# garantir custo de token com teto fixo, independente do tamanho do repositório:
# a cardinalidade de cada lista é O(1) por construção, e o único eixo restante
# (comprimento de path) é cortado por BRIEF_MAX_PATH_CHARS.
BRIEF_FILENAME = "brief.json"
BRIEF_MAX_LAYERS = 8
BRIEF_LAYER_TOP_FILES = 3
# Acima deste número de filhos, um diretório-container (src/, packages/...) deixa de ser
# desdobrado em 2 níveis e volta a ser uma camada única, para não estourar BRIEF_MAX_LAYERS
BRIEF_LAYER_SPLIT_MAX = 12
BRIEF_MAX_ENTRYPOINTS = 6
BRIEF_MAX_HUBS = 8
BRIEF_MAX_LANGUAGES = 6
# Teto de arquivos abertos na verificação de entrypoints inferidos: mantém a detecção
# em O(1) aberturas mesmo em repositórios com milhares de candidatos
BRIEF_ENTRYPOINT_PROBE_LIMIT = 12
BRIEF_ENTRYPOINT_PROBE_MAX_BYTES = 65536
BRIEF_MAX_PATH_CHARS = 90
BRIEF_LEVEL0_MAX_CHARS = 2000
BRIEF_LEVEL1_MAX_CHARS = 5000

# Padrões de arquivos e pastas que devem ser ignorados durante a varredura
IGNORE_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".code-index",
}

# Extensões de arquivo suportadas pelo Tree-sitter para parsing AST no MVP
SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".md": "markdown",
    ".txt": "text",
    ".cs": "csharp",
    ".java": "java",
    ".jsx": "javascript",
    ".xml": "xml",
    ".razor": "razor",
    ".dart": "dart",
    ".pas": "pascal",
    ".dfm": "pascal",
    ".bas": "vb6",
    ".cls": "vb6",
    ".frm": "vb6",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".vue": "vue",
    ".scala": "scala",
    ".lua": "lua",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".ex": "elixir",
    ".exs": "elixir",
}
