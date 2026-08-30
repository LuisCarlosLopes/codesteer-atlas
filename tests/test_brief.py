"""Testes unitários do briefing pré-computado (`brief.py` / tool `atlas_brief`)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from codesteer_atlas.brief import (
    BRIEF_SCHEMA_VERSION,
    _clear_brief_cache,
    _has_main_guard,
    _layer_key,
    build_and_write_brief,
    build_brief,
    load_brief,
    render_brief,
)
from codesteer_atlas.config import (
    BRIEF_ENTRYPOINT_PROBE_LIMIT,
    BRIEF_LEVEL0_MAX_CHARS,
    BRIEF_LEVEL1_MAX_CHARS,
    BRIEF_MAX_LAYERS,
)
from codesteer_atlas.models import IndexManifest


@pytest.fixture(autouse=True)
def _reset_caches():
    """
    Limpa os caches de módulo antes e depois de cada teste: sem isto, o cache de
    processo do brief/grafo vaza entre testes e mascara regressões.
    """
    from codesteer_atlas.graph import _clear_graph_cache

    _clear_brief_cache()
    _clear_graph_cache()
    yield
    _clear_brief_cache()
    _clear_graph_cache()


def _make_manifest(files, **overrides) -> IndexManifest:
    payload = {
        "total_chunks": overrides.pop("total_chunks", len(files) * 3),
        "repos_indexed": overrides.pop("repos_indexed", ["demo"]),
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "embedding_dim": 384,
        "last_indexed_at": "2026-08-04T16:17:48.499369+00:00",
        "git_head_sha": overrides.pop("git_head_sha", "abc1234"),
        "languages_indexed": ["python"],
        "index_version": "2.1.0",
        "files": {path: "hash" for path in files},
    }
    payload.update(overrides)
    return IndexManifest(**payload)


def _graph(nodes, edges=None) -> dict:
    return {
        "graph_version": "1.0",
        "generated_at": "2026-08-04T16:17:48.499369+00:00",
        "workspace_repo": "demo",
        "nodes": nodes,
        "edges": edges or [],
        "metrics": {"node_count": len(nodes), "edge_count": len(edges or []), "top_hubs": []},
    }


def _file_node(path, degree=0, kind=None):
    return {
        "id": f"file:{path}",
        "kind": kind or ("doc" if path.endswith(".md") else "file"),
        "label": path.split("/")[-1],
        "file_path": path,
        "lines": None,
        "degree": degree,
    }


def _symbol_node(path, name, degree=0, end_line=10):
    return {
        "id": f"sym:{path}#{name}",
        "kind": "symbol",
        "label": name,
        "file_path": path,
        "lines": [1, end_line],
        "degree": degree,
    }


# ---------------------------------------------------------------------------
# Camadas
# ---------------------------------------------------------------------------


def test_layer_key_dobra_dois_niveis_em_container():
    """Diretórios-container viram camada de 2 níveis; o resto fica em 1 nível."""
    children = {"src": 1}
    assert _layer_key("src/pkg/x.py", children) == "src/pkg"
    assert _layer_key("a/b.py", children) == "a"
    assert _layer_key("x.py", children) == "(root)"


def test_layer_key_colapsa_container_com_muitos_filhos():
    """Container com filhos demais volta a ser camada única e emite aviso."""
    files = [f"packages/p{i}/mod.py" for i in range(20)]
    manifest = _make_manifest(files)
    brief = build_brief(manifest, None, Path("."))

    assert "layers_collapsed" in brief["warnings"]
    assert [layer["path"] for layer in brief["layers"]] == ["packages"]


def test_layers_limita_a_oito_e_reporta_truncamento():
    """A lista de camadas é capada e sinaliza explicitamente o que ficou de fora."""
    files = [f"dir{i}/mod.py" for i in range(20)]
    manifest = _make_manifest(files)
    brief = build_brief(manifest, None, Path("."))

    assert len(brief["layers"]) == BRIEF_MAX_LAYERS
    assert brief["layers_truncated"] == 20 - BRIEF_MAX_LAYERS


def test_role_de_camada_classifica_testes_e_docs():
    """`role` distingue testes, docs e código a partir dos segmentos do caminho."""
    manifest = _make_manifest(["tests/test_x.py", "docs/guia.md", "src/app/core.py"])
    brief = build_brief(manifest, None, Path("."))
    roles = {layer["path"]: layer["role"] for layer in brief["layers"]}

    assert roles["tests"] == "tests"
    assert roles["docs"] == "docs"
    assert roles["src/app"] == "source"


# ---------------------------------------------------------------------------
# Identidade
# ---------------------------------------------------------------------------


def test_identity_usa_arquivos_como_denominador():
    """
    A distribuição de linguagem é por arquivo, não por chunk: contagem de chunks é
    distorcida pela granularidade do chunker e reportaria a stack errada.
    """
    files = ["README.md"] + [f"src/app/m{i}.py" for i in range(5)]
    manifest = _make_manifest(files, total_chunks=500)
    brief = build_brief(manifest, None, Path("."))

    by_name = {lang["name"]: lang for lang in brief["identity"]["languages"]}
    assert by_name["markdown"]["pct"] == 17
    assert by_name["python"]["pct"] == 83
    assert brief["identity"]["primary_language"] == "python"


def test_identity_deriva_linguagem_da_extensao_sem_grafo():
    """A identidade não depende do grafo — vem da extensão, imune a falha do chunker."""
    files = ["src/app/a.py", "src/app/b.py", "notas.md"]
    manifest = _make_manifest(files)

    com_grafo = build_brief(manifest, _graph([]), Path("."))["identity"]["languages"]
    sem_grafo = build_brief(manifest, None, Path("."))["identity"]["languages"]

    assert com_grafo == sem_grafo


def test_warning_low_symbol_coverage():
    """Arquivos de código sem nenhum símbolo indexado indicam índice degradado."""
    manifest = _make_manifest(["src/app/a.py", "src/app/b.py"])
    brief = build_brief(manifest, _graph([_file_node("src/app/a.py")]), Path("."))

    assert "low_symbol_coverage" in brief["warnings"]


def test_identity_sinaliza_indice_multi_repo():
    """Camadas sobre a união de vários repos não são confiáveis — avisar."""
    manifest = _make_manifest(["a.py"], repos_indexed=["r1", "r2"])
    brief = build_brief(manifest, None, Path("."))

    assert "multi_repo_index" in brief["warnings"]
    assert brief["identity"]["repos"] == ["r1", "r2"]


# ---------------------------------------------------------------------------
# Entrypoints
# ---------------------------------------------------------------------------


def test_entrypoint_declarado_resolve_src_layout(tmp_path):
    """`modulo:attr` do pyproject resolve para `src/<pkg>/...`, que o grafo não cobre."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n[project.scripts]\ndemo-serve = "demo.server:main"\n',
        encoding="utf-8",
    )
    manifest = _make_manifest(["src/demo/server.py", "pyproject.toml"])
    brief = build_brief(manifest, None, tmp_path)

    entry = brief["entrypoints"][0]
    assert entry["file_path"] == "src/demo/server.py"
    assert entry["symbol"] == "main"
    assert entry["confidence"] == "declared"


