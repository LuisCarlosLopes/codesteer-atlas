"""Testes da origem, cache e sidecar semântico."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from codesteer_atlas.models import CodeChunk
from codesteer_atlas.origin import (
    OriginChoice,
    OriginResolver,
    OriginResult,
    _await_if_needed,
    _response_text,
)
from codesteer_atlas.semantic import (
    ProseGenerator,
    SemanticGeneration,
    build_sidecar,
    cache_key,
    content_hash,
    normalize_purpose,
    semantic_index_state,
    write_semantic_sidecar,
)


def _chunk(content="def run(): pass", path="src/app.py", name="run"):
    return CodeChunk(
        id="id",
        file_path=path,
        repo="demo",
        start_line=1,
        end_line=2,
        scope_type="function",
        scope_name=name,
        language="python",
        content=content,
        indexed_at="now",
        vector=[0.1] * 384,
    )


def test_normalize_purpose_cobre_envelope_e_v05():
    assert normalize_purpose({"what": "faz", "invariants": ["x", "y"]}) == "faz — x; y"
    assert normalize_purpose("  \n") == ""


def test_origin_awaitable_custom_fora_de_worker_anyio():
    class AwaitableResult:
        def __await__(self):
            async def result():
                return "propósito"
            return result().__await__()

    assert _await_if_needed(AwaitableResult()) == "propósito"


def test_origin_resolver_prioriza_sampling_e_descreve_egress():
    ctx = SimpleNamespace(sample=lambda **_kwargs: "purpose")
    resolver = OriginResolver(ctx=ctx, environ={"ATLAS_SEMANTIC_API_URL": "https://example.invalid"})
    assert resolver.resolve() == OriginChoice(
        "sampling", "Nenhum egresso adicional; o código já está no contexto do cliente MCP."
    )


def test_origin_resolver_falha_no_local_e_tenta_api(monkeypatch):
    resolver = OriginResolver(
        environ={
            "ATLAS_SEMANTIC_LOCAL_URL": "http://local",
            "ATLAS_SEMANTIC_API_URL": "http://api",
        }
    )
    calls = []

    def fake_http(url, payload, *, api):
        calls.append((url, api, payload["content"]))
        if not api:
            raise OSError("local indisponível")
        return "purpose"

    monkeypatch.setattr(resolver, "_call_http", fake_http)
    result = resolver.generate({"content": "c", "prompt": "p"})
    assert result == OriginResult(
        "purpose", "api", "Envia conteúdo e metadados mínimos ao endpoint de API explicitamente configurado."
    )
    assert calls == [("http://local", False, "c"), ("http://api", True, "c")]


def test_origin_normaliza_sampling_result_e_envelope_openai_sem_repr():
    assert _response_text(SimpleNamespace(text="propósito real", result="auxiliar")) == "propósito real"
    assert _response_text(SimpleNamespace(result="resultado real")) == "resultado real"
    assert _response_text({"choices": [{"message": {"content": "resposta API"}}]}) == "resposta API"

    resolver = OriginResolver(ctx=SimpleNamespace(sample=lambda **_kwargs: object()))
    assert resolver.generate({"prompt": "p"}) is None
    assert normalize_purpose(object()) == ""


def test_origin_api_com_model_usa_contrato_openai_compativel(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "propósito OpenRouter"}}]}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("codesteer_atlas.origin.urlopen", fake_urlopen)
    resolver = OriginResolver(
        environ={
            "ATLAS_SEMANTIC_API_URL": "https://openrouter.ai/api/v1/chat/completions",
            "ATLAS_SEMANTIC_API_KEY": "secret",
            "ATLAS_SEMANTIC_MODEL": "openai/gpt-4.1-mini",
        }
    )

    result = resolver.generate({"prompt": "Descreva run", "content": "def run(): pass"})

    assert result is not None
    assert result.text == "propósito OpenRouter"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"] == {
        "model": "openai/gpt-4.1-mini",
        "messages": [{"role": "user", "content": "Descreva run"}],
    }
    assert captured["timeout"] == 30.0


def test_origin_api_sem_model_preserva_payload_legado(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"text":"proposito legado"}'

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr("codesteer_atlas.origin.urlopen", fake_urlopen)
    resolver = OriginResolver(environ={"ATLAS_SEMANTIC_API_URL": "https://api.example"})
    payload = {"prompt": "p", "content": "c", "scope_name": "run"}

    result = resolver.generate(payload)

    assert result is not None
    assert result.text == "proposito legado"
    assert captured["payload"] == payload


def test_sidecar_acumula_origins_e_egresses_dos_sumarios(tmp_path):
    resolver = OriginResolver()
    responses = iter(
        [
            OriginResult("resumo de arquivo", "local", "egresso local"),
            OriginResult("resumo de camada", "api", "egresso API"),
        ]
    )

    def generate(_payload):
        result = next(responses)
        resolver._record_use(result)
        return result

    resolver.generate = generate
    payload = build_sidecar(
        tmp_path,
        [
            {
                "file_path": "src/app.py",
                "scope_name": "run",
                "scope_type": "function",
                "purpose": "faz algo",
                "purpose_hash": "hash",
            }
        ],
        SemanticGeneration(status="ok"),
        resolver,
    )

    assert payload["origins"] == ["local", "api"]
    assert payload["egresses"] == ["egresso local", "egresso API"]
    assert payload["origin"] == "mixed"
    assert payload["last_generation"]["origins"] == ["local", "api"]


def test_prose_generator_reusa_cache_por_conteudo_e_nao_por_linhas(monkeypatch):
    chunk = _chunk()
    cached = {
        cache_key(chunk): {
            "purpose": "propósito antigo",
            "purpose_hash": content_hash(chunk.content),
            "purpose_vector": [0.2] * 384,
        }
    }
    resolver = Mock(spec=OriginResolver)
    generator = ProseGenerator(resolver)
    stats = generator.generate_purposes([chunk], cached)
    assert stats.reused == 1
    assert stats.generated == 0
    resolver.generate.assert_not_called()


def test_prose_generator_descarta_v05_e_documento(monkeypatch):
    resolver = Mock(spec=OriginResolver)
    resolver.resolve.return_value = OriginChoice("local", "host")
    resolver.generate.return_value = OriginResult("  ", "local", "host")
    chunks = [_chunk(), _chunk(path="README.md", name="Guide")]
    chunks[1].scope_type = "section"
    stats = ProseGenerator(resolver, embedding_engine=Mock()).generate_purposes(chunks)
    assert stats.failed == 1
    assert stats.generated == 0
    assert chunks[0].purpose is None
    assert chunks[1].purpose is None
    assert resolver.generate.call_count == 1


def test_sidecar_atomico_tem_sumarios_condicionados_a_proposito(tmp_path):
    resolver = Mock(spec=OriginResolver)
    resolver.generate.return_value = OriginResult("summary", "local", "host")
    rows = [
        {
            "file_path": "src/app.py",
            "scope_name": "run",
            "scope_type": "function",
            "content": "def run(): pass",
            "purpose": "faz algo",
            "purpose_hash": "hash",
        }
    ]
    payload = build_sidecar(
        tmp_path,
        rows,
        SemanticGeneration(status="ok", generated=1, origin="local", egress="host"),
        resolver,
    )
    assert payload["usable_purpose_count"] == 1
    assert payload["file_summaries"]["src/app.py"]["summary"] == "summary"
    assert payload["layer_summaries"]["src"]["summary"] == "summary"
    assert not (tmp_path / "semantic.json.tmp").exists()


def test_sidecar_42_isola_filhos_e_invalida_somente_ancestrais_afetados(tmp_path):
    """4.2 ignora filhos sem prosa e invalida apenas arquivo/camada dependentes."""
    resolver = Mock(spec=OriginResolver)
    resolver.describe.return_value = ("local", "host")
    resolver.used_origins = []
    resolver.used_egresses = []
    resolver.generate.side_effect = lambda payload: OriginResult(
        f"summary:{payload['scope_type']}:{payload['scope_name']}:{payload['content']}",
        "local",
        "host",
    )
    rows = [
        {
            "file_path": "src/a.py",
            "scope_name": "a1",
            "purpose": "faz A1",
            "purpose_hash": "a1-v1",
        },
        {
            "file_path": "src/a.py",
            "scope_name": "a2",
            "purpose": "",
            "purpose_hash": "a2-v1",
        },
        {
            "file_path": "lib/b.py",
            "scope_name": "b1",
            "purpose": "faz B1",
            "purpose_hash": "b1-v1",
        },
        {
            "file_path": "docs/guide.md",
            "scope_name": "Guide",
            "purpose": None,
            "purpose_hash": None,
        },
    ]

    first = build_sidecar(
        tmp_path, rows, SemanticGeneration(status="ok", generated=2), resolver
    )
    assert set(first["file_summaries"]) == {"src/a.py", "lib/b.py"}
    assert set(first["layer_summaries"]) == {"src", "lib"}
    assert first["last_generation"] == {
        "status": "ok",
        "origin": None,
        "egress": None,
        "origins": [],
        "egresses": [],
        "semantic_generated": 2,
        "semantic_reused": 0,
        "semantic_file_generated": 2,
        "semantic_file_reused": 0,
        "semantic_layer_generated": 2,
        "semantic_layer_reused": 0,
    }

    resolver.generate.reset_mock()
    unchanged = build_sidecar(
        tmp_path, rows, SemanticGeneration(status="ok", reused=2), resolver, first
    )
    resolver.generate.assert_not_called()
    assert unchanged["last_generation"]["semantic_file_reused"] == 2
    assert unchanged["last_generation"]["semantic_layer_reused"] == 2

    changed_rows = [dict(row) for row in rows]
    changed_rows[0]["purpose"] = "faz A1 alterado"
    changed_rows[0]["purpose_hash"] = "a1-v2"
    resolver.generate.reset_mock()
    changed = build_sidecar(
        tmp_path,
        changed_rows,
        SemanticGeneration(status="ok", generated=1, reused=1),
        resolver,
        unchanged,
    )

    assert resolver.generate.call_count == 2
    scopes = {
        (call.args[0]["scope_type"], call.args[0]["scope_name"])
        for call in resolver.generate.call_args_list
    }
    assert scopes == {("arquivo", "src/a.py"), ("camada", "src")}
    assert changed["file_summaries"]["lib/b.py"] == unchanged["file_summaries"]["lib/b.py"]
    assert changed["layer_summaries"]["lib"] == unchanged["layer_summaries"]["lib"]
    assert changed["last_generation"]["semantic_file_generated"] == 1
    assert changed["last_generation"]["semantic_file_reused"] == 1
    assert changed["last_generation"]["semantic_layer_generated"] == 1
    assert changed["last_generation"]["semantic_layer_reused"] == 1


def test_semantic_index_state_distingue_legado_ready_e_no_prose(tmp_path):
    manifest = SimpleNamespace(index_version="2.2.0")
    assert semantic_index_state(tmp_path, manifest) == ("legacy", "index_version_below_2_3_0")
    manifest.index_version = "2.3.0"
    assert semantic_index_state(tmp_path, manifest) == ("absent", "sidecar_unreadable")
    write_semantic_sidecar(tmp_path, {"usable_purpose_count": 1})
    assert semantic_index_state(tmp_path, manifest) == ("ready", None)
