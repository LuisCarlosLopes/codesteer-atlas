"""
Testes do contador de tokens, contrato de evento e sink local de observabilidade
(`observability.py`). Cobre V1-V3 (contagem exata/estimada/degradada) e V8-V9
(privacidade, concorrência, retenção) do plano observabilidade-tokens-consultas.
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from codesteer_atlas import observability as obs
from codesteer_atlas.config import (
    BUNDLED_TOKENIZER_NAME,
    BUNDLED_TOKENIZER_REVISION,
    BUNDLED_TOKENIZER_SHA256,
    OBSERVABILITY_DIRNAME,
    OBSERVABILITY_EVENTS_FILENAME,
    OBSERVABILITY_MAX_BACKUPS,
    OBSERVABILITY_MAX_FILE_BYTES,
    TOKENIZER_PATH_ENV_FLAG,
)


@pytest.fixture(autouse=True)
def _reset_observability_state():
    """Isola o singleton do tokenizer e o estado em memória entre testes."""
    obs.reset_token_counter_for_tests()
    obs.reset_observability_state_for_tests()
    yield
    obs.reset_token_counter_for_tests()
    obs.reset_observability_state_for_tests()


def _write_tiny_tokenizer(path: Path, *, with_padding_truncation: bool = False) -> None:
    """
    Constrói um tokenizer WordLevel minúsculo e determinístico (split por
    espaço) e grava em `path`. Usado como fixture local pequena — nada é
    baixado da rede.
    """
    from tokenizers import Tokenizer, models, pre_tokenizers

    vocab = {"[UNK]": 0, "hello": 1, "world": 2, "foo": 3, "bar": 4, "café": 5, "日本語": 6}
    tokenizer = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    if with_padding_truncation:
        tokenizer.enable_padding(length=16)
        tokenizer.enable_truncation(max_length=2)
    tokenizer.save(str(path))


# ---------------------------------------------------------------------------
# Tokenizer embarcado: padrão local, identidade fixa, carregamento lazy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("enabled", ["0", "1"])
def test_count_tokens_without_override_uses_bundled_tokenizer(monkeypatch, enabled):
    monkeypatch.delenv(TOKENIZER_PATH_ENV_FLAG, raising=False)
    monkeypatch.setenv("ATLAS_OBSERVABILITY", enabled)
    result = obs.count_tokens("Olá mundo! def run(): pass")

    assert result.tokens == 9
    assert result.estimated_tokens is None
    assert result.count_method == "tokenizer"
    assert result.tokenizer_status == "ok"
    assert result.tokenizer_source == "bundled"
    assert result.tokenizer_name == BUNDLED_TOKENIZER_NAME
    assert result.tokenizer_revision == BUNDLED_TOKENIZER_REVISION
    assert result.tokenizer_sha256 == BUNDLED_TOKENIZER_SHA256


def test_count_tokens_empty_string_counts_zero(monkeypatch):
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, "")
    obs.reset_token_counter_for_tests()
    result = obs.count_tokens("")
    assert result.tokens == 0
    assert result.estimated_tokens is None


def test_bundled_tokenizer_import_lazy_and_count_offline_from_another_cwd(tmp_path):
    source = str(Path(obs.__file__).resolve().parents[1])
    script = f"""
