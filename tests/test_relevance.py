"""Contratos do avaliador Jev: config, HTTP, seleção, falhas, limites e custo."""

import json
from urllib.error import HTTPError

import pytest

from codesteer_atlas.config import (
    RELEVANCE_MAX_CONTENT_CHARS,
    RELEVANCE_RUBRIC_VERSION,
    RELEVANCE_TIMEOUT_S,
)
from codesteer_atlas.http_transport import _NoRedirectHandler, post_json
from codesteer_atlas.models import CandidateEvaluation, SearchResult
from codesteer_atlas.relevance import (
    SCORE_LEVELS,
    build_request,
    new_usage,
    order_by_relevance,
    public_usage_fields,
    request_bytes,
    resolve_config,
    status_block,
    try_rerank,
    validate_answers,
)


def _hit(name, content="body", score=0.1, path=None):
    return SearchResult(
        file_path=path or f"src/{name}.py",
        start_line=1,
        end_line=2,
        scope_type="function",
        scope_name=name,
        language="python",
        content=content,
        score=score,
        repo="demo",
        match_arms=["vector"],
    )


def _envelope(ids, scores, *, confidences=None, cost=0.001, model="typesafe/jev-1.13", extra=None):
    answers = {}
    for index, cid in enumerate(ids):
        answers[cid] = {
            "type": "score",
            "score": scores[index],
            "confidence": (confidences or [0.9] * len(ids))[index],
        }
    payload = {
        "id": "gen-dec-test",
        "model": model,
        "provider": "TypeSafe",
        "answers": answers,
        "usage": {"input_tokens": 10, "output_tokens": 2, "cost": cost},
    }
    if extra:
        payload.update(extra)
    return payload


def _openrouter_env(**overrides):
    env = {
        "ATLAS_RELEVANCE": "1",
        "ATLAS_SEMANTIC_API_URL": "https://openrouter.ai/api/v1/chat/completions",
        "ATLAS_SEMANTIC_API_KEY": "sk-or-v1-test",
    }
    env.update(overrides)
    return env


def test_flag_ausente_zero_false_desligam():
    assert resolve_config({}).enabled is False
    assert resolve_config({"ATLAS_RELEVANCE": "0"}).enabled is False
    assert resolve_config({"ATLAS_RELEVANCE": "false"}).enabled is False
    assert resolve_config({"ATLAS_RELEVANCE": "FALSE"}).enabled is False


def test_flag_um_e_true_ligam():
    assert resolve_config({"ATLAS_RELEVANCE": "1"}).enabled is True
    assert resolve_config({"ATLAS_RELEVANCE": "true"}).enabled is True
    assert resolve_config({"ATLAS_RELEVANCE": "TRUE"}).enabled is True


def test_flag_invalida_desliga_e_declara():
    cfg = resolve_config({"ATLAS_RELEVANCE": "maybe"})
    assert cfg.enabled is False
    assert cfg.flag_invalid is True
    assert cfg.reason == "invalid_flag"
    status = status_block({"ATLAS_RELEVANCE": "maybe"})
    assert status["enabled"] is False
    assert status["reason"] == "invalid_flag"
    assert status["egress"].startswith("Nenhum")


def test_modelo_nao_herda_f4_e_aceita_alias_latest():
    env = _openrouter_env(
        ATLAS_SEMANTIC_MODEL="openai/gpt-4.1-mini",
        ATLAS_RELEVANCE_MODEL="~typesafe/jev-latest",
    )
    cfg = resolve_config(env)
    assert cfg.configured is True
    assert cfg.model == "~typesafe/jev-latest"
    defaulted = resolve_config(_openrouter_env(ATLAS_SEMANTIC_MODEL="openai/gpt-4.1-mini"))
    assert defaulted.model == "~typesafe/jev-latest"
    assert defaulted.model != "openai/gpt-4.1-mini"
    without_tilde = resolve_config(_openrouter_env(ATLAS_RELEVANCE_MODEL="typesafe/jev-latest"))
    assert without_tilde.configured is True
    pinned = resolve_config(_openrouter_env(ATLAS_RELEVANCE_MODEL="typesafe/jev-1.13"))
    assert pinned.configured is True
    assert pinned.model == "typesafe/jev-1.13"
    inherited = resolve_config(_openrouter_env(ATLAS_RELEVANCE_MODEL="openai/gpt-4.1-mini"))
    assert inherited.configured is False
    assert inherited.reason == "invalid_model"


