"""
Testes puros de `CrossEncoderReranker` com dublê determinístico do modelo.

Nenhum teste baixa modelo nem toca a rede: a carga real fica atrás de
`_load_model`, que os testes substituem.
"""

from types import SimpleNamespace

import pytest

from codesteer_atlas.reranker import CrossEncoderReranker


def _result(content="doc", score=0.1):
    return SimpleNamespace(content=content, score=score)


@pytest.fixture(autouse=True)
def _reset_singleton():
    CrossEncoderReranker._instance = None
    CrossEncoderReranker._model = None
    yield
    CrossEncoderReranker._instance = None
    CrossEncoderReranker._model = None


class _DeterministicEncoder:
    """Pontua pelo comprimento do documento — ordem previsível sem ONNX."""

    def rerank(self, query, documents, batch_size=64, **kwargs):
        return [float(len(doc)) for doc in documents]


def test_nao_carrega_o_modelo_ate_a_primeira_chamada_de_rerank(monkeypatch):
    loaded = {"n": 0}

    def _fake_load(self):
        loaded["n"] += 1
        return _DeterministicEncoder()

    monkeypatch.setattr(CrossEncoderReranker, "_load_model", _fake_load)

    CrossEncoderReranker()
    assert loaded["n"] == 0

    CrossEncoderReranker().rerank("q", [_result("aa"), _result("bbbb")])
    assert loaded["n"] == 1


def test_reordena_o_pool_pela_pontuacao_do_modelo(monkeypatch):
    monkeypatch.setattr(
        CrossEncoderReranker, "_load_model", lambda self: _DeterministicEncoder()
    )
    curto = _result("aa")
    longo = _result("zzzzzzzz")

    ordenado = CrossEncoderReranker().rerank("q", [curto, longo])

    assert ordenado == [longo, curto]


def test_devolve_entrada_inalterada_para_pool_vazio_ou_de_um_item(monkeypatch):
    loaded = {"n": 0}

    def _fake_load(self):
        loaded["n"] += 1
        return _DeterministicEncoder()

    monkeypatch.setattr(CrossEncoderReranker, "_load_model", _fake_load)

    vazio = []
    unico = [_result("x")]
    assert CrossEncoderReranker().rerank("q", vazio) is vazio
    assert CrossEncoderReranker().rerank("q", unico) is unico
    assert loaded["n"] == 0


def test_trata_content_none_como_string_vazia_sem_levantar(monkeypatch):
    seen = []

    class _Capture:
        def rerank(self, query, documents, batch_size=64, **kwargs):
            seen.extend(list(documents))
            return [0.0 for _ in documents]

    monkeypatch.setattr(CrossEncoderReranker, "_load_model", lambda self: _Capture())

    a = _result(content=None)
    b = _result(content="ok")
    CrossEncoderReranker().rerank("q", [a, b])

    assert seen[0] == ""
    assert seen[1] == "ok"


def test_propaga_excecao_de_carga_para_o_chamador(monkeypatch):
    def _boom(self):
        raise RuntimeError("modelo ausente")

    monkeypatch.setattr(CrossEncoderReranker, "_load_model", _boom)

    with pytest.raises(RuntimeError, match="modelo ausente"):
        CrossEncoderReranker().rerank("q", [_result("a"), _result("b")])
