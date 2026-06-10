import pytest
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
