"""
Testes do finalizador de resposta compartilhado (`response_budget.py`).
Cobre V4-V6 (tetos, Unicode, envelope mínimo, tokenizer incompatível com o
envelope mínimo) do plano observabilidade-tokens-consultas, na parte que é
comum às quatro tools — a política de QUAL item cortar primeiro é testada
junto de cada módulo (`test_context.py`, `test_brief.py`, `test_graph.py`,
`test_server.py` para `atlas_search`).
"""

import json

import pytest

from codesteer_atlas import observability as obs
from codesteer_atlas import response_budget as rb
from codesteer_atlas.config import TOKENIZER_PATH_ENV_FLAG


@pytest.fixture(autouse=True)
def _reset_state():
    obs.reset_token_counter_for_tests()
    yield
    obs.reset_token_counter_for_tests()


def _write_tiny_tokenizer(path, *, vocab_repeat="x"):
    from tokenizers import Tokenizer, models, pre_tokenizers

    vocab = {"[UNK]": 0, vocab_repeat: 1}
    tokenizer = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.save(str(path))


def _cut_from_list(list_key: str):
    def _cut(payload: dict) -> bool:
        items = payload.get(list_key)
        if isinstance(items, list) and items:
            items.pop()
            payload["truncated"] = payload.get("truncated", 0) + 1
            return True
        return False

    return _cut


def _minimal_envelope(payload: dict) -> dict:
    return {"items": [], "truncated": True, "minimal": True}


def test_get_budget_returns_expected_tools_and_levels():
    assert rb.get_budget("search").max_chars == 24000
    assert rb.get_budget("search").max_tokens == 6000
    assert rb.get_budget("brief", level=0).max_tokens == 700
    assert rb.get_budget("brief", level=1).max_tokens == 1700
    assert rb.get_budget("context").max_tokens == 4000
    assert rb.get_budget("graph").max_tokens == 2000
    with pytest.raises(ValueError):
        rb.get_budget("unknown")


def test_finalize_response_no_cut_needed_adds_budget_block(monkeypatch):
    monkeypatch.delenv(TOKENIZER_PATH_ENV_FLAG, raising=False)
    payload = {"items": ["a", "b", "c"]}
    budget = rb.ResponseBudget("test", max_chars=5000, max_bytes=5000, max_tokens=1000)

    text, measurement = rb.finalize_response(
        payload,
        budget,
        cut_once=_cut_from_list("items"),
        minimal_envelope=_minimal_envelope,
    )
    parsed = json.loads(text)
    assert parsed["items"] == ["a", "b", "c"]
    assert parsed["budget"]["mode"] == "byte_bpe_upper_bound"
    assert parsed["budget"]["max_chars"] == 5000
    assert parsed["budget"]["max_tokens"] is None  # sem tokenizer, teto de tokens não se aplica
    assert parsed["budget"]["used_chars"] == len(text)
    assert measurement.chars == len(text)


def test_finalize_response_cuts_tail_until_it_fits(monkeypatch):
    monkeypatch.delenv(TOKENIZER_PATH_ENV_FLAG, raising=False)
    payload = {"items": ["x" * 200 for _ in range(30)]}
    budget = rb.ResponseBudget("test", max_chars=1500, max_bytes=1500, max_tokens=100000)

    text, _measurement = rb.finalize_response(
        payload,
        budget,
        cut_once=_cut_from_list("items"),
        minimal_envelope=_minimal_envelope,
    )
    parsed = json.loads(text)
    assert len(parsed["items"]) < 30
    assert parsed["truncated"] >= 1
    assert len(text) <= 1500
    assert len(text.encode("utf-8")) <= 1500


def test_finalize_response_falls_back_to_minimal_envelope_when_nothing_left():
    payload = {"items": []}  # cut_once não tem nada a remover desde o início
    budget = rb.ResponseBudget("test", max_chars=10, max_bytes=10, max_tokens=100)

    text, _measurement = rb.finalize_response(
        payload,
        budget,
        cut_once=_cut_from_list("items"),
        minimal_envelope=_minimal_envelope,
    )
    parsed = json.loads(text)
    assert parsed["minimal"] is True
    assert parsed["items"] == []


