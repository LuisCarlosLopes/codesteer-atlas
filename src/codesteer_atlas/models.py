from typing import Dict, List, Optional

from pydantic import BaseModel, Field, PrivateAttr


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
    purpose: Optional[str] = Field(None, description="Propósito semântico opcional do símbolo")
    purpose_hash: Optional[str] = Field(None, description="Hash do conteúdo do símbolo")
    purpose_vector: Optional[List[float]] = Field(
        None, description="Embedding do propósito semântico (dimensão 384)"
    )
    references: List[str] = Field(
        default_factory=list, description="Refs de rationale persistidas no chunk"
    )

    # Hash sha256 do arquivo de origem, anexado pelo indexer e consumido por
    # `StorageBackend.store_chunks` para montar o mapa `files` do manifest.
    # Privado de propósito: é estado de indexação, não coluna persistida no LanceDB.
    _file_hash: Optional[str] = PrivateAttr(default=None)


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
    files_meta: dict[str, list] = Field(
        default_factory=dict,
        description="Mapa de path POSIX -> [mtime, size] do arquivo no momento da indexação,"
        " usado para evitar reler/hashear arquivos inalterados em workspaces grandes",
    )
    files_imports: dict[str, list] = Field(
        default_factory=dict,
        description="Mapa de path POSIX -> imports crus extraídos para o grafo",
    )
    files_declares: dict[str, str] = Field(
        default_factory=dict,
        description="Mapa de path POSIX -> namespace/package declarado no arquivo"
        " (Java, C#, Kotlin, Scala). É o outro lado da aresta em linguagens cujo"
        " namespace não é o caminho (DECISÃO-003); ausente em índices < 2.2.0",
    )


