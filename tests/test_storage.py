import json
from types import SimpleNamespace

import lancedb
import pyarrow as pa
import pytest

from codesteer_atlas.config import CANDIDATES_LIMIT
from codesteer_atlas.models import CodeChunk, IndexManifest
from codesteer_atlas.storage import StorageBackend

# Mock do vetor de 384 dimensões preenchido com zeros
MOCK_VECTOR = [0.0] * 384


@pytest.fixture
def temp_storage(tmp_path):
    """Fixture para criar um StorageBackend isolado em diretório temporário."""
    return StorageBackend(index_dir=tmp_path)


def test_store_and_get_manifest(temp_storage):
    """
    Testa se o StorageBackend grava corretamente os chunks no LanceDB
    e lê os dados correspondentes do manifest.json.
    """
    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/main.py",
            repo="test-project",
            start_line=1,
            end_line=10,
            scope_type="class",
            scope_name="MainClass",
            language="python",
            content="class MainClass: pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="c2",
            file_path="src/utils.go",
            repo="test-project",
            start_line=5,
            end_line=15,
            scope_type="function",
            scope_name="Helper",
            language="go",
            content="func Helper() {}",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
    ]

    temp_storage.store_chunks(chunks, git_head_sha="abcdef123456")

    # Verifica se os arquivos foram criados
    assert temp_storage.exists()
    assert temp_storage.manifest_path.exists()
    assert temp_storage.db_path.exists()

    # Lê o manifesto e valida metadados
    manifest = temp_storage.get_manifest()
    assert manifest.total_chunks == 2
    assert "test-project" in manifest.repos_indexed
    assert "python" in manifest.languages_indexed
    assert "go" in manifest.languages_indexed
    assert manifest.git_head_sha == "abcdef123456"


def test_append_chunks_preserves_existing_rows(temp_storage):
    """`append_chunks` deve inserir sem sobrescrever linhas já persistidas no LanceDB."""
    base_chunks = [
        CodeChunk(
            id="c1",
            file_path="src/main.py",
            repo="test-project",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="main",
            language="python",
            content="def main():\n    pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="c2",
            file_path="docs/guide.md",
            repo="test-project",
            start_line=1,
            end_line=3,
            scope_type="section",
            scope_name="Guide",
            language="markdown",
            content="# Guide\n\ncontent",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
    ]
    temp_storage.store_chunks(base_chunks)

    new_chunk = CodeChunk(
        id="c3",
        file_path="src/utils.py",
        repo="test-project",
        start_line=1,
        end_line=3,
        scope_type="function",
        scope_name="helper",
        language="python",
        content="def helper():\n    pass",
        indexed_at="2026-06-05T12:01:00Z",
        vector=MOCK_VECTOR,
    )
    temp_storage.append_chunks([new_chunk])

    symbols = temp_storage.get_symbols()
    assert len(symbols) == 3
    assert {row["file_path"] for row in symbols} == {
        "src/main.py",
        "docs/guide.md",
        "src/utils.py",
    }


def test_store_chunks_persists_references_json_and_search_returns_references(temp_storage):
    vec = [0.1] * 384
    chunk = CodeChunk(
        id="c1",
        file_path="src/main.py",
        repo="test-project",
        start_line=1,
        end_line=5,
        scope_type="function",
        scope_name="main",
        language="python",
        content="def main():\n    pass",
        indexed_at="2026-06-05T12:00:00Z",
        vector=vec,
        references=["cite:dec-002", "why:cache local"],
    )

    temp_storage.store_chunks([chunk])
    results = temp_storage.search_hybrid(query_vector=vec, query_text="main", filters={}, top_k=1).results

    assert results[0].references == ["cite:dec-002", "why:cache local"]


def test_append_chunks_preserves_references_for_old_and_new_rows(temp_storage):
    vec = [0.1] * 384
    temp_storage.store_chunks(
        [
            CodeChunk(
                id="c1",
                file_path="src/old.py",
                repo="test-project",
                start_line=1,
                end_line=2,
                scope_type="function",
                scope_name="old",
                language="python",
                content="def old(): pass",
                indexed_at="2026-06-05T12:00:00Z",
                vector=vec,
                references=["why:legado"],
            )
        ]
    )
    temp_storage.append_chunks(
        [
            CodeChunk(
                id="c2",
                file_path="src/new.py",
                repo="test-project",
                start_line=1,
                end_line=2,
                scope_type="function",
                scope_name="new",
                language="python",
                content="def new(): pass",
                indexed_at="2026-06-05T12:00:00Z",
                vector=vec,
                references=["cite:dec-003"],
            )
        ]
    )

    results = temp_storage.search_hybrid(query_vector=vec, query_text="def", filters={}, top_k=5).results
    refs_by_path = {result.file_path: result.references for result in results}

    assert refs_by_path["src/old.py"] == ["why:legado"]
    assert refs_by_path["src/new.py"] == ["cite:dec-003"]


def test_graph_projection_returns_columns_without_vector(temp_storage):
    temp_storage.store_chunks(
        [
            CodeChunk(
                id="c1",
                file_path="docs/guide.md",
                repo="test-project",
                start_line=1,
                end_line=3,
                scope_type="section",
                scope_name="Guide",
                language="markdown",
                content="# Guide\n\nbody",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
                references=["cite:dec-001"],
            )
        ]
    )

    rows = temp_storage.get_graph_projection()

    assert rows[0]["references_json"] == json.dumps(["cite:dec-001"], ensure_ascii=False)
    assert "vector" not in rows[0]


def test_graph_projection_avoids_loading_code_content(temp_storage):
    temp_storage.store_chunks(
        [
            CodeChunk(
                id="c1",
                file_path="src/app.py",
                repo="test-project",
                start_line=1,
                end_line=3,
                scope_type="function",
                scope_name="run",
                language="python",
                content="def run():\n    return 1",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
            CodeChunk(
                id="c2",
                file_path="docs/guide.md",
                repo="test-project",
                start_line=1,
                end_line=3,
                scope_type="section",
                scope_name="Guide",
                language="markdown",
                content="# Guide\n\nbody",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
        ]
    )

    rows = {row["file_path"]: row for row in temp_storage.get_graph_projection()}

    assert rows["src/app.py"]["content"] is None
    assert rows["docs/guide.md"]["content"] == "# Guide\n\nbody"


def test_search_hybrid_on_legacy_table_without_references_column_returns_empty_refs(temp_storage):
    temp_storage.index_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(temp_storage.db_path))
    db.create_table(
        "chunks",
        data=[
            {
                "id": "c1",
                "file_path": "src/legacy.py",
                "repo": "legacy",
                "start_line": 1,
                "end_line": 2,
                "scope_type": "function",
                "scope_name": "legacy",
                "language": "python",
                "content": "def legacy(): pass",
                "indexed_at": "2026-06-05T12:00:00Z",
                "vector": MOCK_VECTOR,
            }
        ],
        mode="overwrite",
    ).create_fts_index("content", replace=True)
    temp_storage.manifest_path.write_text(
        json.dumps(
            {
                "total_chunks": 1,
                "repos_indexed": ["legacy"],
                "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
                "embedding_dim": 384,
                "embedding_backend": "fastembed",
                "storage_backend": "lancedb",
                "last_indexed_at": "2026-06-05T12:00:00Z",
                "git_head_sha": None,
                "languages_indexed": ["python"],
                "index_version": "2.0.0",
                "files": {"src/legacy.py": "sha"},
                "files_meta": {},
            }
        ),
        encoding="utf-8",
    )

    results = temp_storage.search_hybrid(
        query_vector=MOCK_VECTOR, query_text="legacy", filters={}, top_k=1
    ).results

    assert results[0].references == []


def test_hybrid_search_with_filters(temp_storage):
    """
    Testa se a busca híbrida RRF funciona com sucesso e se os filtros
    de repositório, linguagem e prefixo de caminho funcionam corretamente.
    """
    # Vetores simulando proximidade semântica (cosseno)
    # Como não carregamos o modelo de embedding real no teste de storage,
    # passamos vetores estáticos.
    vec_auth = [0.1] * 384
    vec_database = [0.9] * 384

    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/auth/login.py",
            repo="project-a",
            start_line=1,
            end_line=10,
            scope_type="function",
            scope_name="login",
            language="python",
            content="def login(): return 'authenticated'",
            indexed_at="2026-06-05T12:00:00Z",
            vector=vec_auth,
        ),
        CodeChunk(
            id="c2",
            file_path="src/database/connection.py",
            repo="project-a",
            start_line=1,
            end_line=20,
            scope_type="class",
            scope_name="DBConnection",
            language="python",
            content="class DBConnection: def connect(self): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=vec_database,
        ),
        CodeChunk(
            id="c3",
            file_path="src/auth/jwt.go",
            repo="project-b",
            start_line=1,
            end_line=30,
            scope_type="function",
            scope_name="GenerateToken",
            language="go",
            content="func GenerateToken() string { return 'jwt' }",
            indexed_at="2026-06-05T12:00:00Z",
            vector=vec_auth,
        ),
    ]

    temp_storage.store_chunks(chunks)

    # 1. Busca ampla sem filtros por "login" (deve encontrar no FTS e vetor)
    results = temp_storage.search_hybrid(
        query_vector=vec_auth, query_text="login", filters={}, top_k=5
    ).results
    assert len(results) >= 1
    # Chunks c1 e c3 usam o mesmo vetor aproximado, c1 tem match FTS no termo 'login'
    assert results[0].scope_name == "login"

    # 2. Busca com filtro por repositório "project-b"
    results_repo = temp_storage.search_hybrid(
        query_vector=vec_auth, query_text="authenticated", filters={"repo": "project-b"}, top_k=5
    ).results
    # c1 tem o texto 'authenticated' mas é do project-a. Logo, deve filtrar e trazer apenas c3 do project-b.
    assert len(results_repo) == 1
    assert results_repo[0].repo == "project-b"
    assert results_repo[0].scope_name == "GenerateToken"

    # 3. Busca com filtro por prefixo de caminho "src/database/"
    results_path = temp_storage.search_hybrid(
        query_vector=vec_database,
        query_text="connect",
        filters={"path_prefix": "src/database/"},
        top_k=5,
    ).results
    assert len(results_path) == 1
    assert results_path[0].file_path == "src/database/connection.py"