def test_url_e_chave_so_reutilizam_openrouter_https():
    derived = resolve_config(_openrouter_env())
    assert derived.configured is True
    assert derived.url == "https://openrouter.ai/api/v1/systemone"
    other = resolve_config(
        {
            "ATLAS_RELEVANCE": "1",
            "ATLAS_SEMANTIC_API_URL": "https://api.example/v1/chat/completions",
            "ATLAS_SEMANTIC_API_KEY": "sk-other",
        }
    )
    assert other.configured is False
    assert other.api_key is None
    explicit = resolve_config(
        {
            "ATLAS_RELEVANCE": "1",
            "ATLAS_RELEVANCE_API_URL": "https://openrouter.ai/api/v1/systemone/",
            "ATLAS_RELEVANCE_API_KEY": "sk-rel",
        }
    )
    assert explicit.url == "https://openrouter.ai/api/v1/systemone"
    invalid = resolve_config(
        {
            "ATLAS_RELEVANCE": "1",
            "ATLAS_RELEVANCE_API_URL": "https://evil.example/api/v1/systemone",
            "ATLAS_RELEVANCE_API_KEY": "sk",
        }
    )
    assert invalid.configured is False
    assert invalid.reason == "invalid_url"


def test_status_nao_expoe_credencial_nem_depende_de_rede():
    status = status_block(_openrouter_env())
    dumped = json.dumps(status)
    assert "sk-or-v1-test" not in dumped
    assert status["provider"] == "openrouter"
    assert status["configured"] is True
    assert "OpenRouter" in status["egress"]
    missing_key = status_block({"ATLAS_RELEVANCE": "1", "ATLAS_RELEVANCE_API_URL": "https://openrouter.ai/api/v1/systemone"})
    assert missing_key["configured"] is False
    assert missing_key["reason"] == "missing_api_key"