class CommitRecord(BaseModel):
    """
    Registro histórico de um commit local (F5.1). Vive na tabela histórica
    dedicada, nunca em `chunks`: identidade lógica é `(repo, id)`, com `id`
    sempre o SHA completo [ADR-009].
    """

    id: str = Field(..., description="SHA completo do commit")
    repo: str = Field(..., description="Repositório ao qual o SHA pertence")
    subject: str = Field(..., description="Assunto da mensagem")
    body: str = Field("", description="Corpo da mensagem, vazio quando ausente")
    authored_at: str = Field(..., description="Data de autoria em ISO-8601")
    committed_at: str = Field(..., description="Data de commit em ISO-8601")
    files_touched: List[str] = Field(
        default_factory=list, description="Caminhos POSIX alterados, ordenados e únicos"
    )
    is_revert: bool = Field(False, description="Marca determinística de revert")
    reverted_commit_id: Optional[str] = Field(
        None, description="SHA declarado como revertido, quando a mensagem o informa"
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
    references: List[str] = Field(default_factory=list)
    # @MindDecision: extensão aditiva para a variante histórica (F5.1); em type="commit"
    # os campos de localização ficam em sentinela e a associação vem de commit.files_touched.
    type: str = Field("code", description="Tipo do resultado: 'code' | 'commit'")
    commit: Optional[CommitRecord] = Field(
        None, description="Registro histórico quando type='commit'"
    )
    match_arms: List[str] = Field(
        default_factory=list,
        description="Braços da busca híbrida que recuperaram este chunk:"
        " 'vector' (código), 'fts' (BM25), 'semantic' (propósito) e/ou 'graph' (ativação estrutural,"
        " só quando structural=True). Um resultado presente em mais de um"
        " braço teve consenso entre eles",
    )


class SearchOutcome(BaseModel):
    """
    Resultado da busca híbrida acompanhado dos avisos de degradação.

    Existe para que uma falha de um dos braços (vetorial ou FTS) chegue ao chamador
    em vez de virar um resultado silenciosamente pior: sem isso, um índice FTS
    quebrado transforma a busca híbrida em só-vetorial sem nenhum sinal.
    """

    results: List[SearchResult] = Field(default_factory=list)
    warnings: List[str] = Field(
        default_factory=list,
        description="Códigos de degradação: 'vector_search_unavailable' |"
        " 'fts_unavailable' | 'cross_encoder_unavailable' |"
        " 'structural_arm_unavailable' | 'semantic_layer_unavailable' |"
        " 'semantic_arm_unavailable' | 'git_history_unavailable'. Os resultados estruturais"
        " continuam disponíveis"
        " quando um aviso semântico aparece.",
    )


class IndexStats(BaseModel):
    """
    Estatísticas de uma execução de indexação (completa ou incremental/parcial),
    retornadas por `index_workspace()` (DECISAO-005) e usadas tanto pelo CLI
    quanto pela tool MCP `atlas_index`.
    """

    files_processed: int = Field(..., description="Total de arquivos novos/alterados processados")
    files_failed: int = Field(
        0,
        description="Arquivos que falharam no chunking e ficaram fora do índice."
        " Valor > 0 significa índice incompleto, ainda que a execução termine sem erro",
    )
    files_scanned: int = Field(
        0, description="Total de arquivos elegíveis inspecionados durante a varredura"
    )
    files_eligible: int = Field(
        0, description="Total de arquivos elegíveis encontrados após filtros/ignores"
    )
    files_skipped_unchanged: int = Field(
        ..., description="Arquivos cujo hash não mudou e foram pulados (incremental)"
    )
    files_removed: int = Field(
        ..., description="Arquivos removidos do índice por terem sido deletados do workspace"
    )
    chunks_persisted: int = Field(..., description="Total de chunks persistidos no índice")
    chunks_generated: int = Field(
        0, description="Total de chunks gerados para arquivos novos/alterados nesta execução"
    )
    duration_s: float = Field(..., description="Duração total da indexação em segundos")
    git_head_sha: Optional[str] = Field(
        None, description="SHA do commit HEAD no momento da indexação"
    )
    phase_durations_s: Dict[str, float] = Field(
        default_factory=dict,
        description="Duração por fase da indexação (scan/hash/chunk/embed/persist/graph/brief)",
    )
    graph_strategy: Optional[str] = Field(
        None,
        description="Estratégia usada para atualizar o grafo (ex: full, incremental-code, skipped-unchanged)",
    )
    graph_nodes: int = Field(0, description="Total de nós gravados em graph.json")
    graph_edges: int = Field(0, description="Total de arestas gravadas em graph.json")
    graph_bytes: int = Field(0, description="Tamanho final de graph.json em bytes")
    graph_html_bytes: int = Field(0, description="Tamanho final de graph.html em bytes")
    # 'status' e não 'strategy': o brief é sempre reconstruído por inteiro, não há caminho incremental
    brief_status: Optional[str] = Field(
        None,
        description="Status da geração do brief (full | degraded-no-graph | failed)",
    )
    scip_status: Optional[str] = Field(
        None,
        description="Status da fase de ingestão SCIP (disabled | toolchain_missing |"
        " timeout | parse_failed | ok)",
    )
    scip_edges: int = Field(0, description="Total de arestas `calls` ingeridas via SCIP")
    brief_bytes: int = Field(0, description="Tamanho final de brief.json em bytes")
    brief_layers: int = Field(0, description="Total de camadas mantidas no brief")
    brief_entrypoints: int = Field(0, description="Total de entrypoints detectados no brief")
    semantic_status: str = Field(
        "disabled", description="Estado da geração semântica nesta execução"
    )
    semantic_generated: int = Field(0, description="Símbolos que pagaram geração semântica")
    semantic_reused: int = Field(0, description="Símbolos reutilizados pelo cache semântico")
    semantic_file_generated: int = Field(0, description="Sumários de arquivo gerados")
    semantic_file_reused: int = Field(0, description="Sumários de arquivo reutilizados")
    semantic_layer_generated: int = Field(0, description="Sumários de camada gerados")
    semantic_layer_reused: int = Field(0, description="Sumários de camada reutilizados")
    semantic_origin: Optional[str] = Field(None, description="Origem usada na geração semântica")
    semantic_egress: Optional[str] = Field(None, description="Fronteira de dados da origem")
    semantic_origins: List[str] = Field(
        default_factory=list, description="Origens efetivamente usadas na execução, incluindo sumários"
    )
    semantic_egresses: List[str] = Field(
        default_factory=list, description="Egressos efetivamente usados na execução, sem credenciais"
    )
    git_history_status: str = Field(
        "unavailable",
        description="Estado da publicação histórica (ok | partial | unavailable | empty)",
    )
    git_history_commits: int = Field(0, description="Commits no snapshot histórico ativo")
    git_history_touches: int = Field(0, description="Relações touches do snapshot ativo")
    skipped_reason: Optional[str] = Field(
        None,
        description="Motivo de a indexação ter sido pulada (ex: 'reindex_in_progress')",
    )