def test_hybrid_search_prefilter_returns_full_top_k(temp_storage):
    """
    Com prefilter, um filtro seletivo (language) que ainda possui matches
    suficientes deve retornar exatamente `top_k` resultados.
    """
    vec = [0.5] * 384

    chunks = [
        CodeChunk(
            id=f"py-{i}",
            file_path=f"src/mod_{i}.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name=f"func_{i}",
            language="python",
            content=f"def func_{i}(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=vec,
        )
        for i in range(10)
    ] + [
        CodeChunk(
            id=f"go-{i}",
            file_path=f"src/mod_{i}.go",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name=f"GoFunc{i}",
            language="go",
            content=f"func GoFunc{i}() {{}}",
            indexed_at="2026-06-05T12:00:00Z",
            vector=vec,
        )
        for i in range(10)
    ]

    temp_storage.store_chunks(chunks)

    results = temp_storage.search_hybrid(
        query_vector=vec, query_text="func", filters={"language": "python"}, top_k=5
    ).results

    assert len(results) == 5
    assert all(r.language == "python" for r in results)


def test_hybrid_search_filter_no_matches_returns_empty(temp_storage):
    """Filtro sem nenhum match retorna lista vazia, sem levantar exceção."""
    vec = [0.5] * 384

    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/main.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="main",
            language="python",
            content="def main(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=vec,
        )
    ]

    temp_storage.store_chunks(chunks)

    results = temp_storage.search_hybrid(
        query_vector=vec, query_text="main", filters={"language": "rust"}, top_k=5
    ).results

    assert results == []


def test_hybrid_search_path_prefix_escapes_single_quote(temp_storage):
    """`path_prefix` contendo aspas simples não quebra a query SQL (escape)."""
    vec = [0.5] * 384

    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/it's_a_dir/file.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="run",
            language="python",
            content="def run(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=vec,
        )
    ]

    temp_storage.store_chunks(chunks)

    # Não deve levantar exceção de SQL e deve encontrar o arquivo correto
    results = temp_storage.search_hybrid(
        query_vector=vec, query_text="run", filters={"path_prefix": "src/it's_a_dir/"}, top_k=5
    ).results

    assert len(results) == 1
    assert results[0].file_path == "src/it's_a_dir/file.py"


def test_hybrid_search_path_prefix_normalizes_backslash_to_posix(temp_storage):
    """`path_prefix` com separadores estilo Windows é normalizado para POSIX."""
    vec = [0.5] * 384

    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/database/connection.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="class",
            scope_name="DBConnection",
            language="python",
            content="class DBConnection: pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=vec,
        )
    ]

    temp_storage.store_chunks(chunks)

    # path_prefix com barra invertida (estilo Windows) deve ser normalizado para POSIX
    results = temp_storage.search_hybrid(
        query_vector=vec,
        query_text="DBConnection",
        filters={"path_prefix": "src\\database\\"},
        top_k=5,
    ).results

    assert len(results) == 1
    assert results[0].file_path == "src/database/connection.py"


