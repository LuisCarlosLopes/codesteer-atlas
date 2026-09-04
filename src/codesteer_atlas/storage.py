import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, List, Optional

import lancedb
import pyarrow as pa

from codesteer_atlas.config import (
    CANDIDATES_LIMIT,
    CURRENT_INDEX_VERSION,
    DEFAULT_INDEX_DIR,
    GIT_HISTORY_POINTER_FILENAME,
    GIT_HISTORY_TABLE_PREFIX,
    GIT_HISTORY_VERSION,
    MIN_INDEX_VERSION,
    RERANK_ENV_FLAG,
    RERANK_MODEL_ENV_FLAG,
    RERANK_POOL_MULTIPLIER,
    RRF_K,
    STRUCTURAL_SEED_TOP_N,
)
from codesteer_atlas.embeddings import FASTEMBED_MODEL_NAME
from codesteer_atlas.models import (
    CodeChunk,
    CommitRecord,
    IndexManifest,
    SearchOutcome,
    SearchResult,
)
from codesteer_atlas.ranking import rerank
from codesteer_atlas.rationale import decode_references_json, encode_references_json
from codesteer_atlas.semantic import semantic_enabled, semantic_index_state
from codesteer_atlas.structural import node_id_for, spreading_activation


def _rerank_enabled() -> bool:
    """Lê o interruptor de reordenação a cada busca, para que o A/B não exija reinício."""
    return os.environ.get(RERANK_ENV_FLAG, "1") != "0"


def _write_manifest_atomic(manifest_path: Path, manifest: IndexManifest) -> None:
    """
    Escreve o manifest.json de forma atômica (escreve em arquivo temporário e
    usa `os.replace`, atômico tanto em POSIX quanto no Windows).

    Evita que `get_manifest()` (chamado pelo processo do servidor MCP a cada
    `atlas_search`/`atlas_status`/`atlas_brief`) leia um JSON parcial enquanto o
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


def _chunks_schema() -> pa.Schema:
    """Declara o schema 2.3.0, incluindo irmãos semânticos nullable."""
    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("file_path", pa.string()),
            pa.field("repo", pa.string()),
            pa.field("start_line", pa.int64()),
            pa.field("end_line", pa.int64()),
            pa.field("scope_type", pa.string()),
            pa.field("scope_name", pa.string()),
            pa.field("language", pa.string()),
            pa.field("content", pa.string()),
            pa.field("indexed_at", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), 384)),
            pa.field("purpose", pa.string()),
            pa.field("purpose_hash", pa.string()),
            pa.field("purpose_vector", pa.list_(pa.float32(), 384)),
            pa.field("references_json", pa.string()),
        ]
    )


def _assert_chunks_schema(table) -> None:
    expected = _chunks_schema()
    actual = table.schema
    problems = []
    if actual.names != expected.names:
        problems.append(f"colunas={actual.names!r}")
    for expected_field in expected:
        try:
            actual_field = actual.field(expected_field.name)
        except KeyError:
            continue
        if actual_field.type != expected_field.type or (
            expected_field.name in {"purpose", "purpose_hash", "purpose_vector"}
            and not actual_field.nullable
        ):
            problems.append(
                f"{expected_field.name}={actual_field.type}/{actual_field.nullable}"
            )
    if problems:
        raise RuntimeError(
            "O schema da tabela chunks não é compatível com o índice 2.3.0 "
            f"({'; '.join(problems)}). Nenhuma mutação incremental foi executada; "
            "use full=true sem paths para reindexar integralmente."
        )



def _history_schema() -> pa.Schema:
    """
    Schema da tabela histórica (F5.1). É uma tabela dedicada: `chunks` não ganha
    coluna nenhuma e nenhum commit vira `CodeChunk` [GA-01].

    `touches_json` guarda os pares (arquivo, símbolo) atribuídos ao commit; o
    intervalo de linhas NÃO é persistido aqui porque a localização pública é o
    intervalo ATUAL do símbolo, resolvido na projeção do grafo.
    """
    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("repo", pa.string()),
            pa.field("subject", pa.string()),
            pa.field("body", pa.string()),
            pa.field("message", pa.string()),
            pa.field("authored_at", pa.string()),
            pa.field("committed_at", pa.string()),
            pa.field("files_json", pa.string()),
            pa.field("touches_json", pa.string()),
            pa.field("is_revert", pa.bool_()),
            pa.field("reverted_commit_id", pa.string()),
            pa.field("stale", pa.bool_()),
            pa.field("vector", pa.list_(pa.float32(), 384)),
        ]
    )


def _commit_row(record: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza um registro de commit para a linha persistida."""
    files = sorted({str(path) for path in record.get("files_touched") or []})
    touches = sorted(
        {
            (str(item["file_path"]), str(item["scope_name"]))
            for item in record.get("touches") or []
        }
    )
    subject = str(record.get("subject") or "")
    body = str(record.get("body") or "")
    return {
        "id": str(record["id"]),
        "repo": str(record["repo"]),
        "subject": subject,
        "body": body,
        "message": f"{subject}\n{body}".strip(),
        "authored_at": str(record.get("authored_at") or ""),
        "committed_at": str(record.get("committed_at") or ""),
        "files_json": json.dumps(files, ensure_ascii=False),
        "touches_json": json.dumps(
            [{"file_path": path, "scope_name": scope} for path, scope in touches],
            ensure_ascii=False,
        ),
        "is_revert": bool(record.get("is_revert")),
        "reverted_commit_id": record.get("reverted_commit_id"),
        "stale": bool(record.get("stale")),
        "vector": list(record.get("vector") or [0.0] * 384),
    }