def test_entrypoint_declarado_deduplica_aliases(tmp_path):
    """Aliases distintos para o mesmo alvo viram uma entrada só, com os nomes juntos."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n'
        '[project.scripts]\ndemo-serve = "demo.server:main"\nserver = "demo.server:main"\n',
        encoding="utf-8",
    )
    manifest = _make_manifest(["src/demo/server.py", "pyproject.toml"])
    brief = build_brief(manifest, None, tmp_path)

    assert len(brief["entrypoints"]) == 1
    assert brief["entrypoints"][0]["name"] == "demo-serve, server"


def test_entrypoint_package_json_bin_string_e_dict(tmp_path):
    """`bin` aceita string e dicionário; ambas as formas viram entrypoints declarados."""
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "bin": "./cli.js"}), encoding="utf-8"
    )
    manifest = _make_manifest(["cli.js", "package.json"])
    assert build_brief(manifest, None, tmp_path)["entrypoints"][0]["file_path"] == "cli.js"

    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "bin": {"a": "./a.js", "b": "./b.js"}}), encoding="utf-8"
    )
    manifest = _make_manifest(["a.js", "b.js", "package.json"])
    names = {item["name"] for item in build_brief(manifest, None, tmp_path)["entrypoints"]}
    assert names == {"a", "b"}


def test_entrypoint_nunca_vem_de_camada_de_testes(tmp_path):
    """Arquivo de teste com `__main__` não é entrypoint — é o erro clássico a evitar."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "server.py").write_text(
        'if __name__ == "__main__":\n    pass\n', encoding="utf-8"
    )
    manifest = _make_manifest(["tests/server.py"])
    brief = build_brief(manifest, None, tmp_path)

    assert brief["entrypoints"] == []


