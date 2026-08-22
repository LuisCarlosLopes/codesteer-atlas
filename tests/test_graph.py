import json
from unittest.mock import patch

import pytest

from codesteer_atlas.graph import (
    bfs_path,
    build_and_write,
    build_and_write_incremental,
    explain,
    hubs,
    load_graph,
    resolve_node,
)
from codesteer_atlas.models import CodeChunk
from codesteer_atlas.storage import StorageBackend

MOCK_VECTOR = [0.0] * 384


@pytest.fixture
def temp_storage(tmp_path):
    return StorageBackend(index_dir=tmp_path)


def _make_base_graph_index(temp_storage):
    chunks = [
        CodeChunk(
            id="sym-a",
            file_path="pkg/a.py",
            repo="test-project",
            start_line=1,
            end_line=8,
            scope_type="function",
            scope_name="run",
            language="python",
            content="def run():\n    pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
            references=["cite:dec-002", "why:cache evita lookup"],
        ),
        CodeChunk(
            id="sym-b",
            file_path="pkg/b.py",
            repo="test-project",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="helper",
            language="python",
            content="def helper():\n    pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="sec-overview",
            file_path="docs/overview.md",
            repo="test-project",
            start_line=1,
            end_line=4,
            scope_type="section",
            scope_name="Overview",
            language="markdown",
            content="# Overview\n\nVeja [decisão](dec-002-resolucao.md).",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="sec-dec",
            file_path="docs/dec-002-resolucao.md",
            repo="test-project",
            start_line=1,
            end_line=4,
            scope_type="section",
            scope_name="Decisão 002",
            language="markdown",
            content="# Decisão 002\n\nDetalhes.",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
    ]
    temp_storage.store_chunks(chunks)
    manifest = temp_storage.get_manifest().model_copy(
        update={
            "files": {
                "pkg/a.py": "sha-a",
                "pkg/b.py": "sha-b",
                "docs/overview.md": "sha-c",
                "docs/dec-002-resolucao.md": "sha-d",
            },
            "files_imports": {"pkg/a.py": ["pkg.b", "os"]},
        }
    )
    return manifest


def test_build_generates_nodes_and_edges_for_all_core_kinds(temp_storage):
    manifest = _make_base_graph_index(temp_storage)

    graph_path = build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    kinds = {node["kind"] for node in graph["nodes"]}
    edge_kinds = {edge["kind"] for edge in graph["edges"]}
    assert {"file", "doc", "symbol", "section", "rationale"} <= kinds
    assert {"contains", "links_to", "cites", "annotates", "imports"} <= edge_kinds


def test_unresolved_cite_does_not_create_ghost_node(temp_storage):
    temp_storage.store_chunks(
        [
            CodeChunk(
                id="c1",
                file_path="pkg/a.py",
                repo="test-project",
                start_line=1,
                end_line=3,
                scope_type="function",
                scope_name="run",
                language="python",
                content="def run():\n    pass",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
                references=["cite:dec-999"],
            )
        ]
    )
    manifest = temp_storage.get_manifest().model_copy(
        update={"files": {"pkg/a.py": "sha-a"}, "files_imports": {}}
    )

    graph = json.loads(build_and_write(temp_storage, manifest, temp_storage.index_dir).read_text())

    assert all(node["id"] != "file:dec-999" for node in graph["nodes"])
    assert not any(edge["kind"] == "cites" for edge in graph["edges"])


def test_workspace_without_markdown_still_produces_valid_graph_and_queries(temp_storage):
    temp_storage.store_chunks(
        [
            CodeChunk(
                id="a1",
                file_path="pkg/a.py",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name="run",
                language="python",
                content="def run():\n    pass",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
                references=["why:usa cache"],
            ),
            CodeChunk(
                id="b1",
                file_path="pkg/b.py",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name="helper",
                language="python",
                content="def helper():\n    pass",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
        ]
    )
    manifest = temp_storage.get_manifest().model_copy(
        update={
            "files": {"pkg/a.py": "sha-a", "pkg/b.py": "sha-b"},
            "files_imports": {"pkg/a.py": ["pkg.b"]},
        }
    )

    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)

    assert graph["metrics"]["node_count"] >= 5
    assert bfs_path(graph, "pkg/a.py", "pkg/b.py")["found"] is True
    assert explain(graph, "pkg/a.py")["notes"] == []


def test_hubs_degree_excludes_contains_and_is_sorted_desc(temp_storage):
    manifest = _make_base_graph_index(temp_storage)
    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)

    result = hubs(graph, 3)

    assert result == sorted(result, key=lambda item: (-item["degree"], item["id"]))
    assert result[0]["degree"] >= result[-1]["degree"]