def test_flag_off_nao_chama_rede():
    called = {"n": 0}

    def boom(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("rede")

    usage = new_usage()
    warnings: list[str] = []
    result = try_rerank([_hit("a"), _hit("b")], "q", warnings, usage, environ={}, post=boom)
    assert result is None
    assert called["n"] == 0
    assert usage.cost_status == "not_incurred"
    assert usage.request_count == 0
    assert warnings == []


def test_pool_trivial_nao_chama_rede():
    called = {"n": 0}
    usage = new_usage()
    try_rerank(
        [_hit("a")],
        "q",
        [],
        usage,
        environ=_openrouter_env(),
        post=lambda *_a, **_k: called.__setitem__("n", called["n"] + 1) or "{}",
    )
    assert called["n"] == 0
    assert usage.reason == "pool_too_small"


def test_pergunta_referencia_candidato_explicito():
    payload = build_request("consulta", [_hit("alpha"), _hit("beta")], "typesafe/jev-1.13")
    assert payload["questions"]["0"]["instructions"].count("state.candidates[0]") == 1
    assert "state.candidates[1]" in payload["questions"]["1"]["instructions"]
    assert payload["questions"]["0"]["type"] == "score"
    assert payload["questions"]["0"]["criteria"] == list(SCORE_LEVELS)


def test_conteudo_enviado_e_truncado_sem_alterar_resultado_armazenado():
    original = "x" * (RELEVANCE_MAX_CONTENT_CHARS + 50)
    hit = _hit("long", content=original)
    payload = build_request("q", [hit], "typesafe/jev-1.13")
    assert len(payload["state"]["candidates"][0]["content"]) == RELEVANCE_MAX_CONTENT_CHARS
    assert hit.content == original


def test_overflow_nao_avalia_fracao(monkeypatch):
    monkeypatch.setattr("codesteer_atlas.relevance.RELEVANCE_MAX_REQUEST_BYTES", 80)
    called = {"n": 0}
    usage = new_usage()
    warnings: list[str] = []
    result = try_rerank(
        [_hit("a", content="corpo-grande"), _hit("b", content="outro-grande")],
        "consulta longa o bastante",
        warnings,
        usage,
        environ=_openrouter_env(),
        post=lambda *_a, **_k: called.__setitem__("n", 1) or "{}",
    )
    assert result is None
    assert called["n"] == 0
    assert "relevance_budget_exceeded" in warnings
    assert usage.request_count == 0
    assert usage.cost_status == "not_incurred"


def test_unicode_do_corpo_e_medida_em_utf8():
    payload = {"estado": "ação"}
    assert request_bytes(payload) == len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert request_bytes(payload) > len(json.dumps(payload, ensure_ascii=False))


def test_retorno_valido_promove_candidato_e_preserva_campos():
    pool = [_hit("weak", score=0.9), _hit("strong", score=0.2)]
    original_score = pool[1].score
    original_arms = list(pool[1].match_arms)
    envelope = _envelope(["0", "1"], [0.1, 2.0])
    usage = new_usage()
    warnings: list[str] = []
    captured = {}

    def fake_post(url, payload, **kwargs):
        captured["url"] = url
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return json.dumps(envelope)

    ordered = try_rerank(
        pool, "consulta", warnings, usage, environ=_openrouter_env(), post=fake_post
    )
    assert [item.scope_name for item in ordered] == ["strong", "weak"]
    assert ordered[0].score == original_score
    assert ordered[0].match_arms == original_arms
    assert ordered[0].content == "body"
    assert captured["url"] == "https://openrouter.ai/api/v1/systemone"
    assert captured["payload"]["model"] == "~typesafe/jev-latest"
    assert captured["kwargs"]["timeout_s"] <= RELEVANCE_TIMEOUT_S
    assert captured["kwargs"]["timeout_s"] > 0
    assert captured["kwargs"]["allow_redirects"] is False
    assert captured["kwargs"]["max_response_bytes"] == 256 * 1024
    assert usage.cost_status == "reported"
    assert usage.cost_usd == 0.001
    assert usage.status == "success"
    assert warnings == []


def test_flag_off_nao_valida_url_nem_credencial():
    cfg = resolve_config(
        {
            "ATLAS_RELEVANCE": "0",
            "ATLAS_RELEVANCE_API_URL": "https://evil.example:9999/api/v1/systemone",
            "ATLAS_RELEVANCE_API_KEY": "sk-secret",
        }
    )
    assert cfg.enabled is False
    assert cfg.url is None
    assert cfg.api_key is None
    assert cfg.configured is False


def test_url_porta_invalida_com_flag_ligada():
    invalid = resolve_config(
        {
            "ATLAS_RELEVANCE": "1",
            "ATLAS_RELEVANCE_API_URL": "https://openrouter.ai:8443/api/v1/systemone",
            "ATLAS_RELEVANCE_API_KEY": "sk",
        }
    )
    assert invalid.configured is False
    assert invalid.reason == "invalid_url"


def test_empate_jev_preserva_ordem_rrf_de_entrada():
    pool = [_hit("a", score=0.5), _hit("b", score=0.4), _hit("c", score=0.3)]
    evaluations = {
        "0": CandidateEvaluation(candidate_id="0", score=1.0, confidence=0.9, state="evaluated"),
        "1": CandidateEvaluation(candidate_id="1", score=1.0, confidence=0.9, state="evaluated"),
        "2": CandidateEvaluation(candidate_id="2", score=0.2, confidence=0.9, state="evaluated"),
    }
    ordered = order_by_relevance(pool, "q", evaluations)
    assert [item.scope_name for item in ordered] == ["a", "b", "c"]


def test_match_exato_fica_na_frente_mesmo_com_score_menor():
    pool = [_hit("other", score=0.2), _hit("alvo", score=0.9)]
    evaluations = {
        "0": CandidateEvaluation(candidate_id="0", score=2.0, confidence=0.9, state="evaluated"),
        "1": CandidateEvaluation(candidate_id="1", score=0.0, confidence=0.9, state="evaluated"),
    }
    ordered = order_by_relevance(pool, "alvo", evaluations)
    assert [item.scope_name for item in ordered] == ["alvo", "other"]


def test_match_exato_reconhece_nome_final_qualificado():
    pool = [
        _hit("Helper.run", score=0.2),
        SearchResult(
            file_path="src/svc.py",
            start_line=1,
            end_line=2,
            scope_type="method",
            scope_name="Service.run",
            language="python",
            content="body",
            score=0.9,
            repo="demo",
            match_arms=["fts"],
        ),
    ]
    evaluations = {
        "0": CandidateEvaluation(candidate_id="0", score=2.0, confidence=0.95, state="evaluated"),
        "1": CandidateEvaluation(candidate_id="1", score=0.0, confidence=0.95, state="evaluated"),
    }
    ordered = order_by_relevance(pool, "run", evaluations)
    assert [item.scope_name for item in ordered] == ["Helper.run", "Service.run"]


def test_baixa_confianca_isola_candidato_sem_invalidar_lote():
    usage = new_usage()
    warnings: list[str] = []
    envelope = _envelope(["0", "1"], [2.0, 0.0], confidences=[0.9, 0.2])
    ordered = try_rerank(
        [_hit("strong"), _hit("weak")],
        "q",
        warnings,
        usage,
        environ=_openrouter_env(),
        post=lambda *_a, **_k: json.dumps(envelope),
    )
    assert ordered is not None
    assert [item.scope_name for item in ordered] == ["strong", "weak"]
    assert "relevance_low_confidence" in warnings
    assert usage.status in {"success", "partial"}
    assert usage.candidates_uncertain == 1
    assert usage.candidates_evaluated == 1


def test_ids_ausentes_fazem_fallback_do_lote():
    usage = new_usage()
    warnings: list[str] = []
    missing = _envelope(["0"], [1.0])
    assert (
        try_rerank(
            [_hit("a"), _hit("b")],
            "q",
            warnings,
            usage,
            environ=_openrouter_env(),
            post=lambda *_a, **_k: json.dumps(missing),
        )
        is None
    )
    assert usage.cost_status == "reported"
    assert "relevance_invalid_response" in warnings


def test_lotes_parciais_somam_custo_e_preservam_nao_avaliados(monkeypatch):
    monkeypatch.setattr("codesteer_atlas.relevance.RELEVANCE_MAX_REQUEST_BYTES", 900)

    calls = {"n": 0}

    def fake_post(_url, payload, **_kwargs):
        calls["n"] += 1
        ids = list(payload["questions"].keys())
        if calls["n"] == 1:
            return json.dumps(_envelope(ids, [2.0] * len(ids), cost=0.01))
        raise TimeoutError("timed out")

    pool = [
        _hit("a", content="a" * 200),
        _hit("b", content="b" * 200),
        _hit("c", content="c" * 200),
        _hit("d", content="d" * 200),
    ]
    usage = new_usage()
    warnings: list[str] = []
    ordered = try_rerank(
        pool, "q", warnings, usage, environ=_openrouter_env(), post=fake_post
    )
    assert usage.request_count >= 1
    assert usage.request_count <= 2
    if ordered is not None:
        assert len(ordered) == len(pool)
    assert usage.cost_status in {"reported", "partially_known", "unknown"}


def test_probabilities_invalidas_e_validas_opcionais():
    ids = ["0", "1"]
    _, reason = validate_answers(
        _envelope(ids, [1.0, 1.0], extra={"answers": {
            "0": {"type": "score", "score": 1, "confidence": 0.9, "probabilities": {"irrelevant": 0.5}},
            "1": {"type": "score", "score": 1, "confidence": 0.9},
        }}),
        ids,
    )
    assert reason == "invalid_response"
    scores, reason = validate_answers(
        {
            "model": "typesafe/jev-1.13",
            "answers": {
                "0": {
                    "type": "score",
                    "score": 1.04,
                    "confidence": 0.94,
                    "legend": {
                        "0": SCORE_LEVELS[0],
                        "1": SCORE_LEVELS[1],
                        "2": SCORE_LEVELS[2],
                    },
                    "probabilities": {"0": 0.0, "1": 0.96, "2": 0.04},
                }
            },
        },
        ["0"],
    )
    assert reason is None
    assert scores is not None
    assert scores["0"].score == 1.04
    assert scores["0"].state == "evaluated"


def test_ptbr_consulta_preservada_no_estado():
    captured = {}

    def fake_post(_url, payload, **_kwargs):
        captured["query"] = payload["state"]["query"]
        return json.dumps(_envelope(["0", "1"], [1.0, 0.0]))

    try_rerank(
        [_hit("a"), _hit("b")],
        "onde está o avaliador de relevância?",
        [],
        new_usage(),
        environ=_openrouter_env(),
        post=fake_post,
    )
    assert captured["query"] == "onde está o avaliador de relevância?"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: (_envelope(["0"], [1.0]), "missing"),
        lambda: (_envelope(["0", "1", "2"], [1.0, 1.0, 1.0]), "extra"),
        lambda: (_envelope(["0", "1"], [float("nan"), 1.0]), "nan"),
        lambda: (_envelope(["0", "1"], [float("inf"), 1.0]), "inf"),
        lambda: (_envelope(["0", "1"], [3.0, 1.0]), "range"),
        lambda: (
            _envelope(
                ["0", "1"],
                [1.0, 1.0],
                extra={
                    "answers": {
                        "0": {"type": "score", "score": 1, "confidence": 0.9},
                        "1": {"type": "noul", "noul": 0.2},
                    }
                },
            ),
            "type",
        ),
    ],
)
def test_answers_invalidos_fazem_fallback_preservando_custo(factory):
    envelope, _kind = factory()
    usage = new_usage()
    warnings: list[str] = []
    result = try_rerank(
        [_hit("a"), _hit("b")],
        "q",
        warnings,
        usage,
        environ=_openrouter_env(),
        post=lambda *_a, **_k: json.dumps(envelope),
    )
    assert result is None
    assert usage.status == "fallback"
    assert "relevance_invalid_response" in warnings


