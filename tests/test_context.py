"""Testes do pacote de contexto por tarefa (`context.py` / atlas_context)."""

import json
import subprocess
import sys

import pytest

from codesteer_atlas import context as ctxmod
from codesteer_atlas.context import (
    INTENT_SECTIONS,
    apply_section_quotas,
    build_context,
    discover_tests,
)
from codesteer_atlas.graph import _clear_graph_cache, load_graph
from codesteer_atlas.models import IndexManifest


def _manifest(files):
    return IndexManifest(
        total_chunks=len(files),
        repos_indexed=["demo"],
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        embedding_dim=384,
        last_indexed_at="2026-08-04T16:17:48.499369+00:00",
        git_head_sha="abc1234",
        languages_indexed=["python"],
        index_version="2.1.0",
        files={path: "hash" for path in files},
    )


def _file_node(path, degree=0, label=None):
    return {
        "id": f"file:{path}",
        "kind": "file",
        "label": label or path.rsplit("/", 1)[-1],
        "file_path": path,
        "lines": None,
        "degree": degree,
    }


def _sym_node(path, name, degree=0):
    return {
        "id": f"sym:{path}#{name}",
        "kind": "symbol",
        "label": name,
        "file_path": path,
        "lines": [1, 8],
        "degree": degree,
    }


def _load_graph(tmp_path, nodes, edges):
    _clear_graph_cache()
    payload = {
        "nodes": nodes,
        "edges": edges,
        "metrics": {"node_count": len(nodes), "edge_count": len(edges), "top_hubs": []},
    }
    (tmp_path / "graph.json").write_text(json.dumps(payload), encoding="utf-8")
    return load_graph(tmp_path)


def _base_graph(tmp_path, extra_files=None, extra_nodes=None, extra_edges=None):
    a = _file_node("pkg/a.py", degree=1)
    b = _file_node("pkg/b.py", degree=1)
    run = _sym_node("pkg/a.py", "run", degree=1)
    nodes = [a, b, run, *(extra_nodes or [])]
    edges = [
        {"source": a["id"], "target": run["id"], "kind": "contains"},
        {"source": a["id"], "target": b["id"], "kind": "imports"},
        *(extra_edges or []),
    ]
    files = ["pkg/a.py", "pkg/b.py", *(extra_files or [])]
    return _load_graph(tmp_path, nodes, edges), _manifest(files), run