def test_finalize_response_used_chars_matches_actual_final_length():
    payload = {"items": [f"item-{i}" for i in range(5)]}
    budget = rb.ResponseBudget("test", max_chars=200, max_bytes=200, max_tokens=1000)

    text, _measurement = rb.finalize_response(
        payload,
        budget,
        cut_once=_cut_from_list("items"),
        minimal_envelope=_minimal_envelope,
    )
    parsed = json.loads(text)
    assert parsed["budget"]["used_chars"] == len(text)


def test_finalize_response_respects_byte_budget_for_unicode(monkeypatch):
    monkeypatch.delenv(TOKENIZER_PATH_ENV_FLAG, raising=False)
    # Emoji/CJK ocupam vários bytes por caractere: um payload que cabe em chars
    # pode estourar bytes — o finalizador precisa cortar por ISSO também.
    payload = {"items": ["🎉日本語" * 20 for _ in range(10)]}
    budget = rb.ResponseBudget("test", max_chars=100000, max_bytes=500, max_tokens=100000)

    text, measurement = rb.finalize_response(
        payload,
        budget,
        cut_once=_cut_from_list("items"),
        minimal_envelope=_minimal_envelope,
    )
    assert measurement.bytes <= 500
    assert len(text.encode("utf-8")) <= 500


def test_finalize_response_enforces_exact_token_budget_when_tokenizer_configured(tmp_path, monkeypatch):
    tokenizer_path = tmp_path / "tokenizer.json"
    _write_tiny_tokenizer(tokenizer_path)
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, str(tokenizer_path))
    obs.reset_token_counter_for_tests()

    payload = {"items": ["x " * 5 for _ in range(20)]}
    # max_tokens realista o bastante para sobrar espaço de conteúdo depois da
    # reserva do próprio bloco `budget` (contado com o MESMO tokenizer minúsculo,
    # que tokeniza pontuação JSON de forma agressiva — daí a reserva não ser tão
    # pequena quanto em um tokenizer BPE real).
    budget = rb.ResponseBudget("test", max_chars=100000, max_bytes=100000, max_tokens=60)

    text, measurement = rb.finalize_response(
        payload,
        budget,
        cut_once=_cut_from_list("items"),
        minimal_envelope=_minimal_envelope,
    )
    assert measurement.count_method == "tokenizer"
    assert measurement.tokens <= 60
    parsed = json.loads(text)
    assert len(parsed["items"]) < 20  # precisou cortar para caber no teto de tokens
    assert parsed["budget"]["mode"] == "tokenizer_exact"
    assert parsed["budget"]["max_tokens"] == 60


def test_finalize_response_degrades_to_bytes_when_minimal_envelope_exceeds_token_budget(
    tmp_path, monkeypatch
):
    tokenizer_path = tmp_path / "tokenizer.json"
    _write_tiny_tokenizer(tokenizer_path)
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, str(tokenizer_path))
    obs.reset_token_counter_for_tests()

    payload = {"items": ["x x x x x x x x x x x x x x x x x x x x"]}
    # Teto de tokens absurdamente pequeno: nem o envelope mínimo cabe.
    budget = rb.ResponseBudget("test", max_chars=100000, max_bytes=100000, max_tokens=1)

    def _cut_once(_payload: dict) -> bool:
        return False  # nada a cortar: força o envelope mínimo de imediato

    text, measurement = rb.finalize_response(
        payload,
        budget,
        cut_once=_cut_once,
        minimal_envelope=lambda p: {"items": [], "minimal": True},
    )
    parsed = json.loads(text)
    assert parsed["minimal"] is True
    # Degradado: a modalidade final não promete tokens exatos que não cabem.
    assert measurement.tokens is None
    assert measurement.count_method == "chars_div_4"
    assert parsed["budget"]["mode"] == "byte_bpe_upper_bound"
    # A degradação fica memorizada no contador até reinício (D2).
    assert obs.get_token_counter()._unavailable is True


def test_serialize_is_compact_json():
    text = rb.serialize({"a": 1, "b": [1, 2]})
    assert "\n" not in text
    assert ", " not in text
    assert ": " not in text