def test_custo_zero_positivo_e_desconhecido():
    usage = new_usage()
    try_rerank(
        [_hit("a"), _hit("b")],
        "q",
        [],
        usage,
        environ=_openrouter_env(),
        post=lambda *_a, **_k: json.dumps(_envelope(["0", "1"], [1.0, 0.0], cost=0)),
    )
    assert usage.cost_status == "reported"
    assert usage.cost_usd == 0.0
    unknown = new_usage()
    warnings: list[str] = []
    envelope = _envelope(["0", "1"], [1.0, 0.0])
    envelope["usage"] = {"input_tokens": 1, "output_tokens": 1, "cost": "n/a"}
    try_rerank(
        [_hit("a"), _hit("b")],
        "q",
        warnings,
        unknown,
        environ=_openrouter_env(),
        post=lambda *_a, **_k: json.dumps(envelope),
    )
    assert unknown.cost_status == "unknown"
    assert unknown.cost_usd is None
    assert "relevance_cost_unknown" in warnings


def test_timeout_e_http_error_ficam_unknown():
    from urllib.error import URLError

    usage = new_usage()
    warnings: list[str] = []

    def timeout(*_a, **_k):
        raise URLError(TimeoutError("timed out"))

    assert (
        try_rerank(
            [_hit("a"), _hit("b")],
            "q",
            warnings,
            usage,
            environ=_openrouter_env(),
            post=timeout,
        )
        is None
    )
    assert usage.cost_status == "unknown"
    assert usage.request_count == 1
    assert usage.reason == "timeout"
    http_usage = new_usage()
    http_warnings: list[str] = []

    def unauthorized(*_a, **_k):
        raise HTTPError(
            "https://openrouter.ai/api/v1/systemone", 401, "no", hdrs=None, fp=None
        )

    try_rerank(
        [_hit("a"), _hit("b")],
        "q",
        http_warnings,
        http_usage,
        environ=_openrouter_env(),
        post=unauthorized,
    )
    assert http_usage.reason == "http_401"
    assert http_usage.cost_status == "unknown"