def test_get_symbols_excludes_vector(temp_storage):
    """`get_symbols()` retorna apenas file_path/scope_type/scope_name (sem vector)."""
    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/main.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="main",
            language="python",
            content="def main(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        )
    ]

    temp_storage.store_chunks(chunks)

    symbols = temp_storage.get_symbols()

    assert len(symbols) == 1
    assert symbols[0]["file_path"] == "src/main.py"
    assert symbols[0]["scope_type"] == "function"
    assert symbols[0]["scope_name"] == "main"
    assert "vector" not in symbols[0]
    assert "content" not in symbols[0]


def test_get_manifest_incompatible_version_raises_runtime_error(temp_storage):
    """Manifest com index_version < MIN_INDEX_VERSION levanta RuntimeError acionável."""
    import json

    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/main.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="main",
            language="python",
            content="def main(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        )
    ]
    temp_storage.store_chunks(chunks)

    # Sobrescreve o manifest simulando um índice antigo (v1.0.0)
    with open(temp_storage.manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["index_version"] = "1.0.0"
    with open(temp_storage.manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(RuntimeError):
        temp_storage.get_manifest()


def test_store_chunks_writes_manifest_atomically_no_leftover_tmp(temp_storage):
    """`store_chunks` não deixa arquivo `.json.tmp` residual e o manifest.json é válido."""
    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/main.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="main",
            language="python",
            content="def main(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        )
    ]

    temp_storage.store_chunks(chunks)

    tmp_path = temp_storage.manifest_path.with_suffix(".json.tmp")
    assert not tmp_path.exists()

    manifest = temp_storage.get_manifest()
    assert manifest.total_chunks == 1


def test_append_chunks_updates_fts_index_for_new_content(temp_storage):
    """`append_chunks` em tabela com FTS já existente atualiza o índice (optimize/fallback),
    permitindo que a busca FTS encontre o conteúdo do chunk recém-adicionado."""
    base_chunks = [
        CodeChunk(
            id="c1",
            file_path="src/main.py",
            repo="test-project",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="main",
            language="python",
            content="def main():\n    pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
    ]
    temp_storage.store_chunks(base_chunks)

    new_chunk = CodeChunk(
        id="c2",
        file_path="src/payments.py",
        repo="test-project",
        start_line=1,
        end_line=3,
        scope_type="function",
        scope_name="charge_card",
        language="python",
        content="def charge_card():\n    return 'zorbflex_unique_token'",
        indexed_at="2026-06-05T12:01:00Z",
        vector=MOCK_VECTOR,
    )
    temp_storage.append_chunks([new_chunk])

    results = temp_storage.search_hybrid(
        query_vector=MOCK_VECTOR,
        query_text="zorbflex_unique_token",
        filters={},
        top_k=5,
    ).results

    assert any(r.scope_name == "charge_card" for r in results)


def test_delete_by_file_paths_removes_multiple_in_single_call(temp_storage):
    """`delete_by_file_paths` remove múltiplos paths (incluindo aspas simples) em uma chamada."""
    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/main.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="main",
            language="python",
            content="def main(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="c2",
            file_path="src/it's_a_dir/file.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="run",
            language="python",
            content="def run(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="c3",
            file_path="src/keep.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="keep",
            language="python",
            content="def keep(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
    ]
    temp_storage.store_chunks(chunks)

    temp_storage.delete_by_file_paths(["src/main.py", "src/it's_a_dir/file.py"])

    symbols = temp_storage.get_symbols()
    remaining_paths = {row["file_path"] for row in symbols}
    assert remaining_paths == {"src/keep.py"}


def test_update_manifest_after_incremental_writes_manifest_atomically(temp_storage):
    """`update_manifest_after_incremental` regrava o manifest sem deixar `.json.tmp` residual."""
    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/main.py",
            repo="project-a",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="main",
            language="python",
            content="def main(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        )
    ]
    temp_storage.store_chunks(chunks)

    total = temp_storage.update_manifest_after_incremental(files={"src/main.py": "hash1"})

    tmp_path = temp_storage.manifest_path.with_suffix(".json.tmp")
    assert not tmp_path.exists()
    assert total == 1
    assert temp_storage.get_manifest().files == {"src/main.py": "hash1"}


