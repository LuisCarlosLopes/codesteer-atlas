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

# Versão mínima de manifest aceita pelo server; manifests anteriores usam backend
# de embeddings incompatível (sentence-transformers/torch) e exigem reindexação
MIN_INDEX_VERSION = "2.0.0"

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

