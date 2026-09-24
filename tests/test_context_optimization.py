"""Perfil compacto: dedup, seleção, refs de expansão e projeção."""

from codesteer_atlas.config import CHUNK_TRUNCATION_MARKER
from codesteer_atlas.context_optimization import (
    build_class_outline,
    compact_context_payload,
    decode_expand_ref,
    deduplicate_results,
    encode_expand_ref,
    project_compact_search_results,
    resolve_response_profile,
    select_conservative,
    select_within_top_k,
)
from codesteer_atlas.models import CandidateEvaluation, SearchResult


def _hit(name, *, path=None, content="body", score=0.1, chunk_id=None, start=1, end=2):
    return SearchResult(
        file_path=path or f"src/{name}.py",
        start_line=start,
        end_line=end,
        scope_type="function",
        scope_name=name,
        language="python",
        content=content,
        score=score,
        repo="demo",
        match_arms=["vector"],
        chunk_id=chunk_id or name,
    )


def test_resolve_profile_default_segue_flag(monkeypatch):
    assert resolve_response_profile("default", environ={}) == "full"
    assert (
        resolve_response_profile("default", environ={"ATLAS_CONTEXT_OPTIMIZATION": "1"})
        == "compact"
    )
    assert resolve_response_profile("full", environ={"ATLAS_CONTEXT_OPTIMIZATION": "1"}) == "full"
    assert resolve_response_profile("compact", environ={}) == "compact"


def test_dedupe_mesmo_chunk_e_conteudo_identico():
    a = _hit("fn", content="def fn():\n  return 1\n", chunk_id="aaa")
    b = _hit("fn", content="def fn():\n  return 1\n", chunk_id="aaa")
    c = _hit("fn2", content="def fn():\n  return 1\n", chunk_id="bbb", path="src/fn.py")
    # c mesmo path+conteúdo via path override
    c = _hit("other", content="def fn():\n  return 1\n", chunk_id="bbb", path="src/fn.py")
    a = _hit("fn", content="def fn():\n  return 1\n", chunk_id="aaa", path="src/fn.py")
    outcome = deduplicate_results([a, b, c])
    assert len(outcome.results) == 1
    assert outcome.removed_by_redundancy == 2


def test_dedupe_conteudo_contido_sem_truncamento():
    outer = _hit(
        "Outer",
        content="class Outer:\n  def a(self):\n    return 1\n",
        chunk_id="outer",
        path="src/m.py",
        start=1,
        end=10,
    )
    inner = _hit(
        "Outer.a",
        content="  def a(self):\n    return 1\n",
        chunk_id="inner",
        path="src/m.py",
        start=2,
        end=4,
    )
    outcome = deduplicate_results([outer, inner])
    assert len(outcome.results) == 1
    assert "inner" in outcome.results[0].covered_ids or outcome.removed_by_redundancy == 1


def test_dedupe_nao_une_truncados_nem_arquivos_distintos():
    truncated = _hit(
        "big",
        content=f"header\n{CHUNK_TRUNCATION_MARKER}\nfooter",
        chunk_id="t1",
        path="src/a.py",
    )
    part = _hit("big.part", content="header\n", chunk_id="t2", path="src/a.py")
    other = _hit("big", content="header\n", chunk_id="t3", path="src/b.py")
    outcome = deduplicate_results([truncated, part, other])
    assert len(outcome.results) == 3


def test_selecao_conservadora_remove_so_irrelevante_confiavel():
    results = [_hit("keep", score=0.5), _hit("drop", score=0.1), _hit("uncertain", score=0.2)]
    evaluations = [
        CandidateEvaluation(
            candidate_id="0",
            score=2.0,
            confidence=0.95,
            state="evaluated",
            chunk_id="keep",
            file_path="src/keep.py",
            scope_name="keep",
        ),
        CandidateEvaluation(
            candidate_id="1",
            score=0.0,
            confidence=0.95,
            state="evaluated",
            chunk_id="drop",
            file_path="src/drop.py",
            scope_name="drop",
        ),
        CandidateEvaluation(
            candidate_id="2",
            score=0.0,
            confidence=0.5,
            state="uncertain",
            chunk_id="uncertain",
            file_path="src/uncertain.py",
            scope_name="uncertain",
        ),
    ]
    selected = select_conservative(results, "q", evaluations, top_k=5)
    names = [r.scope_name for r in selected.results]
    assert "drop" not in names
    assert "keep" in names
    assert "uncertain" in names
    assert selected.removed_by_relevance == 1


def test_selecao_fallback_unconfirmed_quando_tudo_irrelevante():
    results = [_hit("only", score=0.1)]
    evaluations = [
        CandidateEvaluation(
            candidate_id="0",
            score=0.0,
            confidence=0.99,
            state="evaluated",
            chunk_id="only",
            file_path="src/only.py",
            scope_name="only",
        )
    ]
    selected = select_conservative(results, "q", evaluations, top_k=5)
    assert len(selected.results) == 1
    assert selected.unconfirmed is True
    assert "relevance_unconfirmed_fallback" in selected.warnings


def _ev(name, score, *, confidence=0.9, state="evaluated"):
    return CandidateEvaluation(
        candidate_id=name,
        score=score,
        confidence=confidence,
        state=state,
        chunk_id=name,
        file_path=f"src/{name}.py",
        scope_name=name,
    )


