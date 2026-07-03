import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Dict, List, Optional
import lancedb
from codesteer_atlas.config import CANDIDATES_LIMIT, DEFAULT_INDEX_DIR, MIN_INDEX_VERSION, RRF_K
from codesteer_atlas.embeddings import FASTEMBED_MODEL_NAME
from codesteer_atlas.models import CodeChunk, IndexManifest, SearchResult


def _write_manifest_atomic(manifest_path: Path, manifest: IndexManifest) -> None:
    """
    Escreve o manifest.json de forma atômica (escreve em arquivo temporário e
    usa `os.replace`, atômico tanto em POSIX quanto no Windows).

    Evita que `get_manifest()` (chamado pelo processo do servidor MCP a cada
    `atlas_search`/`atlas_status`/`atlas_map`) leia um JSON parcial enquanto o
    subprocesso de reindex em background está regravando o manifesto [GA-XX].
    """
    tmp_path = manifest_path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest.model_dump(), f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, manifest_path)


def _version_tuple(version: str) -> tuple:
    """Converte uma string de versão semântica 'x.y.z' em tupla de inteiros para comparação."""
    parts = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _table_names(db) -> List[str]:
    """Normaliza o retorno de `db.list_tables()` (lista ou ListTablesResponse) para nomes."""
    response = db.list_tables()
    tables = getattr(response, "tables", response)
    return list(tables)


