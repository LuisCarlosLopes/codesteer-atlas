"""
Testes das funções puras de preparo de query e reordenação (`ranking.py`).

São puras de propósito: cobrem o comportamento de ranking sem construir índice,
sem LanceDB e sem carregar o modelo de embedding.
"""

from types import SimpleNamespace

import pytest

from codesteer_atlas.ranking import (
    PHRASE_BOOST_CAP,
    TITLE_WEIGHT_CODE,
    TITLE_WEIGHT_PROSE,
    fold,
    min_span,
    phrase_boost,
    proximity_boost,
    query_terms,
    rerank,
    title_boost,
)


def _result(scope_name="fn", scope_type="function", language="python", content="", score=0.5):
    """Dublê leve de `SearchResult` — `rerank` só depende dos atributos."""
    return SimpleNamespace(
        scope_name=scope_name,
        scope_type=scope_type,
        language=language,
        content=content,
        score=score,
    )


# ── fold ─────────────────────────────────────────────────────────────────────


def test_fold_remove_acento_e_preserva_comprimento():
    original = "Índice de Ação não Resolvido"
    folded = fold(original)

    assert folded == "indice de acao nao resolvido"
    # Comprimento preservado é pré-condição do cálculo de proximidade: NFKD
    # decomporia "í" em 2 code points e desalinharia todos os offsets.
    assert len(folded) == len(original)


# ── query_terms ──────────────────────────────────────────────────────────────


def test_query_terms_normaliza_e_poda():
    assert query_terms("Como o Índice é resolvido") == ["indice", "resolvido"]


def test_query_terms_fallback_quando_tudo_e_stopword():
    assert query_terms("como o que") == ["como", "que"]


# ── min_span ─────────────────────────────────────────────────────────────────


def test_min_span_encontra_janela_mais_apertada():
    # termo A em 0 e 100, termo B em 105 -> menor janela é 100..105
    assert min_span([[0, 100], [105]]) == 5


def test_min_span_none_quando_termo_ausente():
    """Termo ausente é ausência de sinal, não proximidade máxima."""
    assert min_span([[1, 2], []]) is None


def test_min_span_lista_vazia():
    assert min_span([]) is None


# ── title_boost ──────────────────────────────────────────────────────────────


def test_title_boost_codigo_pesa_mais_que_prosa():
    terms = ["search", "hybrid"]
    codigo = title_boost("StorageBackend.search_hybrid", terms, is_code=True)
    prosa = title_boost("StorageBackend.search_hybrid", terms, is_code=False)

    assert codigo == pytest.approx(TITLE_WEIGHT_CODE)
    assert prosa == pytest.approx(TITLE_WEIGHT_PROSE)
    assert codigo > prosa


def test_title_boost_proporcional_aos_termos_casados():
    boost = title_boost("search_hybrid", ["search", "ausente"], is_code=True)
    assert boost == pytest.approx(TITLE_WEIGHT_CODE * 0.5)


def test_title_boost_zero_sem_casamento():
    assert title_boost("outro_nome", ["search"], is_code=True) == 0.0


def test_title_boost_ignora_acento():
    assert title_boost("resolve_índice", ["indice"], is_code=True) > 0.0


# ── proximity_boost ──────────────────────────────────────────────────────────


def test_proximity_boost_premia_termos_proximos():
    perto = proximity_boost("alpha beta " + "x" * 200, ["alpha", "beta"])
    longe = proximity_boost("alpha " + "x" * 200 + " beta", ["alpha", "beta"])

    assert perto > longe


def test_proximity_boost_zero_com_termo_unico():
    assert proximity_boost("alpha beta", ["alpha"]) == 0.0


def test_proximity_boost_zero_quando_termo_ausente():
    assert proximity_boost("alpha", ["alpha", "beta"]) == 0.0


# ── phrase_boost ─────────────────────────────────────────────────────────────


def test_phrase_boost_satura_no_teto():
    muitos = "alpha beta " * 50
    assert phrase_boost(muitos, ["alpha", "beta"]) == pytest.approx(PHRASE_BOOST_CAP)


def test_phrase_boost_zero_sem_adjacencia():
    disperso = "alpha " + "x" * 200 + " beta"
    assert phrase_boost(disperso, ["alpha", "beta"]) == 0.0


# ── rerank ───────────────────────────────────────────────────────────────────


def test_rerank_promove_casamento_no_nome_do_simbolo():
    irrelevante = _result(scope_name="outra_coisa", content="nada aqui", score=0.9)
    alvo = _result(scope_name="search_hybrid", content="def search_hybrid(...)", score=0.1)

    ordenado = rerank([irrelevante, alvo], "search_hybrid")

    assert ordenado[0] is alvo


def test_rerank_estavel_sem_boost():
    """Sem nenhum boost, a saída é exatamente a ordem de entrada do RRF."""
    a = _result(scope_name="aa", content="aa", score=0.9)
    b = _result(scope_name="bb", content="bb", score=0.8)
    c = _result(scope_name="cc", content="cc", score=0.7)

    assert rerank([a, b, c], "termo_inexistente_zzz") == [a, b, c]


def test_rerank_desempata_pelo_score_rrf():
    baixo = _result(scope_name="search_hybrid", content="x", score=0.1)
    alto = _result(scope_name="search_hybrid", content="x", score=0.9)

    assert rerank([baixo, alto], "search_hybrid")[0] is alto


def test_rerank_lida_com_content_none():
    """`content` é Optional em `SearchResult`; o boost não pode explodir por isso."""
    alvo = _result(scope_name="search_hybrid", content=None, score=0.5)

    assert rerank([alvo], "search_hybrid") == [alvo]


def test_rerank_devolve_entrada_quando_query_sem_termos_uteis():
    a = _result(scope_name="aa", score=0.9)
    b = _result(scope_name="bb", score=0.8)

    assert rerank([a, b], "   ") == [a, b]


def test_rerank_lista_vazia():
    assert rerank([], "qualquer") == []