def test_import_build_context_does_not_load_fastembed():
    code = (
        "import sys\n"
        "from codesteer_atlas.context import build_context\n"
        "assert 'fastembed' not in sys.modules\n"
        "assert 'codesteer_atlas.embeddings' not in sys.modules\n"
        "assert callable(build_context)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_each_intent_returns_expected_section_keys(tmp_path):
    graph, manifest, run = _base_graph(tmp_path)
    brief = {
        "layers": [{"path": "pkg", "role": "source", "files": 2}],
        "entrypoints": [{"file_path": "pkg/a.py"}],
    }
    for intent, keys in INTENT_SECTIONS.items():
        payload = build_context(graph, target=run["id"], intent=intent, manifest=manifest, brief=brief)
        assert set(payload["sections"]) == set(keys)
        assert payload["intent"] == intent


def test_package_never_exceeds_CONTEXT_RESPONSE_MAX_CHARS(tmp_path, monkeypatch):
    monkeypatch.setattr("codesteer_atlas.context.CONTEXT_RESPONSE_MAX_CHARS", 1800)
    extra_nodes = [_file_node(f"pkg/dep{i}.py", degree=1) for i in range(30)]
    extra_edges = [
        {"source": node["id"], "target": "file:pkg/b.py", "kind": "imports"} for node in extra_nodes
    ]
    graph, manifest, _run = _base_graph(
        tmp_path,
        extra_files=[node["file_path"] for node in extra_nodes],
        extra_nodes=extra_nodes,
        extra_edges=extra_edges,
    )
    payload = build_context(graph, target="pkg/b.py", intent="review", manifest=manifest)
    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    assert len(serialized) <= 1800
    assert payload["budget"]["used_chars"] <= 1800


def test_empty_section_returns_quota_to_pool(monkeypatch):
    budgets = dict(ctxmod.CONTEXT_BUDGET_BY_SECTION)
    budgets["callers"] = 400
    budgets["tests"] = 80
    monkeypatch.setattr(ctxmod, "CONTEXT_BUDGET_BY_SECTION", budgets)
    bulky = {
        "file_path": "tests/" + ("x" * 120) + ".py",
        "confidence": "inferred",
        "via": "convention",
    }
    truncated = {}
    filled = apply_section_quotas(
        ["callers", "tests"],
        {"callers": [], "tests": [bulky]},
        truncated,
    )
    assert filled["callers"] == []
    assert filled["tests"] == [bulky]
    assert "tests" not in truncated


def test_quota_de_secao_trunca_prosa_escalar(monkeypatch):
    budgets = dict(ctxmod.CONTEXT_BUDGET_BY_SECTION)
    budgets["symbol"] = 40
    monkeypatch.setattr(ctxmod, "CONTEXT_BUDGET_BY_SECTION", budgets)
    truncated = {}
    filled = apply_section_quotas(
        ["symbol"], {"symbol": {"label": "run", "purpose": "p" * 200}}, truncated
    )

    assert len(json.dumps(filled["symbol"], separators=(",", ":"))) <= 40
    assert truncated["symbol"] == 1


def test_missing_target_raises_actionable_error(tmp_path):
    graph, manifest, _run = _base_graph(tmp_path)
    with pytest.raises(ValueError, match="não encontrado"):
        build_context(graph, target="does-not-exist", intent="edit", manifest=manifest)


def test_invalid_intent_raises_valueerror(tmp_path):
    graph, manifest, run = _base_graph(tmp_path)
    with pytest.raises(ValueError, match="edit.*debug.*review.*understand"):
        build_context(graph, target=run["id"], intent="refactor", manifest=manifest)


def test_edit_warns_calls_unavailable_without_inventing_callers(tmp_path):
    graph, manifest, run = _base_graph(tmp_path)
    payload = build_context(graph, target=run["id"], intent="edit", manifest=manifest)
    assert "calls_unavailable" in payload["warnings"]
    for caller in payload["sections"]["callers"]:
        assert caller["via"] in {"imports", "calls"}
        assert caller["via"] != "contains"
    assert all(item.get("via") != "calls" for item in payload["sections"]["callers"])


def test_debug_warns_git_history_and_error_path_does_not_raise(tmp_path):
    graph, manifest, run = _base_graph(tmp_path)
    payload = build_context(graph, target=run["id"], intent="debug", manifest=manifest)
    assert "git_history_unavailable" in payload["warnings"]
    assert "error_path_unavailable" in payload["warnings"]
    assert payload["sections"]["recent_history"] == []
    assert payload["sections"]["error_handling"] == []


def test_review_warns_diff_unavailable_includes_impact(tmp_path):
    graph, manifest, _run = _base_graph(tmp_path)
    payload = build_context(graph, target="pkg/b.py", intent="review", manifest=manifest)
    assert "diff_unavailable" in payload["warnings"]
    assert payload["sections"]["diff"] == []
    assert any(item["id"] == "file:pkg/a.py" for item in payload["sections"]["impact"])


def test_understand_includes_layer_from_brief(tmp_path):
    graph, manifest, run = _base_graph(tmp_path)
    brief = {
        "layers": [{"path": "pkg", "role": "source", "files": 2, "rank_basis": "degree"}],
        "entrypoints": [],
    }
    payload = build_context(
        graph, target=run["id"], intent="understand", manifest=manifest, brief=brief
    )
    assert payload["sections"]["layer"]["path"] == "pkg"
    assert payload["sections"]["brief_layer"]["path"] == "pkg"


def test_discover_tests_by_convention_on_manifest_files(tmp_path):
    graph, _manifest_unused, run = _base_graph(tmp_path, extra_files=["tests/test_a.py"])
    hits, warnings = discover_tests(graph, run, _manifest(["pkg/a.py", "pkg/b.py", "tests/test_a.py"]), [])
    assert any(hit["file_path"] == "tests/test_a.py" for hit in hits)
    assert all(hit["confidence"] == "inferred" for hit in hits)
    assert "test_discovery_convention_only" in warnings


def test_discover_tests_inferred_confidence(tmp_path):
    importer = _file_node("tests/test_a.py", degree=1)
    graph, manifest, run = _base_graph(
        tmp_path,
        extra_files=["tests/test_a.py"],
        extra_nodes=[importer],
        extra_edges=[{"source": importer["id"], "target": "file:pkg/a.py", "kind": "imports"}],
    )
    hits, warnings = discover_tests(graph, run, manifest, [])
    assert all(hit["confidence"] == "inferred" for hit in hits)
    assert any(hit["via"] == "imports" for hit in hits)
    assert "test_discovery_convention_only" not in warnings


def test_understand_usa_lookup_e_summaries_semanticos(tmp_path):
    """Understand agrega purpose por point lookup e summaries do sidecar."""
    graph, manifest, run = _base_graph(tmp_path)
    brief = {"layers": [{"path": "pkg", "role": "source", "files": 2}], "entrypoints": []}
    sidecar = {
        "file_summaries": {"pkg/a.py": {"summary": "Implementa a execução principal."}},
        "layer_summaries": {"pkg": {"summary": "Agrupa o núcleo do pacote."}},
    }
    calls = []

    def lookup(file_path, scope_name):
        calls.append((file_path, scope_name))
        return "Executa a operação principal."

    payload = build_context(
        graph,
        target=run["id"],
        intent="understand",
        manifest=manifest,
        brief=brief,
        semantic_enabled=True,
        semantic_ready=True,
        semantic_sidecar=sidecar,
        purpose_lookup=lookup,
    )

    assert calls == [("pkg/a.py", "run")]
    assert payload["sections"]["symbol"]["purpose"] == "Executa a operação principal."
    assert payload["sections"]["file_summary"] == "Implementa a execução principal."
    assert payload["sections"]["layer"]["summary"] == "Agrupa o núcleo do pacote."


@pytest.mark.parametrize("intent", ["edit", "debug", "review"])
def test_semantica_nao_altera_outros_intents(tmp_path, intent):
    """Purpose e summary são opcionais apenas para o intent understand."""
    graph, manifest, run = _base_graph(tmp_path)
    payload = build_context(
        graph,
        target=run["id"],
        intent=intent,
        manifest=manifest,
        brief={"layers": [{"path": "pkg", "role": "source", "files": 2}]},
        semantic_enabled=True,
        semantic_ready=True,
        semantic_sidecar={
            "file_summaries": {"pkg/a.py": {"summary": "não deve aparecer"}},
            "layer_summaries": {"pkg": {"summary": "não deve aparecer"}},
        },
        purpose_lookup=lambda *_args: "não deve aparecer",
    )

    serialized = json.dumps(payload, ensure_ascii=False)
    assert "purpose" not in serialized
    assert "file_summary" not in payload["sections"]
    assert "summary" not in serialized


def test_semantica_off_ou_nao_ready_emite_warning_sem_lookup(tmp_path):
    """Off não enriquece; ligada sem índice pronto avisa e preserva a estrutura."""
    graph, manifest, run = _base_graph(tmp_path)

    def lookup(*_args):
        pytest.fail("lookup não deveria ser chamado")

    for enabled, ready in ((False, True), (True, False)):
        payload = build_context(
            graph,
            target=run["id"],
            intent="understand",
            manifest=manifest,
            brief={"layers": [{"path": "pkg", "role": "source", "files": 2}]},
            semantic_enabled=enabled,
            semantic_ready=ready,
            semantic_sidecar={"file_summaries": {"pkg/a.py": {"summary": "oculto"}}},
            purpose_lookup=lookup,
        )
        assert "purpose" not in json.dumps(payload, ensure_ascii=False)
        assert ("semantic_layer_unavailable" in payload["warnings"]) is (enabled and not ready)


def test_understand_remove_prosa_semantica_antes_da_quota(tmp_path, monkeypatch):
    """O teto remove purpose/summaries antes de reduzir fatos estruturais."""
    graph, manifest, run = _base_graph(tmp_path)
    monkeypatch.setattr(ctxmod, "CONTEXT_RESPONSE_MAX_CHARS", 1500)
    payload = build_context(
        graph,
        target=run["id"],
        intent="understand",
        manifest=manifest,
        brief={"layers": [{"path": "pkg", "role": "source", "files": 2}]},
        semantic_enabled=True,
        semantic_ready=True,
        semantic_sidecar={
            "file_summaries": {"pkg/a.py": {"summary": "f" * 1200}},
            "layer_summaries": {"pkg": {"summary": "l" * 1200}},
        },
        purpose_lookup=lambda *_args: "p" * 1200,
    )

    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    assert len(serialized) <= 1500
    assert "truncated_for_budget" in payload["warnings"]
    assert "purpose" not in json.dumps(payload["sections"]["symbol"], ensure_ascii=False)
    assert "file_summary" not in payload["sections"]
    assert payload["sections"]["layer"]["path"] == "pkg"


# ─────────────────────────────────────────────────────────────────────────────
# F5.1 — recent_history no intent debug
# ─────────────────────────────────────────────────────────────────────────────


def _commit_node(sha, subject="feat: ajusta run"):
    return {
        "id": f"commit:demo:{sha}",
        "kind": "commit",
        "label": subject,
        "file_path": None,
        "lines": None,
        "degree": 0,
    }


def _commit_record(sha, committed_at="2026-08-30T12:05:00+00:00", **overrides):
    from codesteer_atlas.models import CommitRecord

    payload = {
        "id": sha,
        "repo": "demo",
        "subject": "feat: ajusta run",
        "body": "Detalhe da mudança.",
        "authored_at": committed_at,
        "committed_at": committed_at,
        "files_touched": ["pkg/a.py"],
        "is_revert": False,
        "reverted_commit_id": None,
    }
    payload.update(overrides)
    return CommitRecord(**payload)


def _history_warnings(payload):
    """Conjunto EXATO de avisos da camada histórica declarados no pacote."""
    return sorted(w for w in payload["warnings"] if w.startswith("git_history_"))


def _history_graph(
    tmp_path, shas, state="ok", stale_shas=(), extra_files=None, extra_nodes=None, extra_edges=None
):
    """Grafo com `touches` do alvo `run` e o lookup pontual correspondente."""
    nodes = [_commit_node(sha) for sha in shas] + list(extra_nodes or [])
    edges = [
        {
            "source": f"commit:demo:{sha}",
            "target": "sym:pkg/a.py#run",
            "kind": "touches",
            "location": {"file_path": "pkg/a.py", "lines": [1, 8]},
        }
        for sha in shas
    ] + list(extra_edges or [])
    graph, manifest, run = _base_graph(
        tmp_path, extra_files=extra_files, extra_nodes=nodes, extra_edges=edges
    )

    def _lookup(keys):
        wanted = {sha for _repo, sha in keys}
        return [
            (
                _commit_record(sha, committed_at=f"2026-08-{20 + index:02d}T12:00:00+00:00"),
                sha in stale_shas,
            )
            for index, sha in enumerate(shas)
            if sha in wanted
        ]

    return graph, manifest, run, _lookup, {"state": state}


def test_recent_history_so_aparece_no_intent_debug(tmp_path):
    """CA04/GA-06: os demais intents não recebem dado de commit algum."""
    graph, manifest, run, lookup, state = _history_graph(tmp_path, ["a" * 40])

    debug = build_context(
        graph,
        target=run["id"],
        intent="debug",
        manifest=manifest,
        history_lookup=lookup,
        history_state=state,
    )
    assert debug["sections"]["recent_history"][0]["commit"]["id"] == "a" * 40
    assert debug["sections"]["recent_history"][0]["via"] == "touches"
    assert debug["sections"]["recent_history"][0]["via_location"] == {
        "file_path": "pkg/a.py",
        "lines": [1, 8],
    }
    assert "git_history_unavailable" not in debug["warnings"]

    for intent in ("edit", "review", "understand"):
        payload = build_context(
            graph,
            target=run["id"],
            intent=intent,
            manifest=manifest,
            history_lookup=lookup,
            history_state=state,
        )
        assert "recent_history" not in payload["sections"]
        assert "commit" not in json.dumps(payload["sections"])


def test_recent_history_ordena_por_data_desc_e_sha_asc(tmp_path):
    """CA04: ordenação determinística, com SHA crescente no empate de data."""
    shas = ["c" * 40, "a" * 40, "b" * 40]
    extra_nodes = [_commit_node(sha) for sha in shas]
    extra_edges = [
        {
            "source": f"commit:demo:{sha}",
            "target": "sym:pkg/a.py#run",
            "kind": "touches",
            "location": {"file_path": "pkg/a.py", "lines": [1, 8]},
        }
        for sha in shas
    ]
    graph, manifest, run = _base_graph(
        tmp_path, extra_nodes=extra_nodes, extra_edges=extra_edges
    )
    datas = {
        "a" * 40: "2026-08-30T12:00:00+00:00",
        "b" * 40: "2026-08-30T12:00:00+00:00",
        "c" * 40: "2026-08-31T12:00:00+00:00",
    }

    def _lookup(keys):
        return [(_commit_record(sha, committed_at=datas[sha]), False) for _repo, sha in keys]

    payload = build_context(
        graph,
        target=run["id"],
        intent="debug",
        manifest=manifest,
        history_lookup=_lookup,
        history_state={"state": "ok"},
    )

    assert [item["commit"]["id"] for item in payload["sections"]["recent_history"]] == [
        "c" * 40,
        "a" * 40,
        "b" * 40,
    ]


def test_debug_declara_git_history_empty_quando_alvo_nao_tem_ancora(tmp_path):
    """CA11/CA17: camada legível sem commit ancorado no alvo não é falha."""
    graph, manifest, run = _base_graph(tmp_path)

    payload = build_context(
        graph,
        target=run["id"],
        intent="debug",
        manifest=manifest,
        history_lookup=lambda keys: [],
        history_state={"state": "ok"},
    )

    assert payload["sections"]["recent_history"] == []
    assert _history_warnings(payload) == ["git_history_empty"]


def test_debug_sem_camada_historica_declara_indisponivel(tmp_path):
    """CA09: sem conjunto anterior, lista vazia e só `git_history_unavailable`."""
    graph, manifest, run = _base_graph(tmp_path)

    payload = build_context(graph, target=run["id"], intent="debug", manifest=manifest)

    assert payload["sections"]["recent_history"] == []
    # Igualdade de conjunto: é o que torna os quatro estados mutuamente exclusivos
    assert _history_warnings(payload) == ["git_history_unavailable"]


def test_debug_falha_total_retorna_conjunto_anterior_stale(tmp_path):
    """CA09: com conjunto anterior, os itens voltam marcados como stale."""
    graph, manifest, run, lookup, _state = _history_graph(tmp_path, ["a" * 40])

    payload = build_context(
        graph,
        target=run["id"],
        intent="debug",
        manifest=manifest,
        history_lookup=lookup,
        history_state={"state": "unavailable"},
    )

    assert payload["sections"]["recent_history"][0]["stale"] is True
    assert _history_warnings(payload) == ["git_history_stale", "git_history_unavailable"]


def test_debug_falha_parcial_mistura_confirmado_e_preservado(tmp_path):
    """CA10: confirmado com stale=false, preservado com stale=true e ambos declarados."""
    graph, manifest, run, lookup, _state = _history_graph(
        tmp_path, ["a" * 40, "b" * 40], stale_shas={"b" * 40}
    )

    payload = build_context(
        graph,
        target=run["id"],
        intent="debug",
        manifest=manifest,
        history_lookup=lookup,
        history_state={"state": "partial"},
    )

    stale_por_sha = {
        item["commit"]["id"]: item["stale"] for item in payload["sections"]["recent_history"]
    }
    assert stale_por_sha == {"a" * 40: False, "b" * 40: True}
    # `partial` não pode ser conflacionado com `unavailable`: a diferença entre
    # CA09 e CA10 é exatamente o conjunto, não a presença de um warning
    assert _history_warnings(payload) == ["git_history_partial", "git_history_stale"]


def test_debug_target_invalido_mantem_o_erro_atual(tmp_path):
    """CA08: falha de resolução do alvo não é substituída pela seção histórica."""
    graph, manifest, _run, lookup, state = _history_graph(tmp_path, ["a" * 40])

    for alvo in ("", "não-existe"):
        with pytest.raises(ValueError):
            build_context(
                graph,
                target=alvo,
                intent="debug",
                manifest=manifest,
                history_lookup=lookup,
                history_state=state,
            )


def test_teto_corta_recent_history_antes_dos_fatos_estruturais(tmp_path, monkeypatch):
    """
    CA12/GA-06: sob o teto global, a história cede ANTES de qualquer fato
    estrutural. A fixture povoa `call_chain_to_entrypoints` de propósito — sem
    fato estrutural nenhum a precedência seria inobservável.
    """
    monkeypatch.setattr("codesteer_atlas.context.CONTEXT_RESPONSE_MAX_CHARS", 1200)
    shas = [f"{index:040d}" for index in range(12)]
    graph, manifest, run, lookup, state = _history_graph(
        tmp_path,
        shas,
        extra_files=["pkg/entry.py"],
        extra_nodes=[_file_node("pkg/entry.py", degree=1)],
        extra_edges=[
            {"source": "file:pkg/entry.py", "target": "file:pkg/a.py", "kind": "imports"}
        ],
    )
    brief = {"entrypoints": [{"file_path": "pkg/entry.py"}]}

    payload = build_context(
        graph,
        target=run["id"],
        intent="debug",
        manifest=manifest,
        brief=brief,
        history_lookup=lookup,
        history_state=state,
    )

    # Pré-condição: há fato estrutural a ser poupado
    cadeia = payload["sections"]["call_chain_to_entrypoints"]
    assert len(cadeia) == 2
    assert [item["id"] for item in cadeia] == ["file:pkg/entry.py", "file:pkg/a.py"]

    # A história cedeu…
    assert len(payload["sections"]["recent_history"]) < len(shas)
    assert payload["truncated"]["recent_history"] >= 1
    assert "truncated_for_budget" in payload["warnings"]
    # …e nenhum fato estrutural cedeu junto — é isto que M10 viola
    assert payload["sections"]["symbol"]["id"] == run["id"]
    assert "symbol" not in payload["truncated"]
    assert "call_chain_to_entrypoints" not in payload["truncated"]
    assert len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False)) <= 1200


def test_recent_history_expoe_revert_marcado(tmp_path):
    """CA06/CA16: a marcação de revert chega ao debug com e sem SHA declarado."""
    shas = ["a" * 40, "b" * 40]
    graph, manifest, run, _lookup, state = _history_graph(tmp_path, shas)

    def _lookup(keys):
        return [
            (
                _commit_record(
                    "a" * 40,
                    subject='Revert "feat: ajusta run"',
                    is_revert=True,
                    reverted_commit_id="0123456",
                ),
                False,
            ),
            (
                _commit_record(
                    "b" * 40,
                    subject='Revert "feat: outro"',
                    is_revert=True,
                    reverted_commit_id=None,
                ),
                False,
            ),
        ]

    payload = build_context(
        graph,
        target=run["id"],
        intent="debug",
        manifest=manifest,
        history_lookup=_lookup,
        history_state=state,
    )
    reverts = {
        item["commit"]["id"]: item["commit"]["reverted_commit_id"]
        for item in payload["sections"]["recent_history"]
    }

    assert reverts == {"a" * 40: "0123456", "b" * 40: None}
    assert all(item["commit"]["is_revert"] for item in payload["sections"]["recent_history"])