def test_corte_top_k_remove_score_baixo_sem_repor():
    results = [_hit("a"), _hit("b"), _hit("c"), _hit("d")]
    evaluations = [_ev("a", 1.9), _ev("b", 0.4), _ev("c", 1.2), _ev("d", 1.8)]
    selected = select_within_top_k(results, "q", evaluations, top_k=3)
    assert [r.scope_name for r in selected.results] == ["a", "c"]
    assert selected.removed_by_relevance == 1


def test_corte_preserva_exato_e_sem_nota_mas_corta_incerto():
    results = [_hit("alvo"), _hit("sem_nota"), _hit("falhou"), _hit("incerto")]
    evaluations = [
        _ev("alvo", 0.1),
        _ev("falhou", None, confidence=None, state="batch_failed"),
        _ev("incerto", 0.6, confidence=0.3, state="uncertain"),
    ]
    selected = select_within_top_k(results, "alvo", evaluations, top_k=4)
    assert [r.scope_name for r in selected.results] == ["alvo", "sem_nota", "falhou"]
    assert selected.removed_by_relevance == 1


def test_limiares_seguem_a_escala_da_rubrica():
    results = [_hit("a"), _hit("b")]
    v2 = [_ev("a", 1.4).model_copy(update={"score_max": 3.0}),
          _ev("b", 1.6).model_copy(update={"score_max": 3.0})]
    assert [r.scope_name for r in select_within_top_k(results, "q", v2, top_k=2).results] == ["b"]
    v1 = [_ev("a", 1.4), _ev("b", 1.6)]
    assert [r.scope_name for r in select_within_top_k(results, "q", v1, top_k=2).results] == ["a", "b"]
    drop = [_ev("a", 0.3, confidence=0.95).model_copy(update={"score_max": 3.0}),
            _ev("b", 0.3, confidence=0.95)]
    kept = select_conservative(results, "q", drop, top_k=2)
    assert [r.scope_name for r in kept.results] == ["b"]
    assert kept.removed_by_relevance == 1


def _member(chunk_id, name, start, end, kind="method"):
    return {"id": chunk_id, "scope_name": name, "scope_type": kind, "start_line": start, "end_line": end}


def test_resumo_de_classe_lista_so_membros_diretos():
    chunk = {"scope_type": "class", "scope_name": "C", "start_line": 1, "end_line": 50}
    members = [
        _member("c", "C", 1, 50, "class"),
        _member("b", "C.b", 12, 20),
        _member("a", "C.a", 5, 10),
        _member("n", "C.a.inner", 6, 8, "function"),
        _member("x", "D.x", 30, 31),
    ]
    header_end, outline = build_class_outline(chunk, members)
    assert header_end == 4
    assert [m["symbol"] for m in outline] == ["C.a", "C.b"]
    assert outline[0] == {"ref": "a", "symbol": "C.a", "type": "method", "lines": [5, 10]}


def test_resumo_de_classe_nao_se_aplica_sem_membros_ou_em_linha_unica():
    chunk = {"scope_type": "class", "scope_name": "C", "start_line": 1, "end_line": 1}
    assert build_class_outline(chunk, [_member("a", "C.a", 1, 1)]) is None
    assert build_class_outline({**chunk, "end_line": 9}, []) is None
    assert build_class_outline({**chunk, "scope_type": "function"}, [_member("a", "C.a", 2, 3)]) is None


def test_corte_fallback_local_quando_tudo_sai():
    results = [_hit("a"), _hit("b")]
    evaluations = [_ev("a", 0.2), _ev("b", 0.3)]
    selected = select_within_top_k(
        results, "q", evaluations, top_k=2, local_fallback=[results[1], results[0]]
    )
    assert [r.scope_name for r in selected.results] == ["b"]
    assert selected.unconfirmed is True
    assert "relevance_unconfirmed_fallback" in selected.warnings
    assert selected.removed_by_relevance == 1


def test_expand_ref_roundtrip():
    ref = encode_expand_ref(repo="demo", chunk_id="abc123", file_hash="deadbeef")
    decoded = decode_expand_ref(ref)
    assert decoded == {"repo": "demo", "chunk_id": "abc123", "file_hash": "deadbeef"}


def test_project_compact_sobe_repo_uniforme():
    results = [_hit("a"), _hit("b")]
    items, repo = project_compact_search_results(
        results, include_content=False, file_hashes={"src/a.py": "h1", "src/b.py": "h2"}
    )
    assert repo == "demo"
    assert "repo" not in items[0]
    assert "ref" in items[0]
    assert "score" not in items[0]
    assert "match_arms" not in items[0]


def test_compact_context_remove_duplicatas():
    payload = {
        "target": {"id": "sym:x", "label": "x", "purpose": "faz X"},
        "intent": "understand",
        "sections": {
            "symbol": {"id": "sym:x", "label": "x"},
            "layer": {"path": "src", "role": "source"},
            "brief_layer": {"path": "src", "role": "source", "summary": "camada"},
            "neighbors": {},
        },
        "warnings": [],
    }
    compact = compact_context_payload(payload)
    assert "symbol" not in compact["sections"]
    assert compact["target"].get("purpose") == "faz X"
    assert "brief_layer" not in compact["sections"]
    assert compact["sections"]["layer"].get("summary") == "camada"