class StorageBackend:
    """
    Abstração que encapsula toda a interação com o banco de dados vetorial LanceDB
    e gravação do arquivo de manifesto. Cumpre o guardrail [GA-03].
    """

    def __init__(self, index_dir: Path = DEFAULT_INDEX_DIR):
        self.index_dir = Path(index_dir)
        self.db_path = self.index_dir / "lancedb"
        self.manifest_path = self.index_dir / "manifest.json"

    def exists(self) -> bool:
        """Verifica se o índice e o banco de dados LanceDB existem."""
        return self.manifest_path.exists() and self.db_path.exists()

    def store_chunks(
        self,
        chunks: List[CodeChunk],
        git_head_sha: Optional[str] = None,
        files_meta: Optional[Dict[str, list]] = None,
    ):
        """
        Salva uma lista de chunks de código no LanceDB, gera o índice FTS
        e escreve o arquivo manifest.json (sobrescrita completa).
        """
        # Garante que a pasta do índice existe
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Conecta ao banco de dados LanceDB local
        db = lancedb.connect(str(self.db_path))

        # Prepara a lista de dicionários para inserção
        data_to_insert = [chunk.model_dump() for chunk in chunks]

        # Sobrescreve a tabela se já existir para evitar duplicações no MVP
        table_name = "chunks"
        table = db.create_table(table_name, data=data_to_insert, mode="overwrite")

        # Cria índice Full-Text Search (FTS) na coluna 'content' para buscas BM25
        table.create_fts_index("content", replace=True)

        # Coleta metadados para o manifesto
        total_chunks = len(chunks)
        repos = list(set(chunk.repo for chunk in chunks))
        languages = list(set(chunk.language for chunk in chunks))
        timestamp = datetime.now(timezone.utc).isoformat()

        # Mapa de arquivos -> hash sha256 para indexação incremental [J]
        files: Dict[str, str] = {}
        for chunk in chunks:
            file_hash = getattr(chunk, "_file_hash", None)
            if file_hash:
                files[chunk.file_path] = file_hash

        manifest = IndexManifest(
            total_chunks=total_chunks,
            repos_indexed=repos,
            embedding_model=FASTEMBED_MODEL_NAME,
            embedding_dim=384,
            embedding_backend="fastembed",
            storage_backend="lancedb",
            last_indexed_at=timestamp,
            git_head_sha=git_head_sha,
            languages_indexed=languages,
            index_version="2.0.0",
            files=files,
            files_meta=files_meta or {},
        )

        # Salva o arquivo de metadados manifest.json (escrita atômica)
        _write_manifest_atomic(self.manifest_path, manifest)

    def append_chunks(self, chunks: List[CodeChunk]) -> None:
        """
        Insere novos chunks na tabela existente (sem sobrescrever) e atualiza o
        índice FTS. Usado pela indexação incremental [J] após `delete_by_file_paths`
        ter removido as versões antigas dos arquivos alterados.
        """
        if not chunks:
            return

        self.index_dir.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(self.db_path))

        data_to_insert = [chunk.model_dump() for chunk in chunks]

        if "chunks" in _table_names(db):
            table = db.open_table("chunks")
            table.add(data_to_insert)

            # Recria o índice FTS do zero após cada append incremental.
            # Workaround para bug lance-index 7.0.0 (lance-format/lance#7313):
            # table.optimize() causa Rust panic ("index out of bounds") no
            # inverted index builder ao fazer merge de fragmentos FTS após
            # múltiplos appends incrementais. Custo extra ~1-2s por reindex.
            table.create_fts_index("content", replace=True)
        else:
            table = db.create_table("chunks", data=data_to_insert, mode="overwrite")
            table.create_fts_index("content", replace=True)

    def update_manifest_after_incremental(
        self,
        files: Dict[str, str],
        git_head_sha: Optional[str] = None,
        files_meta: Optional[Dict[str, list]] = None,
    ) -> int:
        """
        Recalcula `total_chunks`/`repos_indexed`/`languages_indexed` a partir da
        tabela atual (após inserções/remoções incrementais) e regrava o
        manifest.json com o novo mapa `files`. Retorna o `total_chunks` atualizado.
        """
        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")

        projection = table.search().select(["repo", "language"]).to_arrow()
        rows = projection.to_pylist()

        total_chunks = len(rows)
        repos = sorted({row["repo"] for row in rows})
        languages = sorted({row["language"] for row in rows})

        timestamp = datetime.now(timezone.utc).isoformat()

        manifest = IndexManifest(
            total_chunks=total_chunks,
            repos_indexed=repos,
            embedding_model=FASTEMBED_MODEL_NAME,
            embedding_dim=384,
            embedding_backend="fastembed",
            storage_backend="lancedb",
            last_indexed_at=timestamp,
            git_head_sha=git_head_sha,
            languages_indexed=languages,
            index_version="2.0.0",
            files=files,
            files_meta=files_meta or {},
        )

        _write_manifest_atomic(self.manifest_path, manifest)

        return total_chunks

    def get_manifest(self) -> IndexManifest:
        """
        Lê e retorna o manifesto do índice atual.

        Levanta `RuntimeError` acionável se o manifest for de uma versão de índice
        incompatível (< MIN_INDEX_VERSION) — cenário típico de índices gerados com
        o backend de embeddings antigo (sentence-transformers/torch).
        """
        if not self.manifest_path.exists():
            raise FileNotFoundError(
                "O arquivo manifest.json não foi encontrado. Execute a indexação primeiro."
            )

        with open(self.manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        manifest = IndexManifest(**data)

        if _version_tuple(manifest.index_version) < _version_tuple(MIN_INDEX_VERSION):
            raise RuntimeError(
                f"O índice em '{self.index_dir}' foi gerado com a versão "
                f"{manifest.index_version}, incompatível com o backend de embeddings "
                f"atual (fastembed/ONNX, índice >= {MIN_INDEX_VERSION}). "
                "Reindexe com 'atlas-index --workspace .' (ou a tool atlas_index) "
                "para gerar um índice compatível."
            )

        return manifest

    def _build_where_clause(self, filters: Dict[str, Any]) -> Optional[str]:
        """
        Constrói a cláusula SQL `where` a partir dos filtros de busca (DECISAO-003).

        - `repo`/`language`: igualdade exata (com escape de aspas simples)
        - `path_prefix`: `file_path LIKE 'prefix%'`, normalizado para POSIX e com
          escape de aspas simples e de coringas SQL (`%`/`_`)

        Retorna `None` quando não há filtros (para evitar `where()` desnecessário).
        """
        clauses = []

        if filters.get("repo"):
            repo = str(filters["repo"]).replace("'", "''")
            clauses.append(f"repo = '{repo}'")

        if filters.get("language"):
            language = str(filters["language"]).replace("'", "''")
            clauses.append(f"language = '{language}'")

        if filters.get("path_prefix"):
            # Normaliza separadores do Windows para POSIX antes do LIKE,
            # já que file_path é sempre persistido em formato POSIX [L].
            # Substitui '\\' por '/' explicitamente: PurePath(...).as_posix() não
            # converte separadores estilo Windows quando executado em macOS/Linux.
            raw_prefix = str(filters["path_prefix"]).replace("\\", "/")
            prefix = PurePath(raw_prefix).as_posix()
            # Escapa aspas simples e coringas SQL do LIKE
            prefix = prefix.replace("'", "''").replace("%", r"\%").replace("_", r"\_")
            clauses.append(f"file_path LIKE '{prefix}%' ESCAPE '\\'")

        if not clauses:
            return None

        return " AND ".join(clauses)

    def search_hybrid(
        self, query_vector: List[float], query_text: str, filters: Dict[str, Any], top_k: int
    ) -> List[SearchResult]:
        """
        Executa uma busca híbrida combinando busca vetorial (cosseno) e léxica (BM25 FTS)
        mesclando os rankings com o algoritmo RRF (Reciprocal Rank Fusion) de acordo com o [ADR-002].

        Aplica prefilter via `where()` nos dois braços para garantir que filtros
        seletivos (repo/language/path_prefix) sempre retornem `top_k` resultados
        quando existem matches suficientes (DECISAO-003).
        """
        if not self.exists():
            raise FileNotFoundError(
                "Índice não encontrado. É necessário executar o indexer.py antes de realizar buscas."
            )

        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")

        where_clause = self._build_where_clause(filters)

        # 1. Executa busca vetorial (cosseno) com prefilter
        vector_results: List[Dict[str, Any]] = []
        try:
            query = table.search(query_vector).metric("cosine")
            if where_clause:
                query = query.where(where_clause, prefilter=True)
            vector_results = query.limit(CANDIDATES_LIMIT).to_list()
        except Exception:
            # Em caso de falha silenciosa na busca vetorial, continua
            pass

        # 2. Executa busca textual (BM25 FTS) explícita, com prefilter
        text_results: List[Dict[str, Any]] = []
        try:
            query = table.search(query_text, query_type="fts")
            if where_clause:
                query = query.where(where_clause, prefilter=True)
            text_results = query.limit(CANDIDATES_LIMIT).to_list()
        except Exception:
            # Em caso de falha silenciosa na busca léxica (ex: sem índice FTS pronto), continua
            pass

        # 3. Executa a fusão dos rankings usando RRF (Reciprocal Rank Fusion)
        # score = sum(1 / (rank + k))
        rrf_scores: Dict[str, float] = {}
        items_by_id: Dict[str, Dict[str, Any]] = {}

        # Processa ranking vetorial
        for rank, item in enumerate(vector_results):
            chunk_id = item["id"]
            items_by_id[chunk_id] = item

            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + (1.0 / (rank + RRF_K))

        # Processa ranking léxico (FTS)
        for rank, item in enumerate(text_results):
            chunk_id = item["id"]
            if chunk_id not in items_by_id:
                items_by_id[chunk_id] = item

            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + (1.0 / (rank + RRF_K))

        # Ordena os ids de chunks baseados no score RRF decrescente
        sorted_chunk_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)

        # Monta a lista final dos top_k SearchResults
        final_results = []
        for chunk_id in sorted_chunk_ids[:top_k]:
            item = items_by_id[chunk_id]

            final_results.append(
                SearchResult(
                    file_path=item["file_path"],
                    start_line=item["start_line"],
                    end_line=item["end_line"],
                    scope_type=item["scope_type"],
                    scope_name=item["scope_name"],
                    language=item["language"],
                    content=item["content"],
                    score=float(rrf_scores[chunk_id]),
                    repo=item["repo"],
                )
            )

        return final_results

    def get_symbols(self) -> List[Dict[str, Any]]:
        """
        Retorna apenas as colunas necessárias para montar o mapa de arquitetura
        (file_path, scope_type, scope_name), sem a coluna `vector` e sem usar
        `to_pandas()` — projeção via Arrow para performance [F][M].
        """
        if not self.exists():
            return []

        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")

        arrow_table = (
            table.search().select(["file_path", "scope_type", "scope_name"]).to_arrow()
        )
        return arrow_table.to_pylist()

    def get_sections_by_file_path(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Retorna `scope_type`/`scope_name` de todos os chunks indexados de um
        `file_path` específico, via projeção Arrow (mesmo padrão de
        `get_symbols`). Usado para resolver `#anchor` de links markdown contra
        seções já indexadas do arquivo referenciado [F].

        Retorna lista vazia se o índice não existir ou o arquivo não estiver
        indexado.
        """
        if not self.exists():
            return []

        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")

        escaped_path = file_path.replace("'", "''")
        arrow_table = (
            table.search()
            .where(f"file_path = '{escaped_path}'", prefilter=True)
            .select(["scope_type", "scope_name"])
            .to_arrow()
        )
        return arrow_table.to_pylist()

    def delete_by_file_paths(self, file_paths: List[str]) -> None:
        """
        Remove do índice todos os chunks cujo `file_path` esteja na lista informada.
        Usado pela indexação incremental para arquivos deletados/alterados [J].
        """
        if not file_paths or not self.exists():
            return

        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")

        escaped_paths = [file_path.replace("'", "''") for file_path in file_paths]
        in_clause = ", ".join(f"'{path}'" for path in escaped_paths)
        table.delete(f"file_path IN ({in_clause})")