def _commit_from_row(row: Dict[str, Any]) -> CommitRecord:
    return CommitRecord(
        id=row["id"],
        repo=row["repo"],
        subject=row.get("subject") or "",
        body=row.get("body") or "",
        authored_at=row.get("authored_at") or "",
        committed_at=row.get("committed_at") or "",
        files_touched=json.loads(row.get("files_json") or "[]"),
        is_revert=bool(row.get("is_revert")),
        reverted_commit_id=row.get("reverted_commit_id") or None,
    )



def _without_history(graph: dict, node_kind: str, edge_kind: str) -> dict:
    """
    Cópia do grafo sem a camada histórica, para a expansão estrutural.

    @MindRisk: sem isto um commit vira ponte entre símbolos sem relação de código
    e a ativação estrutural passa a ranquear por coincidência de história [GA-05].
    """
    nodes = [node for node in graph.get("nodes", []) if node.get("kind") != node_kind]
    edges = [edge for edge in graph.get("edges", []) if edge.get("kind") != edge_kind]
    filtered = {"nodes": nodes, "edges": edges}
    filtered["_nodes_by_id"] = {node["id"]: node for node in nodes}
    adjacency: Dict[str, List] = {}
    for edge in edges:
        adjacency.setdefault(edge["source"], []).append((edge["target"], edge["kind"]))
        adjacency.setdefault(edge["target"], []).append((edge["source"], edge["kind"]))
    filtered["_adjacency"] = adjacency
    return filtered