def test_get_sections_by_file_path_returns_scope_names(temp_storage):
    """`get_sections_by_file_path` retorna scope_type/scope_name dos chunks do arquivo informado."""
    chunks = [
        CodeChunk(
            id="c1",
            file_path="docs/decisions.md",
            repo="test-project",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Decisão 007",
            language="markdown",
            content="## Decisão 007\n\nconteúdo",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="c2",
            file_path="docs/decisions.md",
            repo="test-project",
            start_line=6,
            end_line=10,
            scope_type="section",
            scope_name="Decisão 008",
            language="markdown",
            content="## Decisão 008\n\noutro conteúdo",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="c3",
            file_path="src/main.py",
            repo="test-project",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="main",
            language="python",
            content="def main(): pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
    ]
    temp_storage.store_chunks(chunks)

    sections = temp_storage.get_sections_by_file_path("docs/decisions.md")

    scope_names = {row["scope_name"] for row in sections}
    assert scope_names == {"Decisão 007", "Decisão 008"}
    assert all(row["scope_type"] == "section" for row in sections)


def test_get_sections_by_file_path_empty_for_unknown_file(temp_storage):
    """`get_sections_by_file_path` retorna lista vazia para arquivo não indexado."""
    chunks = [
        CodeChunk(
            id="c1",
            file_path="docs/decisions.md",
            repo="test-project",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Decisão 007",
            language="markdown",
            content="## Decisão 007\n\nconteúdo",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
    ]
    temp_storage.store_chunks(chunks)

    sections = temp_storage.get_sections_by_file_path("docs/unknown.md")

    assert sections == []


class _FailingArmTable:
    """Envolve a tabela real e faz falhar apenas o braço escolhido da busca híbrida."""

    def __init__(self, table, fail_vector=False, fail_fts=False, fail_semantic=False):
        self._table = table
        self._fail_vector = fail_vector
        self._fail_fts = fail_fts
        self._fail_semantic = fail_semantic

    def search(self, query=None, query_type=None, **kwargs):
        if query_type == "fts":
            if self._fail_fts:
                raise RuntimeError("índice FTS corrompido")
            return self._table.search(query, query_type=query_type, **kwargs)
        if kwargs.get("vector_column_name") == "purpose_vector" and self._fail_semantic:
            raise RuntimeError("índice semântico corrompido")
        if self._fail_vector:
            raise RuntimeError("índice vetorial corrompido")
        return self._table.search(query, **kwargs)

    def __getattr__(self, name):
        return getattr(self._table, name)


class _FailingArmDB:
    def __init__(self, db, **flags):
        self._db = db
        self._flags = flags

    def open_table(self, name):
        return _FailingArmTable(self._db.open_table(name), **self._flags)

    def __getattr__(self, name):
        return getattr(self._db, name)


# Vetores distintos e não-nulos: com a métrica de cosseno, o MOCK_VECTOR de zeros
# produz similaridade indefinida e o braço vetorial não retorna nada — o que
# mascararia justamente a degradação que estes testes querem observar.
VEC_A = [1.0] + [0.0] * 383
VEC_B = [0.0, 1.0] + [0.0] * 382


def _seed_two_chunks(storage, with_purpose=False):
    chunks = [
        CodeChunk(
            id="c1",
            file_path="src/main.py",
            repo="test-project",
            start_line=1,
            end_line=10,
            scope_type="function",
            scope_name="main",
            language="python",
            content="def main(): print('hello world')",
            indexed_at="2026-06-05T12:00:00Z",
            vector=VEC_A,
        ),
        CodeChunk(
            id="c2",
            file_path="src/utils.py",
            repo="test-project",
            start_line=1,
            end_line=8,
            scope_type="function",
            scope_name="helper",
            language="python",
            content="def helper(): return 42",
            indexed_at="2026-06-05T12:00:00Z",
            vector=VEC_B,
        ),
    ]
    if with_purpose:
        chunks[0].purpose = "propósito persistido"
        chunks[0].purpose_hash = "hash"
        chunks[0].purpose_vector = VEC_A
    storage.store_chunks(chunks)


def _search_with_failing_arm(temp_storage, monkeypatch, **flags):
    real_connect = lancedb.connect

    def _fake_connect(uri, *args, **kwargs):
        return _FailingArmDB(real_connect(uri, *args, **kwargs), **flags)

    monkeypatch.setattr("codesteer_atlas.storage.lancedb.connect", _fake_connect)
    return temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="main", filters={}, top_k=5
    )


def test_search_hybrid_reports_fts_unavailable_and_keeps_vector_results(
    temp_storage, monkeypatch
):
    """Braço FTS quebrado degrada para só-vetorial, mas o aviso chega ao chamador."""
    _seed_two_chunks(temp_storage)

    outcome = _search_with_failing_arm(temp_storage, monkeypatch, fail_fts=True)

    assert "fts_unavailable" in outcome.warnings
    assert "vector_search_unavailable" not in outcome.warnings
    # A degradação não pode zerar a busca: o braço vetorial ainda responde
    assert len(outcome.results) == 2


def test_search_hybrid_reports_vector_unavailable_and_keeps_fts_results(
    temp_storage, monkeypatch
):
    """Braço vetorial quebrado degrada para só-BM25, com aviso explícito."""
    _seed_two_chunks(temp_storage)

    outcome = _search_with_failing_arm(temp_storage, monkeypatch, fail_vector=True)

    assert "vector_search_unavailable" in outcome.warnings
    assert "fts_unavailable" not in outcome.warnings
    assert len(outcome.results) >= 1


def test_search_hybrid_raises_when_both_arms_fail(temp_storage, monkeypatch):
    """
    Com os dois braços quebrados, devolver lista vazia seria indistinguível de
    'nenhum resultado' — precisa levantar erro acionável.
    """
    _seed_two_chunks(temp_storage)

    with pytest.raises(RuntimeError, match="reindexe|Reindexe"):
        _search_with_failing_arm(
            temp_storage, monkeypatch, fail_vector=True, fail_fts=True
        )


def _publish_history(storage, records=()):
    """Publica um snapshot histórico ativo (vazio por padrão) para a busca."""
    snapshot_id = storage.stage_history(list(records))
    return storage.publish_history(snapshot_id)


def test_search_hybrid_healthy_index_has_no_warnings(temp_storage):
    """Busca saudável não deve emitir nenhum aviso de degradação."""
    _seed_two_chunks(temp_storage)
    # Índice saudável hoje inclui a camada histórica publicada; sua ausência é
    # degradação declarada (git_history_unavailable), coberta em teste próprio.
    _publish_history(temp_storage)

    outcome = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="main", filters={}, top_k=5
    )

    assert outcome.warnings == []
    assert len(outcome.results) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Proveniência de match e reordenação pós-RRF
# ─────────────────────────────────────────────────────────────────────────────


def _seed_identifier_chunks(storage):
    """Chunks cujo nome de símbolo é longo o bastante para exercitar trigramas."""
    chunks = [
        CodeChunk(
            id="n1",
            file_path="src/storage.py",
            repo="test-project",
            start_line=1,
            end_line=20,
            scope_type="method",
            scope_name="StorageBackend.search_hybrid",
            language="python",
            content="def search_hybrid(self): ...",
            indexed_at="2026-06-05T12:00:00Z",
            vector=VEC_B,
        ),
        CodeChunk(
            id="n2",
            file_path="src/other.py",
            repo="test-project",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="totalmente_diferente",
            language="python",
            content="def totalmente_diferente(): ...",
            indexed_at="2026-06-05T12:00:00Z",
            vector=VEC_A,
        ),
    ]
    storage.store_chunks(chunks)




def test_match_arms_registra_origem_do_resultado(temp_storage):
    _seed_two_chunks(temp_storage)

    outcome = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="main", filters={}, top_k=5
    )

    assert outcome.results
    for resultado in outcome.results:
        assert resultado.match_arms
        assert set(resultado.match_arms) <= {"vector", "fts"}




def test_rerank_desligado_por_env_preserva_ordem_do_rrf(temp_storage, monkeypatch):
    _seed_identifier_chunks(temp_storage)

    monkeypatch.setenv("ATLAS_RERANK", "0")
    sem_rerank = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="search_hybrid", filters={}, top_k=5
    )

    # Com o rerank desligado a ordem é estritamente decrescente em score RRF
    scores = [r.score for r in sem_rerank.results]
    assert scores == sorted(scores, reverse=True)


def test_sem_atlas_rerank_model_mantem_ordem_do_rerank_lexical(temp_storage, monkeypatch):
    """Sem a variável, a ordem final é byte-a-byte a de ranking.rerank."""
    from codesteer_atlas.ranking import rerank as lexical_rerank

    _seed_identifier_chunks(temp_storage)
    monkeypatch.delenv("ATLAS_RERANK_MODEL", raising=False)
    monkeypatch.delenv("ATLAS_RERANK", raising=False)

    monkeypatch.setenv("ATLAS_RERANK", "0")
    rrf = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="search_hybrid", filters={}, top_k=5
    )
    monkeypatch.delenv("ATLAS_RERANK")
    esperado = [r.scope_name for r in lexical_rerank(list(rrf.results), "search_hybrid")]

    atual = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="search_hybrid", filters={}, top_k=5
    )
    assert [r.scope_name for r in atual.results] == esperado
    assert "cross_encoder_unavailable" not in atual.warnings