def test_entrypoint_inferido_exige_main_guard(tmp_path):
    """Candidato pelo nome só entra se o probe confirmar o guard — senão é descartado."""
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "cli.py").write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    manifest = _make_manifest(["app.py", "cli.py"])
    brief = build_brief(manifest, None, tmp_path)

    assert [item["file_path"] for item in brief["entrypoints"]] == ["cli.py"]
    assert brief["entrypoints"][0]["confidence"] == "inferred"


def test_entrypoint_probe_limita_arquivos_lidos(tmp_path):
    """
    Guarda de regressão O(n): a verificação de entrypoints nunca pode abrir um arquivo
    por item do repositório, independentemente do número de candidatos.
    """
    files = [f"pkg{i}/app.py" for i in range(5000)]
    manifest = _make_manifest(files)

    with patch("codesteer_atlas.brief._has_main_guard", return_value=False) as mock_probe:
        build_brief(manifest, None, tmp_path)

    assert mock_probe.call_count <= BRIEF_ENTRYPOINT_PROBE_LIMIT


def test_has_main_guard_detecta_python_go_e_csharp(tmp_path):
    """O probe reconhece as três formas de entrypoint suportadas."""
    py = tmp_path / "a.py"
    py.write_text('x = 1\nif __name__ == "__main__":\n    pass\n', encoding="utf-8")
    go = tmp_path / "b.go"
    go.write_text("package main\n\nfunc main() {\n}\n", encoding="utf-8")
    plain = tmp_path / "c.py"
    plain.write_text("x = 1\n", encoding="utf-8")

    assert _has_main_guard(py) is True
    assert _has_main_guard(go) is True
    assert _has_main_guard(plain) is False


# ---------------------------------------------------------------------------
# Hubs
# ---------------------------------------------------------------------------


def test_hubs_ignora_secoes_e_rationale():
    """`metrics.top_hubs` é capado antes de filtrar; recalcular remove o ruído."""
    nodes = [
        {"id": "sec:doc.md#T", "kind": "section", "label": "T", "file_path": "doc.md", "degree": 99},
        {"id": "rat:abc", "kind": "rationale", "label": "why", "file_path": "a.py", "degree": 80},
        _file_node("core.py", degree=5),
    ]
    hubs = build_brief(_make_manifest(["core.py", "doc.md"]), _graph(nodes), Path("."))["hubs"]

    assert [hub["kind"] for hub in hubs] == ["file"]
    assert hubs[0]["label"] == "core.py"


