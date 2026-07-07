import pytest
import json
import lancedb
from codesteer_atlas.models import CodeChunk
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
    results = temp_storage.search_hybrid(query_vector=vec, query_text="main", filters={}, top_k=1)

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

    results = temp_storage.search_hybrid(query_vector=vec, query_text="def", filters={}, top_k=5)
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
    )

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
    )
    assert len(results) >= 1
    # Chunks c1 e c3 usam o mesmo vetor aproximado, c1 tem match FTS no termo 'login'
    assert results[0].scope_name == "login"

    # 2. Busca com filtro por repositório "project-b"
    results_repo = temp_storage.search_hybrid(
        query_vector=vec_auth, query_text="authenticated", filters={"repo": "project-b"}, top_k=5
    )
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
    )
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
    )

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
    )

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
    )

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
    )

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
    )

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