def test_cross_encoder_falhando_emite_aviso_e_cai_no_lexical(temp_storage, monkeypatch):
    from codesteer_atlas.reranker import CrossEncoderReranker

    _seed_identifier_chunks(temp_storage)
    monkeypatch.setenv("ATLAS_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")

    def _boom(self, query, results):
        raise RuntimeError("modelo indisponível")

    monkeypatch.setattr(CrossEncoderReranker, "rerank", _boom)

    outcome = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="search_hybrid", filters={}, top_k=5
    )

    assert "cross_encoder_unavailable" in outcome.warnings
    assert outcome.results


def test_structural_false_nao_altera_match_arms(temp_storage, monkeypatch):
    _seed_two_chunks(temp_storage)
    monkeypatch.delenv("ATLAS_RERANK_MODEL", raising=False)

    padrao = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="main", filters={}, top_k=5
    )
    explicito = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="main", filters={}, top_k=5, structural=False
    )

    assert [r.match_arms for r in padrao.results] == [r.match_arms for r in explicito.results]
    for resultado in explicito.results:
        assert "graph" not in resultado.match_arms


def _write_synthetic_graph(index_dir, chunks):
    import json

    nodes = []
    edges = []
    prev = None
    for chunk in chunks:
        node_id = f"sym:{chunk.file_path}#{chunk.scope_name}"
        nodes.append(
            {
                "id": node_id,
                "kind": "symbol",
                "label": chunk.scope_name,
                "file_path": chunk.file_path,
                "degree": 1,
            }
        )
        if prev is not None:
            edges.append({"source": prev, "target": node_id, "kind": "relates"})
        prev = node_id
    payload = {"nodes": nodes, "edges": edges}
    (index_dir / "graph.json").write_text(json.dumps(payload), encoding="utf-8")


def test_structural_true_acrescenta_graph_a_match_arms(temp_storage, monkeypatch):
    _seed_two_chunks(temp_storage)
    _write_synthetic_graph(
        temp_storage.index_dir,
        [
            SimpleNamespace(file_path="src/main.py", scope_name="main"),
            SimpleNamespace(file_path="src/utils.py", scope_name="helper"),
        ],
    )
    monkeypatch.delenv("ATLAS_RERANK_MODEL", raising=False)

    outcome = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="main", filters={}, top_k=5, structural=True
    )

    ativados = [r for r in outcome.results if "graph" in r.match_arms]
    assert ativados
    assert "structural_arm_unavailable" not in outcome.warnings


def test_structural_true_sem_graph_json_emite_aviso_e_nao_quebra(temp_storage, monkeypatch):
    _seed_two_chunks(temp_storage)
    monkeypatch.delenv("ATLAS_RERANK_MODEL", raising=False)

    outcome = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="main", filters={}, top_k=5, structural=True
    )

    assert "structural_arm_unavailable" in outcome.warnings
    assert outcome.results
    for resultado in outcome.results:
        assert "graph" not in resultado.match_arms


def test_atlas_rerank_zero_desliga_toda_reordenacao_inclusive_ce(temp_storage, monkeypatch):
    from codesteer_atlas.reranker import CrossEncoderReranker

    _seed_identifier_chunks(temp_storage)
    monkeypatch.setenv("ATLAS_RERANK", "0")
    monkeypatch.setenv("ATLAS_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
    called = {"n": 0}

    def _spy(self, query, results):
        called["n"] += 1
        return results

    monkeypatch.setattr(CrossEncoderReranker, "rerank", _spy)

    outcome = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="search_hybrid", filters={}, top_k=5
    )

    assert called["n"] == 0
    scores = [r.score for r in outcome.results]
    assert scores == sorted(scores, reverse=True)


def test_purpose_persistido_e_retornado_por_lookup(temp_storage):
    """Purpose semântico persiste como coluna irmã e é recuperado pelo lookup."""
    purpose = "Valida credenciais e cria a sessão do usuário."
    chunk = CodeChunk(
        id="purpose-1",
        file_path="src/auth.py",
        repo="test-project",
        start_line=1,
        end_line=4,
        scope_type="function",
        scope_name="login",
        language="python",
        content="def login():\n    return True",
        indexed_at="2026-06-05T12:00:00Z",
        vector=MOCK_VECTOR,
        purpose=purpose,
        purpose_hash="purpose-hash",
        purpose_vector=[0.2] * 384,
    )

    temp_storage.store_chunks([chunk])

    row = (
        lancedb.connect(str(temp_storage.db_path))
        .open_table("chunks")
        .search()
        .select(["content", "vector", "purpose", "purpose_hash", "purpose_vector"])
        .limit(1)
        .to_arrow()
        .to_pylist()[0]
    )

    schema = lancedb.connect(str(temp_storage.db_path)).open_table("chunks").schema
    assert schema.field("purpose").type == pa.string()
    assert schema.field("purpose").nullable is True
    assert schema.field("purpose_hash").type == pa.string()
    assert schema.field("purpose_hash").nullable is True
    assert schema.field("purpose_vector").type == pa.list_(pa.float32(), 384)
    assert schema.field("purpose_vector").nullable is True

    assert row["content"] == chunk.content
    assert row["vector"] == MOCK_VECTOR
    assert row["purpose"] == purpose
    assert row["purpose_hash"] == "purpose-hash"
    assert row["purpose_vector"] == pytest.approx([0.2] * 384)
    assert temp_storage.lookup_purpose("src/auth.py", "login") == purpose


def _chunk_with_purpose_vector(chunk_id: str, purpose_vector):
    return CodeChunk(
        id=chunk_id,
        file_path=f"src/{chunk_id}.py",
        repo="test-project",
        start_line=1,
        end_line=2,
        scope_type="function",
        scope_name=chunk_id,
        language="python",
        content=f"def {chunk_id}(): pass",
        indexed_at="2026-06-05T12:00:00Z",
        vector=MOCK_VECTOR,
        purpose_vector=purpose_vector,
    )


def test_store_chunks_normaliza_purpose_vector_nulo_vazio_ou_dimensao_errada(temp_storage):
    """Índice grande no Windows falha se purpose_vector mistura None, [] e 384 dims."""
    chunks = [
        _chunk_with_purpose_vector("none", None),
        _chunk_with_purpose_vector("empty", []),
        _chunk_with_purpose_vector("short", [0.3] * 10),
        _chunk_with_purpose_vector("ok", [0.2] * 384),
    ]
    chunks.extend(_chunk_with_purpose_vector(f"pad{i}", None) for i in range(80))

    temp_storage.store_chunks(chunks)

    rows = {
        row["id"]: row
        for row in lancedb.connect(str(temp_storage.db_path))
        .open_table("chunks")
        .to_arrow()
        .to_pylist()
    }
    assert rows["none"]["purpose_vector"] is None
    assert rows["empty"]["purpose_vector"] is None
    assert rows["short"]["purpose_vector"] is None
    assert rows["ok"]["purpose_vector"] == pytest.approx([0.2] * 384)
    assert rows["pad0"]["purpose_vector"] is None