def test_bfs_path_reports_edge_kinds_and_missing_path(temp_storage):
    manifest = _make_base_graph_index(temp_storage)
    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)

    found = bfs_path(graph, "pkg/a.py", "docs/dec-002-resolucao.md")
    missing = bfs_path(graph, "pkg/b.py", "docs/overview.md", max_hops=1)

    assert found["found"] is True
    assert any(step["edge_kind_to_next"] == "cites" for step in found["path"][:-1])
    assert missing["found"] is False


def test_resolve_node_prefers_id_then_label_then_unique_suffix_and_reports_ambiguity(temp_storage):
    manifest = _make_base_graph_index(temp_storage)
    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)

    assert resolve_node(graph, "file:pkg/a.py")["id"] == "file:pkg/a.py"
    assert resolve_node(graph, "Overview")["id"] == "sec:docs/overview.md#Overview"
    assert resolve_node(graph, "pkg/b.py")["id"] == "file:pkg/b.py"

    graph["_nodes_by_id"]["sym:pkg/other.py#helper"] = {
        "id": "sym:pkg/other.py#helper",
        "kind": "symbol",
        "label": "helper",
        "file_path": "pkg/other.py",
        "lines": [1, 2],
    }
    try:
        resolve_node(graph, "helper")
    except ValueError as e:
        assert "candidatos" in str(e).lower() or "candidates" in str(e).lower()
    else:
        raise AssertionError("Esperava ValueError para referência ambígua")


def test_graph_write_is_atomic_and_json_is_valid(temp_storage):
    manifest = _make_base_graph_index(temp_storage)

    graph_path = build_and_write(temp_storage, manifest, temp_storage.index_dir)

    assert graph_path.exists()
    assert not (temp_storage.index_dir / "graph.json.tmp").exists()
    assert json.loads(graph_path.read_text(encoding="utf-8"))["graph_version"] == "1.0"


def test_load_graph_reuses_process_cache(temp_storage):
    manifest = _make_base_graph_index(temp_storage)
    build_and_write(temp_storage, manifest, temp_storage.index_dir)

    first = load_graph(temp_storage.index_dir)

    with patch("builtins.open", side_effect=AssertionError("nao deveria reler graph.json")):
        second = load_graph(temp_storage.index_dir)

    assert first is second


def test_python_import_resolution_ignores_stdlib(temp_storage):
    manifest = _make_base_graph_index(temp_storage)
    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)

    import_edges = [edge for edge in graph["edges"] if edge["kind"] == "imports"]

    assert import_edges == [{"source": "file:pkg/a.py", "target": "file:pkg/b.py", "kind": "imports"}]


