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

# Ausente (default) → ranking.rerank lexical. Presente → cross-encoder ONNX.
# ATLAS_RERANK=0 tem precedência e desliga TODA reordenação, inclusive a do CE.
RERANK_MODEL_ENV_FLAG = "ATLAS_RERANK_MODEL"

# Único ~30M/ONNX listado pelo fastembed que casa com a descrição do roadmap (A1).
CROSS_ENCODER_DEFAULT_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"

# Truncar o documento limita a latência dos pares query×documento no pool.
CROSS_ENCODER_MAX_DOC_CHARS = 2000

# Spreading activation do braço estrutural (A2): faixa conservadora sobre 50 candidatos.
STRUCTURAL_SEED_TOP_N = 10
STRUCTURAL_MAX_HOPS = 2
STRUCTURAL_HOP_DECAY = 0.5
STRUCTURAL_MAX_NEIGHBORS_PER_NODE = 25
# Guarda agnóstica de repositório contra explosão por hub (DECISÃO-002).
STRUCTURAL_HUB_DEGREE_CEILING = 40

# Rótulos de arquivo genéricos usados só na expansão do braço (não no ranking de hubs).
GRAPH_NOISE_LABELS = frozenset(
    {
        "__init__.py",
        "utils.py",
        "index.ts",
        "types.ts",
        "constants.py",
    }
)

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
CURRENT_INDEX_VERSION = "2.3.0"

# Nome do arquivo de exclusão declarativa por workspace (sintaxe .gitignore)
ATLASIGNORE_FILENAME = ".atlasignore"

# Nome do arquivo de lock entre processos para coordenar reindexações concorrentes (DECISAO-001)
REINDEX_LOCK_FILENAME = ".reindex.lock"

GRAPH_FILENAME = "graph.json"
GRAPH_HTML_FILENAME = "graph.html"
GRAPH_TOP_HUBS_LIMIT = 25
GRAPH_PATH_MAX_HOPS = 10
GRAPH_VIEWER_MAX_FULL_NODES = 3000
GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND = 12
GRAPH_AFFECTED_MAX_RESULTS = 40
GRAPH_RESPONSE_MAX_CHARS = 6000
CONTEXT_RESPONSE_MAX_CHARS = 12000
# Cotas por seção do atlas_context (DECISÃO-002). Cada intent soma abaixo do teto
# para o leftover pool ter folga; o serializador aplica CONTEXT_RESPONSE_MAX_CHARS.
CONTEXT_BUDGET_BY_SECTION = {
    "symbol": 1800,
    "callers": 1600,
    "callees": 1600,
    "tests": 1200,
    "rationale": 1000,
    "call_chain_to_entrypoints": 1600,
    "error_handling": 600,
    "recent_history": 600,
    "diff": 800,
    "impact": 1600,
    "adrs": 1000,
    "layer": 800,
    "neighbors": 1600,
    "brief_layer": 1000,
}
GRAPH_NOISE_HUB_LABELS = frozenset(
    {
        "path",
        "optional",
        "logger",
        "utils",
        "json",
        "dict",
        "list",
        "str",
        "any",
    }
)
BACKGROUND_REINDEX_MIN_INTERVAL_S = 300
BACKGROUND_REINDEX_LOG_MAX_BYTES = 256 * 1024

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

# ---------------------------------------------------------------------------
# Fase 3 — "o índice reflete a realidade" (§3.1 watcher, §3.2 SCIP, §3.3 imports)
# Bloco acrescentado ao FINAL do arquivo de propósito: a F2 edita constantes acima
# e as duas fases não devem disputar as mesmas linhas.
# ---------------------------------------------------------------------------

# Watcher de workspace (§3.1) — desligado por padrão; ligar muda o comportamento
# de todo usuário existente sem opt-in.
WATCH_ENV_FLAG = "ATLAS_WATCH"
# Coalesce a rajada de eventos de um `git pull`/save do editor em um único spawn.
WATCH_DEBOUNCE_S = 2.0

# Ingestão SCIP (§3.2) — também desligada por padrão: executa binário externo.
SCIP_ENV_FLAG = "ATLAS_SCIP"
SCIP_TIMEOUT_S = 300
SCIP_INDEX_FILENAME = "index.scip"
# Teto próprio de leitura: MAX_FILE_SIZE não se aplica a artefato de toolchain.
SCIP_MAX_INDEX_BYTES = 256 * 1024 * 1024
# Binário por linguagem, na ordem em que `shutil.which` deve procurá-lo. O argv
# completo (saída, workspace) é montado por quem invoca o subprocesso (§3.2).
SCIP_INDEXERS: dict[str, list[str]] = {
    "python": ["scip-python", "index"],
    "typescript": ["scip-typescript", "index"],
    "javascript": ["scip-typescript", "index"],
    "go": ["scip-go"],
    "rust": ["rust-analyzer", "scip"],
}