def test_append_chunks_normaliza_purpose_vector_invalido(temp_storage):
    temp_storage.store_chunks([_chunk_with_purpose_vector("base", [0.1] * 384)])
    temp_storage.append_chunks(
        [
            _chunk_with_purpose_vector("empty", []),
            _chunk_with_purpose_vector("ok", [0.4] * 384),
        ]
    )

    rows = {
        row["id"]: row
        for row in lancedb.connect(str(temp_storage.db_path))
        .open_table("chunks")
        .to_arrow()
        .to_pylist()
    }
    assert rows["empty"]["purpose_vector"] is None
    assert rows["ok"]["purpose_vector"] == pytest.approx([0.4] * 384)


def _write_legacy_storage(storage, version="2.2.0"):
    storage.index_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(storage.db_path))
    db.create_table(
        "chunks",
        data=[
            {
                "id": "legacy-1",
                "file_path": "src/legacy.py",
                "repo": "legacy",
                "start_line": 1,
                "end_line": 2,
                "scope_type": "function",
                "scope_name": "legacy",
                "language": "python",
                "content": "def legacy(): pass",
                "indexed_at": "2026-06-05T12:00:00Z",
                "vector": VEC_A,
            }
        ],
        mode="overwrite",
    ).create_fts_index("content", replace=True)
    storage.manifest_path.write_text(
        json.dumps(
            {
                "total_chunks": 1,
                "repos_indexed": ["legacy"],
                "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
                "embedding_dim": 384,
                "embedding_backend": "fastembed",
                "storage_backend": "lancedb",
                "last_indexed_at": "2026-06-05T12:00:00Z",
                "git_head_sha": None,
                "languages_indexed": ["python"],
                "index_version": version,
                "files": {"src/legacy.py": "sha"},
                "files_meta": {},
            }
        ),
        encoding="utf-8",
    )


def test_legacy_22_incremental_off_e_on_permanece_immutavel(temp_storage, monkeypatch):
    """Índice legado não aceita append, delete ou rewrite em nenhuma camada."""
    _write_legacy_storage(temp_storage)
    manifest_before = temp_storage.manifest_path.read_bytes()
    symbols_before = temp_storage.get_symbols()
    new_chunk = CodeChunk(
        id="new",
        file_path="src/new.py",
        repo="legacy",
        start_line=1,
        end_line=2,
        scope_type="function",
        scope_name="new",
        language="python",
        content="def new(): pass",
        indexed_at="2026-06-05T12:00:00Z",
        vector=VEC_A,
    )

    for enabled in (False, True):
        if enabled:
            monkeypatch.setenv("ATLAS_SEMANTIC", "1")
        else:
            monkeypatch.delenv("ATLAS_SEMANTIC", raising=False)

        with pytest.raises(RuntimeError, match="legado|rechunkear"):
            temp_storage.append_chunks([new_chunk])
        with pytest.raises(RuntimeError, match="legado|rechunkear"):
            temp_storage.delete_by_file_paths(["src/legacy.py"])
        with pytest.raises(RuntimeError, match="legado|rechunkear"):
            temp_storage.update_manifest_after_incremental({"src/legacy.py": "new-sha"})

    assert temp_storage.manifest_path.read_bytes() == manifest_before
    assert temp_storage.get_symbols() == symbols_before
    assert temp_storage.get_manifest().index_version == "2.2.0"


def test_schema_incompativel_e_rejeitado_antes_do_delete(temp_storage):
    """Uma tabela 2.3.0 mal materializada não pode perder o arquivo tocado."""
    chunk = CodeChunk(
        id="bad-schema",
        file_path="src/app.py",
        repo="test-project",
        start_line=1,
        end_line=2,
        scope_type="function",
        scope_name="run",
        language="python",
        content="def run(): pass",
        indexed_at="2026-06-05T12:00:00Z",
        vector=VEC_A,
    )
    temp_storage.index_dir.mkdir(parents=True, exist_ok=True)
    row = temp_storage._chunk_to_row(chunk)
    db = lancedb.connect(str(temp_storage.db_path))
    db.create_table("chunks", data=[row], mode="overwrite").create_fts_index(
        "content", replace=True
    )
    manifest = IndexManifest(
        total_chunks=1,
        repos_indexed=["test-project"],
        embedding_model="all-MiniLM-L6-v2",
        embedding_dim=384,
        last_indexed_at="2026-06-05T12:00:00Z",
        languages_indexed=["python"],
        index_version="2.3.0",
        files={"src/app.py": "sha"},
    )
    temp_storage.manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    before = db.open_table("chunks").to_arrow().to_pylist()

    with pytest.raises(RuntimeError, match="schema|reindexar"):
        temp_storage.delete_by_file_paths(["src/app.py"])

    assert db.open_table("chunks").to_arrow().to_pylist() == before


def test_semantic_off_nao_adiciona_arm_nem_warning(temp_storage, monkeypatch):
    """A camada desligada preserva a busca estrutural mesmo com propósito persistido."""
    _seed_two_chunks(temp_storage, with_purpose=True)
    monkeypatch.delenv("ATLAS_SEMANTIC", raising=False)

    outcome = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="main", filters={}, top_k=5
    )

    assert "semantic_arm_unavailable" not in outcome.warnings
    assert "semantic_layer_unavailable" not in outcome.warnings
    assert all("semantic" not in result.match_arms for result in outcome.results)