class StorageBackend:
    """
    Abstração que encapsula toda a interação com o banco de dados vetorial LanceDB
    e gravação do arquivo de manifesto. Cumpre o guardrail [GA-03].
    """

    def __init__(self, index_dir: Path = DEFAULT_INDEX_DIR):
        self.index_dir = Path(index_dir)
        self.db_path = self.index_dir / "lancedb"
        self.manifest_path = self.index_dir / "manifest.json"
        self.history_pointer_path = self.index_dir / GIT_HISTORY_POINTER_FILENAME

    def _chunk_to_row(self, chunk: CodeChunk) -> Dict[str, Any]:
        row = chunk.model_dump()
        row["references_json"] = encode_references_json(chunk.references)
        row.pop("references", None)
        return row

    def _assert_incremental_current(self) -> None:
        if not self.manifest_path.exists():
            return
        manifest = self.get_manifest()
        if _version_tuple(manifest.index_version) < _version_tuple(CURRENT_INDEX_VERSION):
            raise RuntimeError(
                f"O índice legado {manifest.index_version} não aceita atualização incremental. "
                "Use full=true sem paths para reindexar e rechunkear integralmente em 2.3.0."
            )

    def validate_incremental_schema(self) -> None:
        """Valida o schema atual antes de qualquer delete/append incremental."""
        self._assert_incremental_current()
        if not self.exists():
            return
        db = lancedb.connect(str(self.db_path))
        if "chunks" not in _table_names(db):
            raise RuntimeError(
                "A tabela chunks não existe para o manifesto atual; "
                "use full=true sem paths para reconstruir o índice."
            )
        _assert_chunks_schema(db.open_table("chunks"))


    def exists(self) -> bool:
        """Verifica se o índice e o banco de dados LanceDB existem."""
        return self.manifest_path.exists() and self.db_path.exists()

    def store_chunks(
        self,
        chunks: List[CodeChunk],
        git_head_sha: Optional[str] = None,
        files_meta: Optional[Dict[str, list]] = None,
        files_declares: Optional[Dict[str, str]] = None,
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
        data_to_insert = pa.Table.from_pylist(
            [self._chunk_to_row(chunk) for chunk in chunks], schema=_chunks_schema()
        )

        # Sobrescreve a tabela se já existir para evitar duplicações no MVP
        table_name = "chunks"
        table = db.create_table(
            table_name, data=data_to_insert, schema=_chunks_schema(), mode="overwrite"
        )

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
            index_version=CURRENT_INDEX_VERSION,
            files=files,
            files_meta=files_meta or {},
            files_declares=files_declares or {},
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

        self._assert_incremental_current()

        self.index_dir.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(self.db_path))

        data_to_insert = [self._chunk_to_row(chunk) for chunk in chunks]

        if "chunks" in _table_names(db):
            table = db.open_table("chunks")
            _assert_chunks_schema(table)
            table.add(data_to_insert)

            # Recria o índice FTS do zero após cada append incremental.
            # Workaround para bug lance-index 7.0.0 (lance-format/lance#7313):
            # table.optimize() causa Rust panic ("index out of bounds") no
            # inverted index builder ao fazer merge de fragmentos FTS após
            # múltiplos appends incrementais. Custo extra ~1-2s por reindex.
            table.create_fts_index("content", replace=True)
        else:
            table = db.create_table(
                "chunks",
                data=pa.Table.from_pylist(data_to_insert, schema=_chunks_schema()),
                schema=_chunks_schema(),
                mode="overwrite",
            )
            table.create_fts_index("content", replace=True)

    def update_manifest_after_incremental(
        self,
        files: Dict[str, str],
        git_head_sha: Optional[str] = None,
        files_meta: Optional[Dict[str, list]] = None,
        files_imports: Optional[Dict[str, list]] = None,
        files_declares: Optional[Dict[str, str]] = None,
    ) -> int:
        """
        Recalcula `total_chunks`/`repos_indexed`/`languages_indexed` a partir da
        tabela atual (após inserções/remoções incrementais) e regrava o
        manifest.json com o novo mapa `files`. Retorna o `total_chunks` atualizado.
        """
        self._assert_incremental_current()
        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")
        _assert_chunks_schema(table)

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
            index_version=CURRENT_INDEX_VERSION,
            files=files,
            files_meta=files_meta or {},
            files_imports=files_imports or {},
            files_declares=files_declares or {},
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
        self,
        query_vector: List[float],
        query_text: str,
        filters: Dict[str, Any],
        top_k: int,
        *,
        structural: bool = False,
    ) -> SearchOutcome:
        """
        Executa uma busca híbrida combinando busca vetorial (cosseno) e léxica (BM25 FTS)
        mesclando os rankings com o algoritmo RRF (Reciprocal Rank Fusion) de acordo com o [ADR-002].

        Aplica prefilter via `where()` nos dois braços para garantir que filtros
        seletivos (repo/language/path_prefix) sempre retornem `top_k` resultados
        quando existem matches suficientes (DECISAO-003).

        A reordenação pós-RRF roda sobre um pool maior que `top_k`, e a regra veio de
        medição no golden set, não de intuição. Veja CLAUDE.md (DECISAO-007).

        `structural=True` acrescenta o braço de grafo à fusão (opt-in por chamada).
        Sem `ATLAS_RERANK_MODEL`, a reordenação permanece a lexical de `ranking.rerank`.

        A falha de um braço isolado degrada a busca em vez de derrubá-la, mas é
        reportada em `SearchOutcome.warnings` — degradação silenciosa aqui significa
        resultado pior sem nenhum sinal para o chamador. Se os DOIS braços falharem,
        levanta `RuntimeError`: devolver lista vazia seria indistinguível de
        "nenhum resultado encontrado".
        """
        if not self.exists():
            raise FileNotFoundError(
                "Índice não encontrado. É necessário executar o indexer.py antes de realizar buscas."
            )

        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")

        where_clause = self._build_where_clause(filters)

        warnings: List[str] = []
        vector_error: Optional[Exception] = None
        fts_error: Optional[Exception] = None

        # 1. Executa busca vetorial (cosseno) com prefilter
        vector_results: List[Dict[str, Any]] = []
        try:
            query = table.search(query_vector, vector_column_name="vector").metric("cosine")
            if where_clause:
                query = query.where(where_clause, prefilter=True)
            vector_results = query.limit(CANDIDATES_LIMIT).to_list()
        except Exception as e:
            vector_error = e
            warnings.append("vector_search_unavailable")
            print(
                f"[atlas] Braço vetorial indisponível ({type(e).__name__}: {e}); "
                "busca degradada para somente BM25.",
                file=sys.stderr,
            )

        # 2. Executa busca textual (BM25 FTS) explícita, com prefilter
        text_results: List[Dict[str, Any]] = []
        try:
            query = table.search(query_text, query_type="fts")
            if where_clause:
                query = query.where(where_clause, prefilter=True)
            text_results = query.limit(CANDIDATES_LIMIT).to_list()
        except Exception as e:
            fts_error = e
            warnings.append("fts_unavailable")
            print(
                f"[atlas] Braço FTS indisponível ({type(e).__name__}: {e}); "
                "busca degradada para somente vetorial.",
                file=sys.stderr,
            )


        if vector_error is not None and fts_error is not None:
            raise RuntimeError(
                "Os dois braços da busca híbrida falharam, o índice provavelmente está "
                f"corrompido ou incompleto. Vetorial: {type(vector_error).__name__}: "
                f"{vector_error}. FTS: {type(fts_error).__name__}: {fts_error}. "
                "Reindexe com 'atlas-index --workspace . --full' (ou a tool atlas_index "
                "com full=true)."
            )

        # 3. Executa a fusão dos rankings usando RRF (Reciprocal Rank Fusion)
        # score = sum(1 / (rank + k))
        rrf_scores: Dict[str, float] = {}
        items_by_id: Dict[str, Dict[str, Any]] = {}
        # Qual braço recuperou cada chunk. Vira `SearchResult.match_arms` para que o
        # chamador saiba se o acerto teve consenso entre braços ou veio de um só.
        arms_by_id: Dict[str, List[str]] = {}

        def _fuse(results: List[Dict[str, Any]], arm: str) -> None:
            for rank, item in enumerate(results):
                chunk_id = item["id"]
                if chunk_id not in items_by_id:
                    items_by_id[chunk_id] = item
                arms_by_id.setdefault(chunk_id, []).append(arm)
                rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + (1.0 / (rank + RRF_K))

        _fuse(vector_results, "vector")
        _fuse(text_results, "fts")

        # @MindDecision: o vetor de propósito é um braço independente; não altera
        # `content`/`vector` e só é consultado em índice 2.3.0 pronto.
        if semantic_enabled():
            manifest = self.get_manifest()
            semantic_index, _reason = semantic_index_state(self.index_dir, manifest)
            if semantic_index == "ready":
                try:
                    query = table.search(query_vector, vector_column_name="purpose_vector").metric("cosine")
                    if where_clause:
                        query = query.where(where_clause, prefilter=True)
                    semantic_results = query.limit(CANDIDATES_LIMIT).to_list()
                    _fuse(semantic_results, "semantic")
                except Exception as error:
                    warnings.append("semantic_arm_unavailable")
                    print(
                        f"[atlas] Braço semântico indisponível ({type(error).__name__}).",
                        file=sys.stderr,
                    )
            else:
                warnings.append("semantic_layer_unavailable")

        if structural:
            self._fuse_structural_arm(rrf_scores, items_by_id, _fuse, warnings)

        # Ordena os ids de chunks baseados no score RRF decrescente
        sorted_chunk_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)

        # Reordena um pool maior que `top_k` antes do corte: o ganho da reordenação
        # vem justamente de promover um acerto que o RRF deixou logo abaixo da linha.
        pool_size = (
            min(top_k * RERANK_POOL_MULTIPLIER, CANDIDATES_LIMIT)
            if _rerank_enabled()
            else top_k
        )

        pool = []
        for chunk_id in sorted_chunk_ids[:pool_size]:
            item = items_by_id[chunk_id]

            pool.append(
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
                    references=decode_references_json(item.get("references_json")),
                    match_arms=arms_by_id.get(chunk_id, []),
                )
            )

        if _rerank_enabled():
            pool = self._rerank_pool(pool, query_text, warnings)

        history = self._search_history_arm(query_vector, query_text, filters, top_k, warnings)
        return SearchOutcome(results=self._merge_typed(pool, history, top_k), warnings=warnings)

    def _fuse_structural_arm(
        self,
        rrf_scores: Dict[str, float],
        items_by_id: Dict[str, Dict[str, Any]],
        fuse: Callable[[List[Dict[str, Any]], str], None],
        warnings: List[str],
    ) -> None:
        # @MindFlow: load_graph → sementes RRF → spreading_activation → _fuse("graph")
        # @MindRisk: junção chunk↔nó só em memória; chunk fora do pool nunca é materializado
        """Acrescenta o braço `graph` à fusão; grafo ausente vira aviso e no-op."""
        try:
            from codesteer_atlas.graph import HISTORY_EDGE_KIND, HISTORY_NODE_KIND, load_graph

            graph = _without_history(load_graph(self.index_dir), HISTORY_NODE_KIND, HISTORY_EDGE_KIND)
        except Exception as e:
            warnings.append("structural_arm_unavailable")
            print(
                f"[atlas] Braço estrutural indisponível ({type(e).__name__}: {e}); "
                "busca segue sem o braço.",
                file=sys.stderr,
            )
            return

        chunk_id_by_node: Dict[str, str] = {}
        for chunk_id, item in items_by_id.items():
            chunk_id_by_node[node_id_for(item)] = chunk_id

        sorted_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)
        seed_node_ids = [
            node_id_for(items_by_id[chunk_id])
            for chunk_id in sorted_ids[:STRUCTURAL_SEED_TOP_N]
        ]
        ranked_node_ids = spreading_activation(
            graph, seed_node_ids, chunk_id_by_node.keys()
        )

        graph_results: List[Dict[str, Any]] = []
        for node_id in ranked_node_ids:
            chunk_id = chunk_id_by_node.get(node_id)
            if chunk_id is None:
                continue
            graph_results.append(items_by_id[chunk_id])

        fuse(graph_results, "graph")

    def _rerank_pool(
        self, pool: List[SearchResult], query_text: str, warnings: List[str]
    ) -> List[SearchResult]:
        """Escolhe lexical vs cross-encoder; falha do CE cai no lexical com aviso."""
        model_name = os.environ.get(RERANK_MODEL_ENV_FLAG)
        if not model_name:
            return rerank(pool, query_text)

        try:
            from codesteer_atlas.reranker import CrossEncoderReranker

            return CrossEncoderReranker().rerank(query_text, pool)
        except Exception as e:
            warnings.append("cross_encoder_unavailable")
            print(
                f"[atlas] Cross-encoder indisponível ({type(e).__name__}: {e}); "
                "reordenação degradada para ranking lexical.",
                file=sys.stderr,
            )
            return rerank(pool, query_text)

    def get_symbols(self) -> List[Dict[str, Any]]:
        """
        Projeção enxuta de `file_path`/`scope_type`/`scope_name`, sem a coluna `vector`
        e sem `to_pandas()` — via Arrow, por performance [F][M].

        Sem chamador em produção desde a remoção da tool `atlas_map`; permanece como
        utilitário de inspeção do índice (usado pela suíte de testes).
        """
        if not self.exists():
            return []

        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")

        arrow_table = (
            table.search().select(["file_path", "scope_type", "scope_name"]).to_arrow()
        )
        return arrow_table.to_pylist()

    def get_semantic_cache(self, file_paths: Optional[List[str]] = None) -> Dict[tuple, Dict[str, Any]]:
        """Lê a projeção semântica antes de um delete para permitir reuso."""
        if not self.exists():
            return {}
        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")
        columns = ["file_path", "scope_name", "scope_type", "content", "purpose", "purpose_hash", "purpose_vector"]
        query = table.search()
        if file_paths:
            escaped = [path.replace("'", "''") for path in file_paths]
            query = query.where(
                f"file_path IN ({', '.join(repr(path) for path in escaped)})", prefilter=True
            )
        try:
            rows = query.select(columns).to_arrow().to_pylist()
        except Exception:
            fallback = ["file_path", "scope_name", "scope_type", "content"]
            rows = query.select(fallback).to_arrow().to_pylist()
        result: Dict[tuple, Dict[str, Any]] = {}
        from codesteer_atlas.semantic import content_hash

        for row in rows:
            if not row.get("purpose"):
                continue
            key = (row.get("file_path", ""), row.get("scope_name", ""), content_hash(row.get("content", "")))
            result[key] = row
        return result

    def get_semantic_projection(self) -> List[Dict[str, Any]]:
        if not self.exists():
            return []
        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")
        columns = ["file_path", "scope_name", "scope_type", "content", "purpose", "purpose_hash"]
        try:
            return table.search().select(columns).to_arrow().to_pylist()
        except Exception:
            return table.search().select(["file_path", "scope_name", "scope_type", "content"]).to_arrow().to_pylist()

    def lookup_purpose(self, file_path: str, scope_name: str) -> Optional[str]:
        """Faz um point lookup do propósito semântico, sem carregar vetores."""
        if not self.exists():
            return None
        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")
        path = file_path.replace("'", "''")
        scope = scope_name.replace("'", "''")
        try:
            rows = table.search().where(
                f"file_path = '{path}' AND scope_name = '{scope}'", prefilter=True
            ).select(["purpose"]).limit(1).to_arrow().to_pylist()
        except Exception:
            return None
        return rows[0].get("purpose") if rows else None

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

    def get_graph_projection(self) -> List[Dict[str, Any]]:
        """
        Retorna a projeção mínima necessária para reconstruir `graph.json`,
        sempre sem a coluna `vector`.

        Para reduzir uso de memória em workspaces grandes, só carrega `content`
        dos chunks Markdown, já que chunks de código usam apenas refs de
        rationale/imports no rebuild do grafo.
        """
        if not self.exists():
            return []

        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")

        base_columns = [
            "file_path",
            "scope_type",
            "scope_name",
            "language",
            "start_line",
            "end_line",
            "references_json",
        ]

        code_rows: List[Dict[str, Any]] = []
        markdown_rows: List[Dict[str, Any]] = []

        try:
            code_arrow = (
                table.search()
                .where("language != 'markdown'", prefilter=True)
                .select(base_columns)
                .to_arrow()
            )
            code_rows = code_arrow.to_pylist()
            for row in code_rows:
                row["content"] = None

            markdown_arrow = (
                table.search()
                .where("language = 'markdown'", prefilter=True)
                .select([*base_columns[:-1], "content", base_columns[-1]])
                .to_arrow()
            )
            markdown_rows = markdown_arrow.to_pylist()
            return code_rows + markdown_rows
        except Exception:
            code_arrow = (
                table.search()
                .where("language != 'markdown'", prefilter=True)
                .select(base_columns[:-1])
                .to_arrow()
            )
            code_rows = code_arrow.to_pylist()
            for row in code_rows:
                row["content"] = None
                row["references_json"] = "[]"

            markdown_arrow = (
                table.search()
                .where("language = 'markdown'", prefilter=True)
                .select([*base_columns[:-1], "content"])
                .to_arrow()
            )
            markdown_rows = markdown_arrow.to_pylist()
            for row in markdown_rows:
                row["references_json"] = "[]"
            return code_rows + markdown_rows

    def get_graph_projection_for_file_paths(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """
        Retorna a mesma projeção de `get_graph_projection`, mas restrita a um
        conjunto de `file_path`s. Usado no update incremental do grafo para
        evitar releitura do índice inteiro.
        """
        if not file_paths or not self.exists():
            return []

        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")

        escaped_paths = [file_path.replace("'", "''") for file_path in file_paths]
        in_clause = ", ".join(f"'{path}'" for path in escaped_paths)
        where_clause = f"file_path IN ({in_clause})"
        base_columns = [
            "file_path",
            "scope_type",
            "scope_name",
            "language",
            "start_line",
            "end_line",
            "references_json",
        ]

        try:
            arrow_table = table.search().where(where_clause, prefilter=True).select(
                [*base_columns[:-1], "content", base_columns[-1]]
            ).to_arrow()
            rows = arrow_table.to_pylist()
        except Exception:
            arrow_table = (
                table.search().where(where_clause, prefilter=True).select([*base_columns[:-1], "content"]).to_arrow()
            )
            rows = arrow_table.to_pylist()
            for row in rows:
                row["references_json"] = "[]"

        for row in rows:
            if row["language"] != "markdown":
                row["content"] = None
        return rows

    def delete_by_file_paths(self, file_paths: List[str]) -> None:
        """
        Remove do índice todos os chunks cujo `file_path` esteja na lista informada.
        Usado pela indexação incremental para arquivos deletados/alterados [J].
        """
        if not file_paths or not self.exists():
            return

        self._assert_incremental_current()

        db = lancedb.connect(str(self.db_path))
        table = db.open_table("chunks")
        _assert_chunks_schema(table)

        escaped_paths = [file_path.replace("'", "''") for file_path in file_paths]
        in_clause = ", ".join(f"'{path}'" for path in escaped_paths)
        table.delete(f"file_path IN ({in_clause})")

    # ------------------------------------------------------------------
    # Camada histórica (F5.1) — tabela dedicada + ponteiro de snapshot ativo
    # @MindContext: história de Git bounded, publicada por snapshot [ADR-009/010]
    # @MindRisk: apagar o snapshot ativo antes da publicação perderia a única
    # evidência conhecida; por isso staging → ponteiro → GC, nessa ordem [GA-08]
    # @MindTest: tests/test_storage.py
    # ------------------------------------------------------------------

    def _connect(self):
        self.index_dir.mkdir(parents=True, exist_ok=True)
        return lancedb.connect(str(self.db_path))

    def read_history_pointer(self) -> Optional[Dict[str, Any]]:
        """Ponteiro da geração ativa; `None` quando ausente ou incompatível."""
        if not self.history_pointer_path.exists():
            return None
        try:
            with open(self.history_pointer_path, "r", encoding="utf-8") as f:
                pointer = json.load(f)
        except Exception:
            return None
        if not isinstance(pointer, dict):
            return None
        if str(pointer.get("history_version")) != GIT_HISTORY_VERSION:
            # Incompatibilidade histórica degrada só esta camada [GA-010-08/09]
            return None
        return pointer

    def _write_history_pointer(self, pointer: Dict[str, Any]) -> None:
        tmp_path = self.history_pointer_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(pointer, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.history_pointer_path)

    def _active_history_table(self):
        """Tabela do snapshot ativo, ou `None` quando não há visão publicada."""
        pointer = self.read_history_pointer()
        if not pointer or not pointer.get("table"):
            return None
        if not self.db_path.exists():
            return None
        db = lancedb.connect(str(self.db_path))
        if pointer["table"] not in _table_names(db):
            return None
        return db.open_table(pointer["table"])

    def stage_history(self, records: List[Dict[str, Any]]) -> str:
        """
        Materializa a visão candidata em uma geração NÃO ativa e devolve seu id.

        Nada aqui é observável: sem publicação do ponteiro, o consumidor continua
        vendo o snapshot anterior [GA-010-03].
        """
        snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        table_name = f"{GIT_HISTORY_TABLE_PREFIX}{snapshot_id}"

        deduped: Dict[tuple, Dict[str, Any]] = {}
        for record in records:
            row = _commit_row(record)
            deduped.setdefault((row["repo"], row["id"]), row)
        rows = [deduped[key] for key in sorted(deduped)]

        db = self._connect()
        table = db.create_table(
            table_name,
            data=pa.Table.from_pylist(rows, schema=_history_schema()),
            schema=_history_schema(),
            mode="overwrite",
        )
        if rows:
            table.create_fts_index("message", replace=True)
        return snapshot_id

    def publish_history(self, snapshot_id: str, state: str = "ok") -> Dict[str, Any]:
        """Torna a geração `snapshot_id` a ativa. Só é chamada com o grafo pronto."""
        db = self._connect()
        table_name = f"{GIT_HISTORY_TABLE_PREFIX}{snapshot_id}"
        table = db.open_table(table_name)
        rows = table.search().select(["touches_json"]).to_arrow().to_pylist()
        touches = sum(len(json.loads(row.get("touches_json") or "[]")) for row in rows)
        pointer = {
            "history_version": GIT_HISTORY_VERSION,
            "snapshot_id": snapshot_id,
            "table": table_name,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "state": state,
            "commits": len(rows),
            "touches": touches,
        }
        self._write_history_pointer(pointer)
        return pointer

    def mark_history_state(self, state: str) -> Optional[Dict[str, Any]]:
        """
        Declara falha da atualização sem tocar no snapshot ativo: o conjunto
        anterior continua servível e passa a ser apresentado como stale [GA-010-05].
        """
        pointer = self.read_history_pointer()
        if pointer is None:
            return None
        pointer = {**pointer, "state": state}
        self._write_history_pointer(pointer)
        return pointer

    def gc_history(self) -> int:
        """Remove gerações não ativas. Roda DEPOIS da publicação e nunca apaga o ativo."""
        if not self.db_path.exists():
            return 0
        pointer = self.read_history_pointer()
        active = pointer.get("table") if pointer else None
        db = lancedb.connect(str(self.db_path))
        removed = 0
        for name in _table_names(db):
            if not name.startswith(GIT_HISTORY_TABLE_PREFIX) or name == active:
                continue
            try:
                db.drop_table(name)
                removed += 1
            except Exception as error:
                print(
                    f"[atlas] Falha ao remover geração histórica {name}: {error}",
                    file=sys.stderr,
                )
        return removed

    def get_history_state(self) -> Dict[str, Any]:
        """
        Estado observável da camada: `absent` | `ok` | `partial` | `unavailable`.
        `absent` significa que não há conjunto anterior algum para servir.
        """
        pointer = self.read_history_pointer()
        if pointer is None:
            return {"state": "absent", "commits": 0, "touches": 0}
        try:
            table = self._active_history_table()
        except Exception:
            table = None
        if table is None:
            return {"state": "absent", "commits": 0, "touches": 0}
        return {
            "state": str(pointer.get("state") or "ok"),
            "snapshot_id": pointer.get("snapshot_id"),
            "commits": int(pointer.get("commits") or 0),
            "touches": int(pointer.get("touches") or 0),
        }

    def get_history_projection(self) -> List[Dict[str, Any]]:
        """Projeção do snapshot ativo para a derivação de nós/arestas do grafo."""
        try:
            table = self._active_history_table()
        except Exception as error:
            print(f"[atlas] Camada histórica ilegível: {error}", file=sys.stderr)
            return []
        if table is None:
            return []
        columns = ["id", "repo", "subject", "committed_at", "touches_json", "stale"]
        try:
            rows = table.search().select(columns).to_arrow().to_pylist()
        except Exception as error:
            print(f"[atlas] Projeção histórica indisponível: {error}", file=sys.stderr)
            return []
        return sorted(rows, key=lambda row: (row["repo"], row["id"]))

    def lookup_commits(self, keys: List[tuple]) -> List[tuple]:
        """
        Point lookup por `(repo, SHA completo)` no snapshot ativo, devolvendo
        `(CommitRecord, stale)` — nunca varredura integral, nunca leitura de Git [GA-02].
        """
        if not keys:
            return []
        try:
            table = self._active_history_table()
        except Exception:
            table = None
        if table is None:
            return []
        ids = sorted({str(sha).replace("'", "''") for _repo, sha in keys})
        in_clause = ", ".join(f"'{sha}'" for sha in ids)
        try:
            rows = (
                table.search()
                .where(f"id IN ({in_clause})", prefilter=True)
                .select(
                    [
                        "id",
                        "repo",
                        "subject",
                        "body",
                        "authored_at",
                        "committed_at",
                        "files_json",
                        "is_revert",
                        "reverted_commit_id",
                        "stale",
                    ]
                )
                .to_arrow()
                .to_pylist()
            )
        except Exception as error:
            print(f"[atlas] Lookup histórico indisponível: {error}", file=sys.stderr)
            return []
        wanted = {(str(repo), str(sha)) for repo, sha in keys}
        found = []
        for row in rows:
            if (row["repo"], row["id"]) not in wanted:
                continue
            record = _commit_from_row(row)
            found.append((record, bool(row.get("stale"))))
        return found

    def _search_history_arm(
        self,
        query_vector: List[float],
        query_text: str,
        filters: Dict[str, Any],
        top_k: int,
        warnings: List[str],
    ) -> List[SearchResult]:
        """
        Sub-recuperação histórica tipada (F5.1): RRF próprio sobre a mensagem do
        commit, com a mesma constante de fusão do pipeline de código.

        @MindDecision: pipeline separado para o commit não virar semente nem vizinho
        do braço `graph` — ele nunca ganha `match_arms=["graph"]` [ADR-009].
        """
        if filters.get("language") and filters["language"] != "git":
            return []

        try:
            table = self._active_history_table()
        except Exception as error:
            print(f"[atlas] Camada histórica ilegível ({type(error).__name__}).", file=sys.stderr)
            table = None
        if table is None:
            warnings.append("git_history_unavailable")
            return []

        where_clause = None
        if filters.get("repo"):
            repo = str(filters["repo"]).replace("'", "''")
            where_clause = f"repo = '{repo}'"

        def _run(query) -> List[Dict[str, Any]]:
            if where_clause:
                query = query.where(where_clause, prefilter=True)
            return query.limit(CANDIDATES_LIMIT).to_list()

        vector_rows: List[Dict[str, Any]] = []
        text_rows: List[Dict[str, Any]] = []
        failures = 0
        try:
            vector_rows = _run(table.search(query_vector, vector_column_name="vector").metric("cosine"))
        except Exception:
            failures += 1
        try:
            text_rows = _run(table.search(query_text, query_type="fts"))
        except Exception:
            failures += 1

        if failures == 2:
            warnings.append("git_history_unavailable")
            return []

        scores: Dict[str, float] = {}
        rows_by_key: Dict[str, Dict[str, Any]] = {}
        arms: Dict[str, List[str]] = {}
        for rows, arm in ((vector_rows, "vector"), (text_rows, "fts")):
            for rank, row in enumerate(rows):
                key = f"{row['repo']}:{row['id']}"
                rows_by_key.setdefault(key, row)
                arms.setdefault(key, []).append(arm)
                scores[key] = scores.get(key, 0.0) + (1.0 / (rank + RRF_K))

        prefix = None
        if filters.get("path_prefix"):
            prefix = PurePath(str(filters["path_prefix"]).replace("\\", "/")).as_posix()

        results: List[SearchResult] = []
        # Filtrar ANTES de cortar em `top_k`: `files_json` só existe na linha já
        # materializada, então o prefixo não vira `where()` como no braço de código
        # (DECISAO-003) — mas a semântica de `top_k` ("os N melhores ENTRE os que
        # casam") exige a mesma ordem de aplicação.
        for key in sorted(scores, key=lambda item: (-scores[item], item)):
            if len(results) >= top_k:
                break
            row = rows_by_key[key]
            record = _commit_from_row(row)
            # Um commit casa `path_prefix` quando ao menos um arquivo tocado está sob ele
            if prefix and not any(path.startswith(prefix) for path in record.files_touched):
                continue
            results.append(
                SearchResult(
                    # Sentinelas: um commit não tem localização de símbolo; a associação
                    # a arquivos é `commit.files_touched` [A4 do IPD]
                    file_path="",
                    start_line=0,
                    end_line=0,
                    scope_type="",
                    scope_name="",
                    language="git",
                    content=row.get("message"),
                    score=float(scores[key]),
                    repo=record.repo,
                    type="commit",
                    commit=record,
                    match_arms=arms.get(key, []),
                )
            )
        return results

    def _merge_typed(
        self, code: List[SearchResult], history: List[SearchResult], top_k: int
    ) -> List[SearchResult]:
        """
        União tipada: a ordem do código é a mesma de antes da F5.1 e os commits
        entram apenas nas vagas restantes de `top_k`, por score decrescente.

        @MindDecision: a união por score foi medida no golden set e regrediu TODAS
        as classes (MRR total 0.4230 → 0.2077) — o pool histórico é pequeno, então
        seus scores RRF nascem altos e deslocam código. Fica o fallback previsto no
        plano: nenhuma classe regride e a história ainda é recuperável (GA-12).
        """
        results = code[:top_k]
        if not history or len(results) >= top_k:
            return results
        ordered = sorted(history, key=lambda result: (-result.score, result.commit.id))
        return results + ordered[: top_k - len(results)]