def test_relative_ts_imports_resolve_with_suffixes_and_bare_imports_are_ignored(temp_storage):
    temp_storage.store_chunks(
        [
            CodeChunk(
                id="ts-app",
                file_path="src/app.ts",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name="app",
                language="typescript",
                content="export const app = 1",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
            CodeChunk(
                id="ts-lib",
                file_path="src/lib.tsx",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name="lib",
                language="typescript",
                content="export const lib = 1",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
            CodeChunk(
                id="ts-index",
                file_path="src/dir/index.ts",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name="dir",
                language="typescript",
                content="export const dir = 1",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
        ]
    )
    manifest = temp_storage.get_manifest().model_copy(
        update={
            "files": {
                "src/app.ts": "sha-a",
                "src/lib.tsx": "sha-b",
                "src/dir/index.ts": "sha-c",
            },
            "files_imports": {"src/app.ts": ["./lib", "./dir", "react"]},
        }
    )

    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)
    import_targets = sorted(
        edge["target"] for edge in graph["edges"] if edge["kind"] == "imports"
    )

    assert import_targets == ["file:src/dir/index.ts", "file:src/lib.tsx"]


# --- Resolução de imports absolutos em layout src/ --------------------------


def test_infer_package_roots_detecta_src_layout():
    """A raiz de código é o pai do pacote mais externo, deduzida pelos __init__.py."""
    from codesteer_atlas.graph import infer_package_roots

    files = {
        "src/demo/__init__.py",
        "src/demo/core.py",
        "src/demo/sub/__init__.py",
        "src/demo/sub/util.py",
        "tests/test_core.py",
    }

    roots = infer_package_roots(files)

    assert "" in roots
    assert "src" in roots
    # Determinístico e da raiz mais rasa para a mais profunda
    assert roots == sorted(roots, key=lambda root: (len(root), root))


def test_infer_package_roots_em_layout_plano():
    """Sem `src/`, a raiz continua sendo a do workspace — nada de raiz inventada."""
    from codesteer_atlas.graph import infer_package_roots

    roots = infer_package_roots({"demo/__init__.py", "demo/core.py"})

    assert roots == [""]


def test_resolve_module_path_absoluto_em_src_layout():
    """
    `import demo.core` precisa casar com `src/demo/core.py`: as chaves do manifest são
    relativas ao workspace, então sem raiz de pacote nenhum import absoluto resolvia.
    """
    from codesteer_atlas.graph import resolve_module_path

    files = {"src/demo/__init__.py", "src/demo/core.py", "src/demo/sub/__init__.py"}

    assert resolve_module_path("demo.core", files) == "src/demo/core.py"
    assert resolve_module_path("demo", files) == "src/demo/__init__.py"
    assert resolve_module_path("demo.sub", files) == "src/demo/sub/__init__.py"
    assert resolve_module_path("inexistente.modulo", files) is None


def test_resolve_python_import_relativo_continua_funcionando():
    """Imports relativos se ancoram no próprio arquivo e não devem usar raízes."""
    from codesteer_atlas.graph import _resolve_python_import

    files = {"src/demo/core.py", "src/demo/util.py"}

    resolvido = _resolve_python_import("src/demo/core.py", ".util", files)

    assert resolvido == "src/demo/util.py"


def _make_src_layout_index(temp_storage):
    """Índice mínimo em layout `src/` com um import absoluto entre dois módulos."""
    chunks = [
        CodeChunk(
            id="sym-core",
            file_path="src/demo/core.py",
            repo="demo",
            start_line=1,
            end_line=4,
            scope_type="function",
            scope_name="run",
            language="python",
            content="def run():\n    return helper()",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="sym-util",
            file_path="src/demo/util.py",
            repo="demo",
            start_line=1,
            end_line=3,
            scope_type="function",
            scope_name="helper",
            language="python",
            content="def helper():\n    return 1",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
    ]
    temp_storage.store_chunks(chunks)
    return temp_storage.get_manifest().model_copy(
        update={
            "files": {
                "src/demo/__init__.py": "h0",
                "src/demo/core.py": "h1",
                "src/demo/util.py": "h2",
            },
            "files_imports": {"src/demo/core.py": ["demo.util"]},
        }
    )


def test_build_and_write_cria_aresta_imports_em_src_layout(temp_storage):
    """
    Regressão do bug que deixava o grafo praticamente sem arestas `imports`: um
    import absoluto num layout `src/` tem de virar aresta entre os dois arquivos.
    """
    manifest = _make_src_layout_index(temp_storage)
    graph_path = build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    import_edges = [edge for edge in graph["edges"] if edge["kind"] == "imports"]

    assert import_edges == [
        {
            "source": "file:src/demo/core.py",
            "target": "file:src/demo/util.py",
            "kind": "imports",
        }
    ]


def test_import_resolvido_gera_grau_para_ranqueamento(temp_storage):
    """
    A aresta `imports` tem de contar no `degree` (que ignora apenas `contains`), pois
    é dele que dependem os hubs do `atlas_graph` e o ranking do `atlas_brief`.
    """
    manifest = _make_src_layout_index(temp_storage)
    graph_path = build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    degrees = {node["id"]: node["degree"] for node in graph["nodes"]}

    assert degrees["file:src/demo/core.py"] == 1
    assert degrees["file:src/demo/util.py"] == 1


# ---------------------------------------------------------------------------
# Tetos honestos de hubs/explain (ADR-005 / RF08-RF10)
# ---------------------------------------------------------------------------


def _synthetic_graph(nodes, edges=None, top_hubs=None):
    """Grafo já finalizado, com `metrics.top_hubs` controlável à parte dos nós."""
    return {
        "nodes": nodes,
        "edges": edges or [],
        "metrics": {
            "node_count": len(nodes),
            "edge_count": len(edges or []),
            "top_hubs": top_hubs if top_hubs is not None else [],
        },
        "_nodes_by_id": {node["id"]: node for node in nodes},
        "_adjacency": {},
    }


def _wide_graph(total_nodes=60):
    """`total_nodes` arquivos com graus decrescentes e distintos."""
    return [
        {
            "id": f"file:pkg/mod_{i:03d}.py",
            "kind": "file",
            "label": f"mod_{i:03d}.py",
            "file_path": f"pkg/mod_{i:03d}.py",
            "degree": total_nodes - i,
        }
        for i in range(total_nodes)
    ]


def test_ca07_hubs_devolve_50_quando_o_grafo_tem_mais_de_50_nos():
    """
    CA07 — `metrics.top_hubs` é capado em GRAPH_TOP_HUBS_LIMIT (25). Se `hubs()`
    voltar a fatiá-lo, este teste devolve 25 e falha.
    """
    nodes = _wide_graph(60)
    graph = _synthetic_graph(nodes, top_hubs=[{"id": n["id"], "degree": n["degree"]} for n in nodes[:25]])

    result = hubs(graph, 50)

    assert len(result) == 50
    assert result[0]["id"] == "file:pkg/mod_000.py"
    assert result[0]["degree"] == 60
    assert result == sorted(result, key=lambda item: (-item["degree"], item["id"]))


def test_ca08_hubs_ate_25_e_identico_ao_pre_computado():
    """CA08 — abaixo do teto antigo, id e degree têm de bater com `metrics.top_hubs`."""
    nodes = _wide_graph(60)
    top_hubs = [{"id": n["id"], "degree": n["degree"]} for n in nodes[:25]]
    graph = _synthetic_graph(nodes, top_hubs=top_hubs)

    result = hubs(graph, 25)

    assert [(item["id"], item["degree"]) for item in result] == [
        (item["id"], item["degree"]) for item in top_hubs
    ]


def test_hubs_top_n_maior_que_o_grafo_devolve_o_que_existe():
    graph = _synthetic_graph(_wide_graph(3))
    assert len(hubs(graph, 50)) == 3


def test_hubs_desempata_por_id_quando_o_grau_empata():
    nodes = [
        {"id": "file:z.py", "kind": "file", "label": "z.py", "file_path": "z.py", "degree": 7},
        {"id": "file:a.py", "kind": "file", "label": "a.py", "file_path": "a.py", "degree": 7},
    ]
    result = hubs(_synthetic_graph(nodes), 2)
    assert [item["id"] for item in result] == ["file:a.py", "file:z.py"]


def _hub_with_neighbors(symbol_count, note_count=0):
    """Um nó central ligado a `symbol_count` símbolos e `note_count` docs."""
    center = {"id": "file:pkg/big.py", "kind": "file", "label": "big.py", "file_path": "pkg/big.py", "degree": symbol_count}
    nodes = [center]
    adjacency = []
    for i in range(symbol_count):
        nodes.append(
            {
                "id": f"sym:pkg/big.py#fn_{i:03d}",
                "kind": "symbol",
                "label": f"fn_{i:03d}",
                "file_path": "pkg/big.py",
                "lines": [i + 1, i + 2],
                "degree": 1,
            }
        )
        adjacency.append((f"sym:pkg/big.py#fn_{i:03d}", "contains"))
    for i in range(note_count):
        nodes.append(
            {
                "id": f"file:docs/nota_{i:03d}.md",
                "kind": "doc",
                "label": f"nota_{i:03d}.md",
                "file_path": f"docs/nota_{i:03d}.md",
                "lines": None,
                "degree": 1,
            }
        )
        adjacency.append((f"file:docs/nota_{i:03d}.md", "links_to"))

    graph = _synthetic_graph(nodes)
    graph["_adjacency"] = {center["id"]: adjacency}
    return graph


def test_ca09_explain_capa_vizinhos_por_kind_e_conta_o_omitido():
    """CA09 — 100 vizinhos `symbol` viram 25, e `omitted` reporta os 75 restantes."""
    graph = _hub_with_neighbors(symbol_count=100)

    result = explain(graph, "file:pkg/big.py")

    assert len(result["neighbors"]["symbol"]) == 25
    assert result["omitted"]["symbol"] == 75
    # o corte vem depois da ordenação: os 25 primeiros são os menores rótulos
    assert result["neighbors"]["symbol"][0]["label"] == "fn_000"
    assert result["neighbors"]["symbol"][-1]["label"] == "fn_024"


def test_ca10_explain_sem_truncamento_devolve_omitted_vazio():
    """CA10 — `omitted` está sempre no payload; vazio quando nada foi cortado."""
    graph = _hub_with_neighbors(symbol_count=3)

    result = explain(graph, "file:pkg/big.py")

    assert result["omitted"] == {}
    assert len(result["neighbors"]["symbol"]) == 3


def test_explain_capa_notes_no_proprio_teto():
    """`notes` tem teto próprio (15), menor que o de vizinhos por kind."""
    graph = _hub_with_neighbors(symbol_count=1, note_count=20)

    result = explain(graph, "file:pkg/big.py")

    assert len(result["notes"]) == 15
    assert result["omitted"]["notes"] == 5


def test_explain_e_estavel_entre_chamadas():
    """Corte depois da ordenação: duas chamadas sobre o mesmo grafo dão o mesmo corte."""
    graph = _hub_with_neighbors(symbol_count=40)
    assert explain(graph, "file:pkg/big.py") == explain(graph, "file:pkg/big.py")


# ---------------------------------------------------------------------------
# Arestas calls: escada de resolução e paridade incremental (ADR-003/004)
# ---------------------------------------------------------------------------


def _sym(file_path, scope_name, calls=None, language="python", lines=(1, 5)):
    return CodeChunk(
        id=f"{file_path}#{scope_name}",
        file_path=file_path,
        repo="test-project",
        start_line=lines[0],
        end_line=lines[1],
        scope_type="function",
        scope_name=scope_name,
        language=language,
        content=f"def {scope_name}():\n    pass",
        indexed_at="2026-06-05T12:00:00Z",
        vector=MOCK_VECTOR,
        calls=list(calls or []),
    )


def _build(temp_storage, chunks, files_imports=None):
    temp_storage.store_chunks(chunks)
    files = {chunk.file_path: f"sha-{chunk.file_path}" for chunk in chunks}
    manifest = temp_storage.get_manifest().model_copy(
        update={"files": files, "files_imports": files_imports or {}}
    )
    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    return load_graph(temp_storage.index_dir), manifest


def _call_edges(graph):
    return sorted(
        (edge["source"], edge["target"], edge.get("resolution"))
        for edge in graph["edges"]
        if edge["kind"] == "calls"
    )


def test_ca13_degrau_1_mesmo_arquivo_gera_exact(temp_storage):
    """CA13 — chamada intra-arquivo é fato estrutural: `exact`."""
    graph, _ = _build(
        temp_storage,
        [
            _sym("pkg/a.py", "run", calls=["helper"], lines=(1, 5)),
            _sym("pkg/a.py", "helper", lines=(7, 9)),
        ],
    )

    assert _call_edges(graph) == [("sym:pkg/a.py#run", "sym:pkg/a.py#helper", "exact")]


def test_ca14_degrau_2_via_import_gera_exact(temp_storage):
    """CA14 — o import resolvido a um path do manifest é evidência estrutural."""
    graph, _ = _build(
        temp_storage,
        [
            _sym("pkg/a.py", "run", calls=["helper"]),
            _sym("pkg/b.py", "helper"),
        ],
        files_imports={"pkg/a.py": ["pkg.b"]},
    )

    assert _call_edges(graph) == [("sym:pkg/a.py#run", "sym:pkg/b.py#helper", "exact")]


def test_degrau_2_exige_import_de_verdade(temp_storage):
    """Sem o import, o mesmo par cai para o degrau 3 e vira `inferred`."""
    graph, _ = _build(
        temp_storage,
        [
            _sym("pkg/a.py", "run", calls=["helper"]),
            _sym("pkg/b.py", "helper"),
        ],
        files_imports={},
    )

    assert _call_edges(graph) == [("sym:pkg/a.py#run", "sym:pkg/b.py#helper", "inferred")]


def test_ca24_degrau_3_unico_no_grafo_gera_inferred(temp_storage):
    """CA24 — nome único no grafo é pista, e a aresta sai carimbada como tal."""
    graph, _ = _build(
        temp_storage,
        [
            _sym("pkg/a.py", "run", calls=["so_existe_um"]),
            _sym("pkg/z.py", "so_existe_um"),
        ],
    )

    assert _call_edges(graph) == [("sym:pkg/a.py#run", "sym:pkg/z.py#so_existe_um", "inferred")]


def test_ca23_nome_ambiguo_e_descartado_sem_desempate(temp_storage):
    """
    CA23 — dois portadores do mesmo nome curto: nenhuma aresta. Ambiguidade nunca
    é desempatada por heurística — mesma política de `_resolve_note_matches`.
    """
    graph, _ = _build(
        temp_storage,
        [
            _sym("pkg/a.py", "run", calls=["duplicado"]),
            _sym("pkg/b.py", "duplicado"),
            _sym("pkg/c.py", "duplicado"),
        ],
    )

    assert _call_edges(graph) == []


def test_a3_casamento_e_exato_nunca_por_prefixo(temp_storage):
    """
    A3 — `_resolve_note_matches` casa por prefixo (`get` acharia `get-manifest`).
    O casamento de símbolo é por igualdade exata: `get` não pode casar
    `get_manifest`.
    """
    graph, _ = _build(
        temp_storage,
        [
            _sym("pkg/a.py", "run", calls=["buscar"]),
            _sym("pkg/b.py", "buscar_manifesto"),
        ],
    )

    assert _call_edges(graph) == []


def test_casamento_e_sensivel_a_caixa(temp_storage):
    """R-CALL-06 — comparação case-sensitive; só o filtro de ruído usa casefold."""
    graph, _ = _build(
        temp_storage,
        [
            _sym("pkg/a.py", "run", calls=["Processar"]),
            _sym("pkg/b.py", "processar"),
        ],
    )

    assert _call_edges(graph) == []


def test_casa_pelo_ultimo_segmento_do_scope_name(temp_storage):
    """`StorageBackend.store_chunks` é candidato para uma chamada a `store_chunks`."""
    graph, _ = _build(
        temp_storage,
        [
            _sym("pkg/a.py", "run", calls=["store_chunks"]),
            _sym("pkg/b.py", "StorageBackend.store_chunks"),
        ],
    )

    assert _call_edges(graph) == [
        ("sym:pkg/a.py#run", "sym:pkg/b.py#StorageBackend.store_chunks", "inferred")
    ]


def test_ca28_recursao_nao_gera_auto_aresta(temp_storage):
    """CA28 — `source == target` é descartado pela regra de dedup existente."""
    graph, _ = _build(temp_storage, [_sym("pkg/a.py", "run", calls=["run"])])

    assert _call_edges(graph) == []


def test_ca32_ruido_impede_aresta_mesmo_com_import_resolvivel(temp_storage):
    """
    CA32 — custo declarado de R-CALL-04: o filtro precede a escada, então uma
    chamada verdadeira a um símbolo homônimo de um nome da lista some em TODOS os
    degraus. `exists` é o caso real do repositório.

    O chunk aqui já chega sem `exists` porque a extração o descartou; o teste
    fixa que a resolução não o reintroduz por outro caminho.
    """
    graph, _ = _build(
        temp_storage,
        [
            _sym("pkg/a.py", "run", calls=[]),
            _sym("pkg/b.py", "StorageBackend.exists"),
        ],
        files_imports={"pkg/a.py": ["pkg.b"]},
    )

    assert _call_edges(graph) == []


def test_ca29_arquivos_sem_simbolo_ast_nao_originam_calls(temp_storage):
    """CA29 — markdown entra no grafo como seção, nunca como chamador."""
    graph, _ = _build(
        temp_storage,
        [
            CodeChunk(
                id="md1",
                file_path="docs/nota.md",
                repo="test-project",
                start_line=1,
                end_line=3,
                scope_type="section",
                scope_name="Titulo",
                language="markdown",
                content="# Titulo\n\nchamar()\n",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
            _sym("pkg/a.py", "chamar"),
        ],
    )

    assert _call_edges(graph) == []


def test_resolution_so_existe_em_arestas_calls(temp_storage):
    """O atributo é exclusivo do kind novo; os demais mantêm o shape antigo."""
    graph, _ = _build(
        temp_storage,
        [
            _sym("pkg/a.py", "run", calls=["helper"], lines=(1, 5)),
            _sym("pkg/a.py", "helper", lines=(7, 9)),
        ],
    )

    for edge in graph["edges"]:
        if edge["kind"] == "calls":
            assert edge["resolution"] in {"exact", "inferred"}
        else:
            assert "resolution" not in edge


def test_ca12_path_atravessa_arestas_calls(temp_storage):
    """
    CA12 — o caminho só existe por `calls`. Sem essas arestas, o mesmo fixture
    (mesmos nós, contains intacto) devolve `found: false`.
    """
    chunks = [
        _sym("pkg/a.py", "run", calls=["meio"], lines=(1, 5)),
        _sym("pkg/b.py", "meio", calls=["fim"], lines=(1, 5)),
        _sym("pkg/c.py", "fim", lines=(1, 5)),
    ]
    graph, _ = _build(temp_storage, chunks)

    resultado = bfs_path(graph, "sym:pkg/a.py#run", "sym:pkg/c.py#fim")

    assert resultado["found"] is True
    assert resultado["hops"] == 2
    assert [passo["edge_kind_to_next"] for passo in resultado["path"]] == ["calls", "calls", None]

    sem_calls = [
        _sym("pkg/a.py", "run", lines=(1, 5)),
        _sym("pkg/b.py", "meio", lines=(1, 5)),
        _sym("pkg/c.py", "fim", lines=(1, 5)),
    ]
    graph_sem, _ = _build(temp_storage, sem_calls)

    assert bfs_path(graph_sem, "sym:pkg/a.py#run", "sym:pkg/c.py#fim")["found"] is False


def test_ca17_tabela_sem_calls_json_nao_derruba_o_rebuild(temp_storage):
    """
    CA17 — índice anterior a esta feature não tem a coluna. A projeção degrada
    para zero chamadas em vez de estourar. [RE07]
    """
    temp_storage.store_chunks([_sym("pkg/a.py", "run", calls=["helper"])])
    linhas = temp_storage.get_graph_projection()
    for linha in linhas:
        linha.pop("calls_json", None)

    with patch.object(temp_storage, "get_graph_projection", return_value=linhas):
        manifest = temp_storage.get_manifest().model_copy(
            update={"files": {"pkg/a.py": "sha-a"}, "files_imports": {}}
        )
        build_and_write(temp_storage, manifest, temp_storage.index_dir)

    assert _call_edges(load_graph(temp_storage.index_dir)) == []


def test_ca17_tabela_sem_calls_json_nao_derruba_o_incremental(temp_storage):
    """
    CA17 — o caminho incremental lê a mesma projeção. Sem a coluna, descarta as
    `calls` anteriores e não recria nenhuma — sem crash.
    """
    inicial = [
        _sym("pkg/a.py", "run", calls=["helper"]),
        _sym("pkg/b.py", "helper"),
    ]
    graph, manifest = _build(temp_storage, inicial)
    assert _call_edges(graph) == [("sym:pkg/a.py#run", "sym:pkg/b.py#helper", "inferred")]

    linhas = temp_storage.get_graph_projection()
    for linha in linhas:
        linha.pop("calls_json", None)

    with patch.object(temp_storage, "get_graph_projection", return_value=linhas):
        build_and_write_incremental(
            index_path=temp_storage.index_dir,
            manifest=manifest,
            updated_chunks=[],
            updated_file_paths=set(),
            storage=temp_storage,
        )

    assert _call_edges(load_graph(temp_storage.index_dir)) == []


def test_ca27_incremental_re_resolve_chamadores_nao_alterados(temp_storage):
    """
    CA27 — o teste que pega a regressão de R-CALL-12.

    `pkg/a.py` não é tocado, mas `pkg/c.py` ganha um homônimo de um nome antes
    único. A aresta `inferred` que dependia daquela unicidade tem de sumir, e o
    conjunto final — pares E `resolution` — tem de bater com o de um rebuild
    completo sobre o mesmo índice. Se a resolução usasse apenas `updated_chunks`,
    a aresta velha sobreviveria e este teste falharia.
    """
    inicial = [
        _sym("pkg/a.py", "run", calls=["alvo"]),
        _sym("pkg/b.py", "alvo"),
        _sym("pkg/c.py", "outro"),
    ]
    graph, manifest = _build(temp_storage, inicial)
    assert _call_edges(graph) == [("sym:pkg/a.py#run", "sym:pkg/b.py#alvo", "inferred")]

    # pkg/c.py passa a carregar um segundo `alvo`; pkg/a.py fica intocado
    novo_c = _sym("pkg/c.py", "alvo", lines=(20, 24))
    temp_storage.delete_by_file_paths(["pkg/c.py"])
    temp_storage.append_chunks([novo_c])

    build_and_write_incremental(
        index_path=temp_storage.index_dir,
        manifest=manifest,
        updated_chunks=[novo_c],
        updated_file_paths={"pkg/c.py"},
        storage=temp_storage,
    )
    incremental = _call_edges(load_graph(temp_storage.index_dir))

    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    rebuild = _call_edges(load_graph(temp_storage.index_dir))

    assert ("sym:pkg/a.py#run", "sym:pkg/b.py#alvo", "inferred") not in incremental
    assert incremental == rebuild


def test_ca26_incremental_nao_transporta_calls_do_grafo_anterior(temp_storage):
    """RF17 — nenhuma aresta `calls` sobrevive a uma escrita; todas são recriadas."""
    inicial = [
        _sym("pkg/a.py", "run", calls=["helper"]),
        _sym("pkg/b.py", "helper"),
    ]
    graph, manifest = _build(temp_storage, inicial, files_imports={"pkg/a.py": ["pkg.b"]})
    assert len(_call_edges(graph)) == 1

    # `helper` desaparece do índice: a aresta não pode ser transportada
    temp_storage.delete_by_file_paths(["pkg/b.py"])
    manifest_sem_b = manifest.model_copy(update={"files": {"pkg/a.py": "sha-a"}})

    build_and_write_incremental(
        index_path=temp_storage.index_dir,
        manifest=manifest_sem_b,
        updated_chunks=[],
        updated_file_paths={"pkg/b.py"},
        storage=temp_storage,
    )

    assert _call_edges(load_graph(temp_storage.index_dir)) == []


def test_incremental_sem_storage_nao_inventa_subconjunto(temp_storage):
    """
    Sem a fonte global, o correto é ficar sem `calls` — um subconjunto resolvido
    só com os arquivos alterados divergiria do rebuild em silêncio. [A2]
    """
    inicial = [
        _sym("pkg/a.py", "run", calls=["helper"]),
        _sym("pkg/b.py", "helper"),
    ]
    _graph, manifest = _build(temp_storage, inicial, files_imports={"pkg/a.py": ["pkg.b"]})

    build_and_write_incremental(
        index_path=temp_storage.index_dir,
        manifest=manifest,
        updated_chunks=[],
        updated_file_paths={"pkg/b.py"},
    )

    assert _call_edges(load_graph(temp_storage.index_dir)) == []


def test_ca25_go_csharp_java_nao_produzem_exact_via_import(temp_storage):
    """
    CA25 — não existe resolvedor de import para `go`, `csharp` e `java` no
    repositório, então o degrau 2 nunca dispara nessas linguagens: o par cai para
    o degrau 3 e sai `inferred`, ou é descartado. Limite declarado, não defeito
    a corrigir nesta entrega. [R-CALL-09]

    Falha se alguém adicionar um resolvedor sem atualizar o contrato de RF14.
    """
    for language, extensao in (("go", "go"), ("csharp", "cs"), ("java", "java")):
        storage = StorageBackend(index_dir=temp_storage.index_dir / language)
        chamador = f"pkg/a.{extensao}"
        alvo = f"pkg/b.{extensao}"
        storage.store_chunks(
            [
                _sym(chamador, "Run", calls=["Alvo"], language=language),
                _sym(alvo, "Alvo", language=language),
            ]
        )
        manifest = storage.get_manifest().model_copy(
            update={
                "files": {chamador: "sha-a", alvo: "sha-b"},
                # mesmo declarando o import, não há resolvedor para essas linguagens
                "files_imports": {chamador: ["pkg/b"]},
            }
        )
        build_and_write(storage, manifest, storage.index_dir)
        arestas = _call_edges(load_graph(storage.index_dir))

        assert arestas == [(f"sym:{chamador}#Run", f"sym:{alvo}#Alvo", "inferred")], language


def test_python_e_typescript_alcancam_exact_via_import(temp_storage):
    """Contraponto de CA25: onde há resolvedor, o mesmo cenário sobe para `exact`."""
    for language, extensao, raw_import in (
        ("python", "py", "pkg.b"),
        ("typescript", "ts", "./b"),
    ):
        storage = StorageBackend(index_dir=temp_storage.index_dir / f"ok-{language}")
        chamador = f"pkg/a.{extensao}"
        alvo = f"pkg/b.{extensao}"
        storage.store_chunks(
            [
                _sym(chamador, "run", calls=["alvo"], language=language),
                _sym(alvo, "alvo", language=language),
            ]
        )
        manifest = storage.get_manifest().model_copy(
            update={
                "files": {chamador: "sha-a", alvo: "sha-b"},
                "files_imports": {chamador: [raw_import]},
            }
        )
        build_and_write(storage, manifest, storage.index_dir)
        arestas = _call_edges(load_graph(storage.index_dir))

        assert arestas == [(f"sym:{chamador}#run", f"sym:{alvo}#alvo", "exact")], language