def test_semantic_ready_funde_query_filtros_limite_e_match_arms(temp_storage, monkeypatch):
    """Ready consulta os dois espaços com os mesmos filtros e registra consenso real."""
    shared = {
        "id": "shared",
        "file_path": "src/shared.py",
        "repo": "test-project",
        "start_line": 1,
        "end_line": 2,
        "scope_type": "function",
        "scope_name": "shared",
        "language": "python",
        "content": "def shared(): pass",
        "indexed_at": "2026-06-05T12:00:00Z",
        "vector": VEC_A,
        "references_json": "[]",
    }
    semantic_only = {**shared, "id": "semantic-only", "scope_name": "meaning"}
    calls = []

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def metric(self, value):
            calls.append(("metric", value))
            return self

        def where(self, clause, prefilter=False):
            calls.append(("where", clause, prefilter))
            return self

        def limit(self, value):
            calls.append(("limit", value))
            return self

        def to_list(self):
            return self.rows

    class Table:
        def search(self, query=None, query_type=None, **kwargs):
            calls.append(("search", query, query_type, kwargs.get("vector_column_name")))
            if query_type == "fts":
                return Query([shared])
            if kwargs.get("vector_column_name") == "purpose_vector":
                return Query([shared, semantic_only])
            return Query([shared])

    class DB:
        def open_table(self, name):
            assert name == "chunks"
            return Table()

    monkeypatch.setenv("ATLAS_SEMANTIC", "1")
    monkeypatch.setenv("ATLAS_RERANK", "0")
    temp_storage.store_chunks([CodeChunk(**{**shared, "id": "shared"})])
    monkeypatch.setattr("codesteer_atlas.storage.lancedb.connect", lambda *_args, **_kwargs: DB())
    monkeypatch.setattr("codesteer_atlas.storage.semantic_enabled", lambda: True)
    monkeypatch.setattr(
        "codesteer_atlas.storage.semantic_index_state", lambda *_args: ("ready", None)
    )

    outcome = temp_storage.search_hybrid(
        query_vector=VEC_A,
        query_text="meaning",
        filters={"language": "python", "path_prefix": "src"},
        top_k=2,
    )

    searches = [call for call in calls if call[0] == "search"]
    assert searches == [
        ("search", VEC_A, None, "vector"),
        ("search", "meaning", "fts", None),
        ("search", VEC_A, None, "purpose_vector"),
    ]
    assert [call for call in calls if call[0] == "limit"] == [
        ("limit", CANDIDATES_LIMIT),
        ("limit", CANDIDATES_LIMIT),
        ("limit", CANDIDATES_LIMIT),
    ]
    assert len([call for call in calls if call[0] == "where"]) == 3
    arms = {result.scope_name: result.match_arms for result in outcome.results}
    assert arms["shared"] == ["vector", "fts", "semantic"]
    assert arms["meaning"] == ["semantic"]
    searches = [call for call in calls if call[0] == "search"]
    assert searches[0][1] == searches[2][1] == VEC_A
    assert searches[0][3] == "vector"
    assert searches[2][3] == "purpose_vector"
    where_clauses = [call for call in calls if call[0] == "where"]
    assert len({call[1] for call in where_clauses}) == 1
    assert all(call[2] is True for call in where_clauses)


def test_falha_so_do_semantic_preserva_resultados_e_avisa(temp_storage, monkeypatch):
    """Falha semântica isolada mantém vector+FTS e sinaliza a degradação."""
    _seed_two_chunks(temp_storage)
    monkeypatch.setenv("ATLAS_SEMANTIC", "1")
    monkeypatch.setattr("codesteer_atlas.storage.semantic_enabled", lambda: True)
    monkeypatch.setattr(
        "codesteer_atlas.storage.semantic_index_state", lambda *_args: ("ready", None)
    )

    outcome = _search_with_failing_arm(temp_storage, monkeypatch, fail_semantic=True)

    assert "semantic_arm_unavailable" in outcome.warnings
    assert outcome.results
    assert all("semantic" not in result.match_arms for result in outcome.results)


# ─────────────────────────────────────────────────────────────────────────────
# F5.1 — camada histórica: tabela dedicada, snapshot ativo e recuperação tipada
# ─────────────────────────────────────────────────────────────────────────────


def _commit_record(sha="a" * 40, subject="Corrige resolução de imports", **overrides):
    record = {
        "id": sha,
        "repo": "test-project",
        "subject": subject,
        "body": "Preserva a resolução relativa em pacotes aninhados.",
        "authored_at": "2026-08-30T12:00:00+00:00",
        "committed_at": "2026-08-30T12:05:00+00:00",
        "files_touched": ["src/main.py", "src/utils.py"],
        "touches": [
            {"file_path": "src/main.py", "scope_name": "main"},
            {"file_path": "src/main.py", "scope_name": "main"},
        ],
        "is_revert": False,
        "reverted_commit_id": None,
        "vector": MOCK_VECTOR,
    }
    record.update(overrides)
    return record


def test_history_nao_toca_a_tabela_chunks(temp_storage):
    """GA-01: commit vive em tabela própria; o schema de chunks segue intacto."""
    import lancedb

    from codesteer_atlas.storage import _table_names

    _seed_two_chunks(temp_storage)
    schema_antes = lancedb.connect(str(temp_storage.db_path)).open_table("chunks").schema

    _publish_history(temp_storage, [_commit_record()])

    db = lancedb.connect(str(temp_storage.db_path))
    assert db.open_table("chunks").schema == schema_antes
    assert db.open_table("chunks").count_rows() == 2
    assert any(name.startswith("commits_") for name in _table_names(db))


def test_history_staging_invisivel_ate_a_publicacao(temp_storage):
    """ADR-010: geração não publicada não aparece em estado, lookup nem projeção."""
    _seed_two_chunks(temp_storage)
    snapshot_id = temp_storage.stage_history([_commit_record()])

    assert temp_storage.get_history_state()["state"] == "absent"
    assert temp_storage.lookup_commits([("test-project", "a" * 40)]) == []
    assert temp_storage.get_history_projection() == []

    temp_storage.publish_history(snapshot_id)

    assert temp_storage.get_history_state()["state"] == "ok"
    (record, stale) = temp_storage.lookup_commits([("test-project", "a" * 40)])[0]
    assert record.id == "a" * 40
    assert record.files_touched == ["src/main.py", "src/utils.py"]
    assert stale is False


def test_history_falha_antes_de_ativar_preserva_o_snapshot_anterior(temp_storage):
    """GA-08: uma geração candidata não publicada não substitui a ativa."""
    _seed_two_chunks(temp_storage)
    _publish_history(temp_storage, [_commit_record()])
    ativo = temp_storage.read_history_pointer()

    temp_storage.stage_history([_commit_record(sha="b" * 40, subject="outro")])

    assert temp_storage.read_history_pointer() == ativo
    assert [row["id"] for row in temp_storage.get_history_projection()] == ["a" * 40]

    # GC roda depois da publicação e nunca remove a geração ativa
    temp_storage.gc_history()
    assert temp_storage.get_history_state()["state"] == "ok"


def test_search_hybrid_sem_camada_historica_avisa_e_preserva_o_codigo(temp_storage):
    """GA-009-07: ausência da camada é declarada, sem alterar o resultado de código."""
    _seed_two_chunks(temp_storage)

    degradado = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="main", filters={}, top_k=5
    )
    _publish_history(temp_storage)
    saudavel = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="main", filters={}, top_k=5
    )

    assert degradado.warnings == ["git_history_unavailable"]
    assert saudavel.warnings == []
    assert [result.file_path for result in degradado.results] == [
        result.file_path for result in saudavel.results
    ]