def test_jev_rejeita_redirect_e_resposta_grande():
    handler = _NoRedirectHandler()
    req = type("Req", (), {"full_url": "https://openrouter.ai/api/v1/systemone"})()
    with pytest.raises(HTTPError) as raised:
        handler.redirect_request(
            req, fp=None, code=302, msg="Found", headers={}, newurl="https://evil.example"
        )
    assert raised.value.code == 302

    class Huge:
        def read(self, n=-1):
            return b"x" * (n if n > 0 else 10)

    class FakeResponse:
        def __enter__(self):
            return Huge()

        def __exit__(self, *_args):
            return False

    def fake_open(_request, timeout):
        return FakeResponse()

    import codesteer_atlas.http_transport as transport

    original = transport.urlopen
    transport.urlopen = fake_open
    try:
        with pytest.raises(ValueError, match="response_too_large"):
            post_json("https://example.invalid", {"a": 1}, timeout_s=1, max_response_bytes=4)
    finally:
        transport.urlopen = original


def test_log_publico_nao_carrega_segredo_nem_path():
    usage = new_usage()
    usage.requested_model = "typesafe/jev-1.13"
    usage.resolved_model = "typesafe/jev-1.13-20260917"
    usage.provider = "TypeSafe"
    usage.request_id = "gen-dec-test"
    usage.cost_usd = 0.01
    usage.cost_status = "reported"
    dumped = json.dumps(public_usage_fields(usage))
    assert "sk-" not in dumped
    assert "src/" not in dumped
    assert usage.search_id in dumped
    assert dumped.count(RELEVANCE_RUBRIC_VERSION) == 1


