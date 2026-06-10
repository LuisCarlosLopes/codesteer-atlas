from pydantic import BaseModel, Field
from typing import List, Optional


class CodeChunk(BaseModel):
    """
    Representa um fragmento de código (chunk) extraído da árvore sintática (AST).
    """

    id: str = Field(..., description="Hash único identificador do chunk")
    file_path: str = Field(..., description="Caminho relativo do arquivo no workspace")
    repo: str = Field(..., description="Nome do repositório (diretório raiz do workspace)")
    start_line: int = Field(..., description="Linha inicial do bloco no arquivo (1-indexed)")
    end_line: int = Field(..., description="Linha final do bloco no arquivo (1-indexed)")
    scope_type: str = Field(
        ..., description="Tipo do escopo: 'class' | 'function' | 'method' | 'module'"
    )
    scope_name: str = Field(
        ..., description="Nome qualificado do símbolo (ex: UserService.authenticate)"
    )
    language: str = Field(..., description="Linguagem de programação detectada")
    content: str = Field(..., description="Conteúdo textual do fragmento de código")
    indexed_at: str = Field(..., description="Timestamp ISO do momento de indexação")
    vector: Optional[List[float]] = Field(None, description="Embedding vetorial (dimensão 384)")


class IndexManifest(BaseModel):
    """
    Metadados estruturados sobre o estado atual do índice local.
    Salvo como arquivo manifest.json.
    """

    total_chunks: int = Field(..., description="Total de chunks armazenados")
    repos_indexed: List[str] = Field(..., description="Lista de repositórios presentes no índice")
    embedding_model: str = Field(..., description="Nome do modelo de embedding utilizado")
    embedding_dim: int = Field(..., description="Dimensão dos vetores de embedding")
    embedding_backend: str = Field(
        "fastembed", description="Backend de geração de embeddings utilizado (ex: fastembed)"
    )
    storage_backend: str = Field("lancedb", description="Mecanismo de persistência utilizado")
    last_indexed_at: str = Field(..., description="Timestamp ISO da última execução do indexador")
    git_head_sha: Optional[str] = Field(
        None, description="SHA do commit HEAD no momento da indexação"
    )
    languages_indexed: List[str] = Field(
        ..., description="Linguagens de programação detectadas no índice"
    )
    index_version: str = Field("2.0.0", description="Versão do formato do índice")
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Mapa de path POSIX (relativo ao workspace) -> hash sha256 do conteúdo,"
        " usado para indexação incremental",
    )


class SearchResult(BaseModel):
    """
    Representa um resultado retornado na busca híbrida.
    """

    file_path: str
    start_line: int
    end_line: int
    scope_type: str
    scope_name: str
    language: str
    content: Optional[str] = None
    score: float
    repo: str


class IndexStats(BaseModel):
    """
    Estatísticas de uma execução de indexação (completa ou incremental/parcial),
    retornadas por `index_workspace()` (DECISAO-005) e usadas tanto pelo CLI
    quanto pela tool MCP `atlas_index`.
    """

    files_processed: int = Field(..., description="Total de arquivos novos/alterados processados")
    files_skipped_unchanged: int = Field(
        ..., description="Arquivos cujo hash não mudou e foram pulados (incremental)"
    )
    files_removed: int = Field(
        ..., description="Arquivos removidos do índice por terem sido deletados do workspace"
    )
    chunks_persisted: int = Field(..., description="Total de chunks persistidos no índice")
    duration_s: float = Field(..., description="Duração total da indexação em segundos")
    git_head_sha: Optional[str] = Field(
        None, description="SHA do commit HEAD no momento da indexação"
    )