# ---------------------------------------------------------------------------
# Fase 4 — camada semântica opt-in (desligada por padrão)
# ---------------------------------------------------------------------------

SEMANTIC_ENV_FLAG = "ATLAS_SEMANTIC"
SEMANTIC_LOCAL_URL_ENV = "ATLAS_SEMANTIC_LOCAL_URL"
SEMANTIC_API_URL_ENV = "ATLAS_SEMANTIC_API_URL"
SEMANTIC_API_KEY_ENV = "ATLAS_SEMANTIC_API_KEY"
SEMANTIC_MODEL_ENV = "ATLAS_SEMANTIC_MODEL"
SEMANTIC_TIMEOUT_S = 30.0
SEMANTIC_FILENAME = "semantic.json"
SEMANTIC_MAX_TEXT_CHARS = 4000
SEMANTIC_MAX_SUMMARY_CHARS = 1200

# Tier de resolução de import por linguagem (DECISÃO-005). Cada linguagem de
# SUPPORTED_EXTENSIONS aparece em EXATAMENTE um tier — `tests/test_resolution_coverage.py`
# falha quando uma extensão nova entra sem decisão de tier, que é o modo de falha
# por esquecimento que o roadmap pede para guardar.
#
# `scip` nasce vazio: nenhuma linguagem é SCIP por natureza; a fase §3.2 promove
# em tempo de execução a linguagem cuja ingestão realmente casou uma ocorrência.
# `treesitter` é exatamente o conjunto coberto por `graph._IMPORT_RESOLVERS`.
IMPORT_RESOLUTION_TIERS: dict[str, frozenset[str]] = {
    "scip": frozenset(),
    "treesitter": frozenset(
        {
            "python",
            "javascript",
            "typescript",
            "go",
            "java",
            "csharp",
            "kotlin",
            "scala",
            "rust",
            "php",
            "ruby",
            "swift",
        }
    ),
    "none": frozenset(
        {
            "markdown",
            "text",
            "xml",
            "razor",
            "dart",
            "pascal",
            "vb6",
            "c",
            "cpp",
            "sql",
            "bash",
            "yaml",
            "json",
            "toml",
            "vue",
            "lua",
            "html",
            "css",
            "scss",
            # Imports são extraídos (chunker), mas nenhum resolver mapeia
            # `MyApp.Core` -> `lib/my_app/core.ex` — declarado, não silenciado.
            "elixir",
        }
    ),
}

# ---------------------------------------------------------------------------
# Fase 5.1 — arqueologia de Git (história local bounded, ancorada por `touches`)
# ---------------------------------------------------------------------------

# Teto aprovado da janela histórica: o primeiro limite atingido prevalece.
# Configuração só pode REDUZIR estes valores (V01-V03 do TSD).
GIT_HISTORY_MAX_COMMITS_PER_FILE = 100
GIT_HISTORY_MAX_MONTHS = 24

# Artefatos locais da camada histórica. O ponteiro fica ao lado do manifest e
# nomeia a geração ativa; tabelas de staging usam o mesmo prefixo com outro id.
GIT_HISTORY_POINTER_FILENAME = "history.json"
GIT_HISTORY_TABLE_PREFIX = "commits_"
GIT_HISTORY_VERSION = "1.0"

# Teto de leitura do Git em subprocesso, no padrão de `get_git_head_sha`.
GIT_HISTORY_TIMEOUT_S = 60


def resolve_git_history_window(
    max_commits: int | None = None, max_months: int | None = None
) -> tuple[int, int]:
    """
    Janela efetiva (commits por arquivo, meses). Recusa override acima do teto
    aprovado em vez de silenciosamente ampliá-lo [V01-V03].
    """
    commits = GIT_HISTORY_MAX_COMMITS_PER_FILE if max_commits is None else int(max_commits)
    months = GIT_HISTORY_MAX_MONTHS if max_months is None else int(max_months)
    if commits < 1 or commits > GIT_HISTORY_MAX_COMMITS_PER_FILE:
        raise ValueError(
            "A janela histórica aceita de 1 a "
            f"{GIT_HISTORY_MAX_COMMITS_PER_FILE} commits por arquivo."
        )
    if months < 1 or months > GIT_HISTORY_MAX_MONTHS:
        raise ValueError(
            f"A janela histórica aceita de 1 a {GIT_HISTORY_MAX_MONTHS} meses."
        )
    return commits, months