def test_exemplo_mcp_default_off():
    from pathlib import Path

    payload = json.loads(
        (Path(__file__).resolve().parents[1] / "examples/clients/cursor/mcp.json").read_text(
            encoding="utf-8"
        )
    )
    env = payload["mcpServers"]["codesteer-atlas"]["env"]
    assert env["ATLAS_RELEVANCE"] == "0"
    assert env.get("ATLAS_CONTEXT_OPTIMIZATION", "0") == "0"
    assert "ATLAS_RELEVANCE_API_KEY" not in env


@pytest.mark.parametrize("confidence,changed,confident", [(0.1, False, 0), (0.99, True, 2)])
def test_processing_success_reports_ranking_evidence(confidence, changed, confident):
    usage = new_usage()
    pool = [_hit("alpha"), _hit("beta")]

    def post(_url, payload, **kwargs):
        ids = [c["id"] for c in payload["state"]["candidates"]]
        return json.dumps(_envelope(ids, [0, 2], confidences=[confidence] * 2))

    try_rerank(pool, "unrelated", [], usage, environ=_openrouter_env(), post=post)
    fields = public_usage_fields(usage)
    assert fields["status"] == "success"
    assert fields["ranking_changed"] is changed
    assert fields["confident_evaluations"] == confident


def test_exact_gate_is_opt_in_and_skips_without_remote_call():
    pool = [_hit("unrelated"), _hit("Owner.target")]
    from codesteer_atlas.relevance import exact_gate_reason

    assert exact_gate_reason(pool, "target", environ={}) is None
    assert exact_gate_reason(pool, "target", environ={"ATLAS_RELEVANCE_GATE": "1"}) == "unique_exact_symbol"
    usage = new_usage()
    result = try_rerank(pool, "target", [], usage,
                        environ=_openrouter_env(ATLAS_RELEVANCE_GATE="1"),
                        post=lambda *a, **k: pytest.fail("Não deve chamar rede"))
    assert result is None
    assert usage.status == "skipped"
    assert usage.reason == "unique_exact_symbol"
    assert usage.request_count == 0
    assert usage.cost_status == "not_incurred"
    assert usage.cost_usd == 0


@pytest.mark.parametrize("names,query,scope_type", [
    (["Owner.target", "Other.target"], "target", "function"),
    (["Owner.target"], "how target works", "function"),
    (["Owner.target"], "Target", "function"),
    (["Owner.target"], "tar", "function"),
    (["target"], "target", "section"),
])
def test_exact_gate_does_not_skip_ambiguous_or_non_symbol_queries(names, query, scope_type):
    from codesteer_atlas.relevance import exact_gate_reason

    pool = [_hit(name).model_copy(update={"scope_type": scope_type}) for name in names]
    assert exact_gate_reason(pool, query, environ={"ATLAS_RELEVANCE_GATE": "1"}) is None


def test_exact_gate_status_declares_configuration():
    gate = status_block(_openrouter_env(ATLAS_RELEVANCE_GATE="1"))["gate"]
    assert gate["enabled"] is True
    assert gate["policy"] == "unique_exact_symbol_v1"
    assert status_block(_openrouter_env())["gate"]["enabled"] is False
    assert status_block(_openrouter_env(ATLAS_RELEVANCE_GATE="bad"))["gate"]["reason"] == "invalid_flag"