def test_search_hybrid_devolve_commit_tipado_sem_braco_graph(temp_storage):
    """RF06: resultado histórico é tipado, único por commit e nunca ganha `graph`."""
    _seed_two_chunks(temp_storage)
    _publish_history(temp_storage, [_commit_record()])

    outcome = temp_storage.search_hybrid(
        query_vector=MOCK_VECTOR,
        query_text="resolução de imports",
        filters={},
        top_k=10,
    )
    commits = [result for result in outcome.results if result.type == "commit"]

    assert len(commits) == 1
    assert commits[0].language == "git"
    assert commits[0].commit.id == "a" * 40
    assert commits[0].file_path == "" and commits[0].start_line == 0
    assert "graph" not in commits[0].match_arms
    assert commits[0].match_arms


def test_search_hybrid_commit_casa_path_prefix_por_arquivo_tocado(temp_storage):
    """Um commit casa `path_prefix` quando algum arquivo tocado está sob o prefixo."""
    _seed_two_chunks(temp_storage)
    _publish_history(temp_storage, [_commit_record()])

    dentro = temp_storage.search_hybrid(
        query_vector=MOCK_VECTOR,
        query_text="resolução de imports",
        filters={"path_prefix": "src"},
        top_k=10,
    )
    fora = temp_storage.search_hybrid(
        query_vector=MOCK_VECTOR,
        query_text="resolução de imports",
        filters={"path_prefix": "docs"},
        top_k=10,
    )

    assert any(result.type == "commit" for result in dentro.results)
    assert not any(result.type == "commit" for result in fora.results)


def test_commit_sem_ancoragem_continua_recuperavel(temp_storage):
    """CA17: sem interseção com símbolo atual o commit não some da busca."""
    _seed_two_chunks(temp_storage)
    _publish_history(temp_storage, [_commit_record(touches=[])])

    outcome = temp_storage.search_hybrid(
        query_vector=MOCK_VECTOR,
        query_text="resolução de imports",
        filters={},
        top_k=10,
    )

    commits = [result for result in outcome.results if result.type == "commit"]
    assert [result.commit.id for result in commits] == ["a" * 40]
    assert temp_storage.get_history_state()["touches"] == 0


def _seed_many_chunks(storage, total=11):
    """
    `total` chunks com similaridade decrescente à `VEC_A`; os dois últimos são
    ortogonais, ficam fora dos `STRUCTURAL_SEED_TOP_N` e só podem ser ativados
    por vizinhança — que é o que torna a barreira da GA-05 observável.
    """
    chunks = []
    for index in range(total):
        ortogonal = index >= total - 1
        vector = [0.0, 1.0] + [0.0] * 382 if ortogonal else [1.0, 0.01 * index] + [0.0] * 382
        chunks.append(
            CodeChunk(
                id=f"c{index}",
                file_path=f"src/m{index}.py",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name=f"sym{index}",
                language="python",
                content=f"def sym{index}(): return {index}",
                indexed_at="2026-06-05T12:00:00Z",
                vector=vector,
            )
        )
    storage.store_chunks(chunks)
    return chunks


def test_braco_estrutural_nao_semeia_nem_atravessa_a_camada_historica(
    temp_storage, monkeypatch
):
    """
    GA-05: com histórico publicado e projetado no grafo, o commit não pode servir
    de ponte no `spreading_activation`. `sym10` não é semente e não tem nenhuma
    aresta de código — só o `touches` o alcançaria.
    """
    import json

    chunks = _seed_many_chunks(temp_storage)
    _publish_history(temp_storage, [_commit_record(vector=VEC_A)])
    monkeypatch.delenv("ATLAS_RERANK_MODEL", raising=False)

    commit_node = "commit:test-project:" + "a" * 40
    nodes = [
        {
            "id": f"sym:{chunk.file_path}#{chunk.scope_name}",
            "kind": "symbol",
            "label": chunk.scope_name,
            "file_path": chunk.file_path,
            "degree": 1,
        }
        for chunk in chunks
    ]
    nodes.append(
        {"id": commit_node, "kind": "commit", "label": "Corrige resolução de imports",
         "file_path": None, "degree": 0}
    )
    # O commit é a ÚNICA ligação entre `sym0` (semente) e `sym10` (fora das sementes)
    edges = [
        {"source": commit_node, "target": "sym:src/m0.py#sym0", "kind": "touches"},
        {"source": commit_node, "target": "sym:src/m10.py#sym10", "kind": "touches"},
    ]
    (temp_storage.index_dir / "graph.json").write_text(
        json.dumps({"nodes": nodes, "edges": edges}), encoding="utf-8"
    )

    outcome = temp_storage.search_hybrid(
        query_vector=VEC_A, query_text="sym0", filters={}, top_k=12, structural=True
    )
    arms = {r.scope_name: r.match_arms for r in outcome.results if r.type == "code"}

    # Positivo: a semente é ativada, então o braço `graph` está de fato ligado
    assert "graph" in arms["sym0"]
    # O que M5 quebra: sem o filtro, o commit faz a ponte e ativa `sym10`
    assert "graph" not in arms["sym10"]
    assert all(
        "graph" not in r.match_arms for r in outcome.results if r.type == "commit"
    )


def test_commit_com_score_acima_do_codigo_nao_desloca_o_top_k(temp_storage):
    """
    CA13: o fallback de slots — não o acaso da fixture — mantém o commit fora das
    vagas de código. A query casa a mensagem do commit nos dois braços históricos
    e só o braço vetorial do código, então o commit VENCE no score; a união por
    score o poria em primeiro, o fallback o põe na vaga restante.
    """
    _seed_two_chunks(temp_storage)
    _publish_history(temp_storage, [_commit_record(vector=VEC_A)])

    query = {"query_vector": VEC_A, "query_text": "resolução de imports", "filters": {}}
    cheio = temp_storage.search_hybrid(**query, top_k=2)
    com_vaga = temp_storage.search_hybrid(**query, top_k=3)

    # Pré-condição da fixture: sem ela o teste volta a ser tautológico
    scores = {tipo: [r.score for r in com_vaga.results if r.type == tipo] for tipo in ("code", "commit")}
    assert max(scores["commit"]) > max(scores["code"])

    # Metade 1 do contrato: a ordem do código é idêntica à de antes da F5.1
    assert [(r.type, r.scope_name) for r in cheio.results] == [
        ("code", "main"),
        ("code", "helper"),
    ]
    # Metade 2: o commit entra apenas na vaga que sobra, no fim
    assert [(r.type, r.scope_name) for r in com_vaga.results[:2]] == [
        ("code", "main"),
        ("code", "helper"),
    ]
    assert com_vaga.results[2].type == "commit"
    assert com_vaga.results[2].commit.id == "a" * 40