def test_compute_hubs_uses_shared_is_noise_hub():
    """Label json/Path não ranqueia; um file real de grau menor permanece."""
    nodes = [
        _symbol_node("lib/codec.py", "json", degree=50),
        _symbol_node("lib/paths.py", "Path", degree=40),
        _file_node("core.py", degree=5),
        {"id": "sec:doc.md#T", "kind": "section", "label": "T", "file_path": "doc.md", "degree": 99},
    ]
    hubs = build_brief(
        _make_manifest(["lib/codec.py", "lib/paths.py", "core.py", "doc.md"]),
        _graph(nodes),
        Path("."),
    )["hubs"]

    labels = {hub["label"] for hub in hubs}
    assert "json" not in labels
    assert "Path" not in labels
    assert "core.py" in labels


def test_hubs_vazio_quando_sem_arestas_cruzadas():
    """Sem conectividade real, a resposta é lista vazia — não uma ordem alfabética."""
    nodes = [_file_node("a.py", degree=0), _file_node("b.py", degree=0)]
    brief = build_brief(_make_manifest(["a.py", "b.py"]), _graph(nodes), Path("."))

    assert brief["hubs"] == []


# ---------------------------------------------------------------------------
# Degradação
# ---------------------------------------------------------------------------


def test_build_brief_sem_grafo_degrada_sem_excecao(tmp_path):
    """Sem graph.json a tool continua útil: nada de exceção, e o que falta é declarado."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n[project.scripts]\nd = "demo.server:main"\n', encoding="utf-8"
    )
    manifest = _make_manifest(["src/demo/server.py", "pyproject.toml"])
    brief = build_brief(manifest, None, tmp_path)

    assert brief["degraded"] is True
    assert "graph_unavailable" in brief["warnings"]
    assert brief["hubs"] == []
    # Ausência de dado não pode ser lida como medição de zero
    assert "symbols" not in brief["identity"]
    assert "top" not in brief["layers"][0]
    assert brief["entrypoints"]


def test_rank_basis_reporta_criterio_efetivo():
    """O consumidor precisa saber se o ranking veio de grau, símbolos ou nome."""
    nodes = [_file_node("src/app/a.py"), _symbol_node("src/app/a.py", "f")]
    brief = build_brief(_make_manifest(["src/app/a.py"]), _graph(nodes), Path("."))

    assert brief["layers"][0]["rank_basis"] == "symbols"


# ---------------------------------------------------------------------------
# Persistência e cache
# ---------------------------------------------------------------------------


def test_persist_brief_e_atomico(tmp_path):
    """A escrita usa arquivo temporário + os.replace; o .tmp não sobrevive."""
    manifest = _make_manifest(["a.py"])
    build_and_write_brief(manifest, tmp_path, tmp_path)

    assert (tmp_path / "brief.json").exists()
    assert not (tmp_path / "brief.json.tmp").exists()


def test_brief_json_persiste_git_sha_e_index_version(tmp_path):
    """Metadados que o graph.json não guarda — necessários para reportar staleness."""
    manifest = _make_manifest(["a.py"], git_head_sha="deadbeef")
    build_and_write_brief(manifest, tmp_path, tmp_path)
    payload = json.loads((tmp_path / "brief.json").read_text(encoding="utf-8"))

    assert payload["git_head_sha"] == "deadbeef"
    assert payload["index_version"] == "2.1.0"
    assert payload["brief_version"] == BRIEF_SCHEMA_VERSION


def test_load_brief_invalida_cache_por_mtime(tmp_path):
    """Reescrever o arquivo tem de invalidar o cache de processo."""
    import os

    manifest = _make_manifest(["a.py"], git_head_sha="sha-1")
    build_and_write_brief(manifest, tmp_path, tmp_path)
    assert load_brief(tmp_path)["git_head_sha"] == "sha-1"

    manifest2 = _make_manifest(["a.py"], git_head_sha="sha-2")
    build_and_write_brief(manifest2, tmp_path, tmp_path)
    path = tmp_path / "brief.json"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert load_brief(tmp_path)["git_head_sha"] == "sha-2"


def test_load_brief_retorna_none_quando_ausente_ou_incompativel(tmp_path):
    """Ausência e versão desconhecida devolvem None para o chamador recomputar."""
    assert load_brief(tmp_path) is None

    (tmp_path / "brief.json").write_text(json.dumps({"brief_version": "0.1"}), encoding="utf-8")
    assert load_brief(tmp_path) is None


def test_brief_determinista(tmp_path):
    """Mesma entrada tem de produzir exatamente os mesmos bytes."""
    manifest = _make_manifest(["src/app/a.py", "src/app/b.py", "docs/x.md"])
    graph = _graph([_file_node("src/app/a.py", degree=2), _symbol_node("src/app/a.py", "f")])

    first = json.dumps(build_brief(manifest, graph, tmp_path), sort_keys=True)
    second = json.dumps(build_brief(manifest, graph, tmp_path), sort_keys=True)

    assert first == second


# ---------------------------------------------------------------------------
# Orçamento de tokens
# ---------------------------------------------------------------------------


def _pathological_manifest() -> IndexManifest:
    files = []
    for d in range(40):
        for f in range(50):
            deep = "/".join(f"nivel{d}{i:03d}" for i in range(6))
            files.append(f"dir{d}/{deep}/arquivo_com_nome_muito_longo_{f:04d}.py")
    return _make_manifest(files)


def test_level0_e_level1_respeitam_orcamento_de_chars():
    """
    O teto é a propriedade central da tool: um repositório patológico (2000 arquivos,
    40 diretórios, caminhos longuíssimos) tem de caber no mesmo orçamento.
    """
    manifest = _pathological_manifest()
    nodes = [_file_node(path, degree=7) for path in list(manifest.files)[:400]]
    brief = build_brief(manifest, _graph(nodes), Path("."))

    for level, cap in ((0, BRIEF_LEVEL0_MAX_CHARS), (1, BRIEF_LEVEL1_MAX_CHARS)):
        payload = render_brief(brief, level, current_git_sha="abc1234")
        serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        assert len(serialized) <= cap, f"level={level} estourou: {len(serialized)} > {cap}"


def test_render_brief_trunca_e_avisa_ao_exceder():
    """Quando corta para caber, o corte é declarado — não silencioso."""
    manifest = _pathological_manifest()
    nodes = [_file_node(path, degree=7) for path in list(manifest.files)[:400]]
    brief = build_brief(manifest, _graph(nodes), Path("."))
    payload = render_brief(brief, 1, current_git_sha="abc1234")

    assert "truncated_for_budget" in payload["warnings"]


def test_render_brief_reporta_staleness_sem_gravar():
    """`is_stale` é calculado na chamada, comparando o SHA gravado com o HEAD atual."""
    brief = build_brief(_make_manifest(["a.py"], git_head_sha="sha-antigo"), None, Path("."))

    atual = render_brief(brief, 1, current_git_sha="sha-antigo")
    mudou = render_brief(brief, 1, current_git_sha="sha-novo")

    assert atual["is_stale"] is False
    assert mudou["is_stale"] is True
    assert mudou["stale_reason"] == "git_head_changed"
    assert "index_stale" in mudou["warnings"]


def test_level0_e_subconjunto_de_level1():
    """O nível 0 é projeção estrita do 1: mesmos números, menos campos."""
    manifest = _make_manifest(["src/app/a.py", "docs/x.md"])
    brief = build_brief(manifest, _graph([_file_node("src/app/a.py", degree=1)]), Path("."))

    level0 = render_brief(brief, 0, current_git_sha="abc1234")
    level1 = render_brief(brief, 1, current_git_sha="abc1234")

    assert level0["identity"]["files"] == level1["identity"]["files"]
    assert level0["identity"]["repo"] == level1["identity"]["repo"]
    assert [layer["path"] for layer in level0["layers"]] == [
        layer["path"] for layer in level1["layers"]
    ]