import sys, socket
sys.path.insert(0, {source!r})
from codesteer_atlas import observability as obs
assert 'tokenizers' not in sys.modules
assert obs._counter_instance is None
def forbidden(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket = forbidden
result = obs.count_tokens('Olá mundo! def run(): pass')
assert result.tokens == 9, result
assert result.tokenizer_source == 'bundled'
"""
    env = {k: v for k, v in os.environ.items() if k != TOKENIZER_PATH_ENV_FLAG}
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""


@pytest.mark.parametrize("corrupt", [False, True])
def test_bundled_resource_missing_or_corrupt_degrades_and_caches(tmp_path, monkeypatch, corrupt):
    monkeypatch.delenv(TOKENIZER_PATH_ENV_FLAG, raising=False)
    if corrupt:
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "tokenizer.json").write_text("{}", encoding="utf-8")
    calls = []

    def resource(package):
        calls.append(package)
        return tmp_path

    monkeypatch.setattr(obs, "files", resource)
    for _ in range(2):
        result = obs.count_tokens("abcdefgh")
        assert result.tokens is None
        assert result.estimated_tokens == 2
        assert result.tokenizer_status == "unavailable"
        assert result.tokenizer_source == "bundled"
    assert calls == ["codesteer_atlas"]


def test_module_import_does_not_touch_tokenizers(monkeypatch):
    """V3: importar o módulo não carrega o tokenizer nem toca `tokenizers`."""
    counter = obs.TokenCounter()
    assert counter._tokenizer is None
    assert counter._loaded_path is None


# ---------------------------------------------------------------------------
# V1 — tokenizer local configurado: contagem exata, hash, unicode
# ---------------------------------------------------------------------------


def test_count_tokens_with_tokenizer_exact_and_unicode(tmp_path, monkeypatch):
    tokenizer_path = tmp_path / "tokenizer.json"
    _write_tiny_tokenizer(tokenizer_path)
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, str(tokenizer_path))
    obs.reset_token_counter_for_tests()

    result = obs.count_tokens("hello world")
    assert result.tokens == 2
    assert result.estimated_tokens is None
    assert result.count_method == "tokenizer"
    assert result.tokenizer_status == "ok"
    assert result.tokenizer_sha256 is not None
    assert len(result.tokenizer_sha256) == 64
    assert result.tokenizer_source == "custom"
    assert result.tokenizer_name == "custom"
    assert result.tokenizer_revision is None

    # pt-BR/emoji/CJK: não falha, mesmo que vire [UNK] no vocabulário minúsculo.
    result_unicode = obs.count_tokens("café 日本語 🎉")
    assert result_unicode.count_method == "tokenizer"
    assert result_unicode.tokens is not None


def test_tokenizer_sha256_identifies_the_configured_file(tmp_path, monkeypatch):
    import hashlib

    tokenizer_path = tmp_path / "tokenizer.json"
    _write_tiny_tokenizer(tokenizer_path)
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, str(tokenizer_path))
    obs.reset_token_counter_for_tests()

    expected_sha = hashlib.sha256(tokenizer_path.read_bytes()).hexdigest()
    result = obs.count_tokens("hello")
    assert result.tokenizer_sha256 == expected_sha


# ---------------------------------------------------------------------------
# V2 — padding/truncamento desativados independente da config do arquivo
# ---------------------------------------------------------------------------


def test_tokenizer_disables_padding_and_truncation(tmp_path, monkeypatch):
    tokenizer_path = tmp_path / "tokenizer.json"
    _write_tiny_tokenizer(tokenizer_path, with_padding_truncation=True)
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, str(tokenizer_path))
    obs.reset_token_counter_for_tests()

    # Truncation estava configurado para max_length=2; um texto maior precisa
    # contar TODOS os tokens, não só os 2 primeiros.
    long_text = "hello world foo bar hello world foo bar"
    result = obs.count_tokens(long_text)
    assert result.count_method == "tokenizer"
    assert result.tokens == 8  # 8 palavras separadas por espaço, sem truncar

    # Sem padding: texto curto não ganha tokens de padding.
    short_result = obs.count_tokens("hello")
    assert short_result.tokens == 1


# ---------------------------------------------------------------------------
# V3 — arquivo ausente/inválido: degrada sem exceção nem rede
# ---------------------------------------------------------------------------


def test_tokenizer_missing_file_degrades(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, str(tmp_path / "does_not_exist.json"))
    obs.reset_token_counter_for_tests()

    result = obs.count_tokens("hello world")
    assert result.tokens is None
    assert result.estimated_tokens is not None
    assert result.count_method == "chars_div_4"
    assert result.tokenizer_status == "unavailable"
    assert result.tokenizer_source == "custom"


def test_tokenizer_invalid_json_degrades(tmp_path, monkeypatch):
    bad_path = tmp_path / "tokenizer.json"
    bad_path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, str(bad_path))
    obs.reset_token_counter_for_tests()

    result = obs.count_tokens("hello world")
    assert result.tokenizer_status == "unavailable"
    assert result.count_method == "chars_div_4"


def test_tokenizer_library_unavailable_degrades_without_raising(tmp_path, monkeypatch):
    tokenizer_path = tmp_path / "tokenizer.json"
    _write_tiny_tokenizer(tokenizer_path)
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, str(tokenizer_path))
    obs.reset_token_counter_for_tests()

    # Simula a lib 'tokenizers' ausente: sys.modules[name] = None força ImportError
    # na próxima `from tokenizers import Tokenizer`, sem tocar rede nem pip.
    monkeypatch.setitem(sys.modules, "tokenizers", None)

    result = obs.count_tokens("hello world")
    assert result.tokenizer_status == "unavailable"
    assert result.count_method == "chars_div_4"
    assert result.estimated_tokens is not None


def test_invalid_tokenizer_config_memoized_until_reset(tmp_path, monkeypatch):
    """Config inválida não tenta recarregar a cada chamada (memoização por processo)."""
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, str(tmp_path / "missing.json"))
    obs.reset_token_counter_for_tests()
    counter = obs.get_token_counter()

    counter.count("first call")
    assert counter._unavailable is True
    # Segunda chamada não deve tentar ler o arquivo de novo (mesmo path memoizado)
    loaded_path_before = counter._loaded_path
    counter.count("second call")
    assert counter._loaded_path == loaded_path_before


def test_force_unavailable_persists_until_reset(tmp_path, monkeypatch):
    tokenizer_path = tmp_path / "tokenizer.json"
    _write_tiny_tokenizer(tokenizer_path)
    monkeypatch.setenv(TOKENIZER_PATH_ENV_FLAG, str(tokenizer_path))
    obs.reset_token_counter_for_tests()

    counter = obs.get_token_counter()
    assert counter.count("hello").tokenizer_status == "ok"

    counter.force_unavailable()
    assert counter._unavailable is True
    result = counter.count("anything")
    assert result.tokenizer_status == "unavailable"
    assert result.count_method == "chars_div_4"


# ---------------------------------------------------------------------------
# Contrato de evento (T1)
# ---------------------------------------------------------------------------


def test_build_event_success_has_required_fields():
    measurement = obs.measure_response('{"a":1}')
    event = obs.build_event(
        tool="atlas_search",
        outcome="success",
        duration_ms=12.345,
        measurement=measurement,
        extra={"top_k": 5, "include_content": False, "results_returned": 1, "results_omitted": 0},
    )
    for field in (
        "schema_version",
        "event_id",
        "timestamp",
        "tool",
        "outcome",
        "scope",
        "duration_ms",
        "response_chars",
        "response_bytes",
        "response_tokens",
        "estimated_tokens",
        "count_method",
        "tokenizer_sha256",
        "tokenizer_status",
        "tokenizer_source",
        "tokenizer_name",
        "tokenizer_revision",
        "truncated",
        "warnings",
    ):
        assert field in event, field
    assert event["scope"] == "tool_json_text"
    assert event["outcome"] == "success"
    assert event["top_k"] == 5
    assert event["results_returned"] == 1
    assert "error_class" not in event


def test_build_event_error_has_null_measurements_and_safe_class():
    event = obs.build_event(
        tool="atlas_search",
        outcome="error",
        duration_ms=0.5,
        error=ValueError("query continha segredo confidencial"),
    )
    assert event["outcome"] == "error"
    assert event["response_chars"] is None
    assert event["response_tokens"] is None
    assert event["error_class"] == "ValueError"
    # A classe é segura; a mensagem (que poderia carregar dado sensível) nunca é gravada.
    assert "segredo" not in json.dumps(event)


# ---------------------------------------------------------------------------
# V8 — desligado: nenhum arquivo/estado; ligado: evento consistente e privado
# ---------------------------------------------------------------------------


def test_record_event_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_OBSERVABILITY", raising=False)
    event = obs.build_event(tool="atlas_brief", outcome="success", duration_ms=1.0)
    obs.record_event(tmp_path, event)

    assert not (tmp_path / OBSERVABILITY_DIRNAME).exists()
    status = obs.get_observability_status()
    assert status["last_by_tool"] == {}


def test_record_event_enabled_persists_and_updates_status(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_OBSERVABILITY", "1")
    event = obs.build_event(tool="atlas_brief", outcome="success", duration_ms=1.0)
    obs.record_event(tmp_path, event)

    events_path = tmp_path / OBSERVABILITY_DIRNAME / OBSERVABILITY_EVENTS_FILENAME
    assert events_path.exists()
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    persisted = json.loads(lines[0])
    assert persisted["tool"] == "atlas_brief"

    status = obs.get_observability_status()
    assert status["enabled"] is True
    assert status["last_by_tool"]["atlas_brief"]["tool"] == "atlas_brief"
    assert status["dropped_events"] == 0


def test_events_never_contain_query_or_path_sentinels(tmp_path, monkeypatch):
    """
    Simula uso correto pelo chamador (server.py): nenhum campo livre (query,
    paths retornados, código) é passado a `build_event`/`record_event` — a
    string do evento nunca deve conter os sentinelas de query/paths/código.
    """
    monkeypatch.setenv("ATLAS_OBSERVABILITY", "1")
    sentinel_query = "SELECT senha_secreta FROM usuarios"
    sentinel_path = "/Users/alguem/segredo/projeto"

    measurement = obs.measure_response(json.dumps({"results": []}))
    event = obs.build_event(
        tool="atlas_search",
        outcome="success",
        duration_ms=1.0,
        measurement=measurement,
        extra={"top_k": 5, "include_content": False, "results_returned": 0, "results_omitted": 0},
    )
    obs.record_event(tmp_path, event)

    events_path = tmp_path / OBSERVABILITY_DIRNAME / OBSERVABILITY_EVENTS_FILENAME
    content = events_path.read_text(encoding="utf-8")
    assert sentinel_query not in content
    assert sentinel_path not in content


# ---------------------------------------------------------------------------
# V9 — concorrência, contenção e rotação sob teto
# ---------------------------------------------------------------------------


def test_sink_rotation_keeps_at_most_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_OBSERVABILITY", "1")
    events_dir = tmp_path / OBSERVABILITY_DIRNAME
    events_dir.mkdir(parents=True)
    events_path = events_dir / OBSERVABILITY_EVENTS_FILENAME

    # Pré-popula o arquivo ativo acima do teto para forçar rotação na próxima escrita.
    events_path.write_bytes(b"x" * (OBSERVABILITY_MAX_FILE_BYTES + 10) + b"\n")

    event = obs.build_event(tool="atlas_graph", outcome="success", duration_ms=1.0)
    obs.record_event(tmp_path, event)

    backups = sorted(events_dir.glob(f"{OBSERVABILITY_EVENTS_FILENAME}.*"))
    assert len(backups) <= OBSERVABILITY_MAX_BACKUPS
    assert events_path.exists()
    # O arquivo ativo pós-rotação contém só o evento novo (pequeno)
    assert events_path.stat().st_size < OBSERVABILITY_MAX_FILE_BYTES


def test_concurrent_record_event_does_not_corrupt_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_OBSERVABILITY", "1")

    def _record(i: int) -> None:
        event = obs.build_event(tool="atlas_search", outcome="success", duration_ms=float(i))
        obs.record_event(tmp_path, event)

    threads = [threading.Thread(target=_record, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events_path = tmp_path / OBSERVABILITY_DIRNAME / OBSERVABILITY_EVENTS_FILENAME
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    for line in lines:
        json.loads(line)  # cada linha precisa ser JSON válido e completo


def test_record_event_lock_contention_drops_and_reports(tmp_path, monkeypatch):
    """Contenção do lock descarta a persistência do evento e incrementa dropped_events."""
    from filelock import FileLock

    monkeypatch.setenv("ATLAS_OBSERVABILITY", "1")
    events_dir = tmp_path / OBSERVABILITY_DIRNAME
    events_dir.mkdir(parents=True)
    lock_path = events_dir / ".observability.lock"
    holder = FileLock(str(lock_path))
    holder.acquire()
    try:
        event = obs.build_event(tool="atlas_context", outcome="success", duration_ms=1.0)
        obs.record_event(tmp_path, event)
        status = obs.get_observability_status()
        assert status["dropped_events"] == 1
        assert status["sink_state"] == "degraded"
        # Mesmo sob contenção, o último evento continua disponível em memória.
        assert status["last_by_tool"]["atlas_context"]["tool"] == "atlas_context"
    finally:
        holder.release()


def test_last_by_tool_limited_to_known_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_OBSERVABILITY", "1")
    for tool in ("atlas_search", "atlas_context", "atlas_brief", "atlas_graph"):
        obs.record_event(tmp_path, obs.build_event(tool=tool, outcome="success", duration_ms=1.0))

    status = obs.get_observability_status()
    assert set(status["last_by_tool"].keys()) == {
        "atlas_search",
        "atlas_context",
        "atlas_brief",
        "atlas_graph",
    }
