"""
Observabilidade local de tokens por consulta (opt-in via `ATLAS_OBSERVABILITY=1`).

Mede a string JSON final devolvida pelas quatro tools de recuperação
(`atlas_search`, `atlas_context`, `atlas_brief`, `atlas_graph`): caracteres/bytes
exatos e tokens (exatos via tokenizer embarcado ou `ATLAS_TOKENIZER_PATH`,
ou estimativa `ceil(chars/4)` sempre identificada como tal). Nunca mede
código-fonte, texto de query ou paths — apenas o tamanho/forma da resposta já
serializada (escopo `tool_json_text`, D1 da arquitetura).

O contador de tokens (`count_tokens`) é INDEPENDENTE do flag de observabilidade:
o orçamento de resposta (`response_budget.py`) usa o tokenizer local
mesmo com `ATLAS_OBSERVABILITY` desligado. Só o registro
de eventos (memória + JSONL + bloco em `atlas_status`) é que liga/desliga com
`ATLAS_OBSERVABILITY`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Optional

from filelock import FileLock, Timeout

from codesteer_atlas.config import (
    BUNDLED_TOKENIZER_NAME,
    BUNDLED_TOKENIZER_REVISION,
    BUNDLED_TOKENIZER_SHA256,
    OBSERVABILITY_DIRNAME,
    OBSERVABILITY_ENV_FLAG,
    OBSERVABILITY_EVENT_SCHEMA_VERSION,
    OBSERVABILITY_EVENTS_FILENAME,
    OBSERVABILITY_LOCK_FILENAME,
    OBSERVABILITY_LOCK_TIMEOUT_S,
    OBSERVABILITY_MAX_BACKUPS,
    OBSERVABILITY_MAX_FILE_BYTES,
    TOKEN_ESTIMATE_DIVISOR,
    TOKENIZER_PATH_ENV_FLAG,
)

# ---------------------------------------------------------------------------
# Contador de tokens (T1) — lazy, singleton por processo (D2 da arquitetura)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenCount:
    tokens: Optional[int]
    estimated_tokens: Optional[int]
    count_method: str  # "tokenizer" | "chars_div_4"
    tokenizer_status: str  # "ok" | "unavailable"
    tokenizer_sha256: Optional[str]
    tokenizer_source: Optional[str] = None
    tokenizer_name: Optional[str] = None
    tokenizer_revision: Optional[str] = None


class TokenCounter:
    """
    Contador local de tokens para a string final de uma tool.

    Carrega o tokenizer apenas na primeira chamada que precise dele — nunca
    no import. `ATLAS_TOKENIZER_PATH` substitui o recurso embarcado, sem rede.
    Cache por processo:
    trocar o arquivo exige reiniciar o servidor (documentado no README).
    Configuração inválida (arquivo ausente, ilegível, ou lib `tokenizers`
    ausente) é memorizada como indisponível até reinício, evitando nova
    tentativa a cada consulta.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded_path: Optional[str] = None
        self._tokenizer: Any = None
        self._sha256: Optional[str] = None
        self._unavailable = False

    def _configured_path(self) -> Optional[str]:
        return os.environ.get(TOKENIZER_PATH_ENV_FLAG) or None

    def force_unavailable(self) -> None:
        """
        Memoriza a degradação até reinício quando até o envelope mínimo excede
        o teto de tokens exatos (D2/T2): a partir daqui `count` sempre estima.
        """
        with self._lock:
            self._unavailable = True

    def _ensure_loaded(self, path: Optional[str]) -> None:
        if self._loaded_path == path and (self._tokenizer is not None or self._unavailable):
            return
        with self._lock:
            if self._loaded_path == path and (self._tokenizer is not None or self._unavailable):
                return
            self._loaded_path = path
            self._tokenizer = None
            self._sha256 = None
            self._unavailable = False

            try:
                file_path = (
                    Path(path) if path else files("codesteer_atlas").joinpath("assets/tokenizer.json")
                )
                data = file_path.read_bytes()
            except OSError as e:
                print(f"[atlas] Falha ao ler tokenizer '{path or 'embarcado'}': {e}", file=sys.stderr)
                self._unavailable = True
                return
            self._sha256 = hashlib.sha256(data).hexdigest()
            if path is None and self._sha256 != BUNDLED_TOKENIZER_SHA256:
                print("[atlas] Tokenizer embarcado: SHA-256 inesperado.", file=sys.stderr)
                self._unavailable = True
                return

            try:
                from tokenizers import Tokenizer  # import pesado só aqui (lazy)
            except Exception as e:
                print(f"[atlas] Biblioteca 'tokenizers' indisponível: {e}", file=sys.stderr)
                self._unavailable = True
                return
            try:
                tokenizer = Tokenizer.from_str(data.decode("utf-8"))
                tokenizer.no_padding()
                tokenizer.no_truncation()
            except Exception as e:
                print(f"[atlas] Falha ao carregar tokenizer '{file_path}': {e}", file=sys.stderr)
                self._unavailable = True
                return
            self._tokenizer = tokenizer

    def count(self, text: str) -> TokenCount:
        chars = len(text)
        estimated = math.ceil(chars / TOKEN_ESTIMATE_DIVISOR) if chars else 0

        path = self._configured_path()
        self._ensure_loaded(path)
        result = TokenCount(
            None, estimated, "chars_div_4", "unavailable", self._sha256,
            tokenizer_source="custom" if path else "bundled",
            tokenizer_name="custom" if path else BUNDLED_TOKENIZER_NAME,
            tokenizer_revision=None if path else BUNDLED_TOKENIZER_REVISION,
        )
        if self._unavailable or self._tokenizer is None:
            return result

        try:
            encoding = self._tokenizer.encode(text, add_special_tokens=False)
            return replace(
                result, tokens=len(encoding.ids), estimated_tokens=None,
                count_method="tokenizer", tokenizer_status="ok",
            )
        except Exception as e:
            print(f"[atlas] Falha ao contar tokens com tokenizer local: {e}", file=sys.stderr)
            with self._lock:
                self._unavailable = True
            return result


_counter_lock = threading.Lock()
_counter_instance: Optional[TokenCounter] = None


def get_token_counter() -> TokenCounter:
    global _counter_instance
    if _counter_instance is None:
        with _counter_lock:
            if _counter_instance is None:
                _counter_instance = TokenCounter()
    return _counter_instance


def count_tokens(text: str) -> TokenCount:
    return get_token_counter().count(text)


def reset_token_counter_for_tests() -> None:
    """Só para testes: descarta o singleton para simular reinício do processo."""
    global _counter_instance
    _counter_instance = None


# ---------------------------------------------------------------------------
# Medição da resposta final (compartilhada com response_budget.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponseMeasurement:
    chars: int
    bytes: int
    tokens: Optional[int]
    estimated_tokens: Optional[int]
    count_method: str
    tokenizer_status: str
    tokenizer_sha256: Optional[str]
    tokenizer_source: Optional[str] = None
    tokenizer_name: Optional[str] = None
    tokenizer_revision: Optional[str] = None


def measure_response(text: str) -> ResponseMeasurement:
    count = count_tokens(text)
    return ResponseMeasurement(
        chars=len(text),
        bytes=len(text.encode("utf-8")),
        tokens=count.tokens,
        estimated_tokens=count.estimated_tokens,
        count_method=count.count_method,
        tokenizer_status=count.tokenizer_status,
        tokenizer_sha256=count.tokenizer_sha256,
        tokenizer_source=count.tokenizer_source,
        tokenizer_name=count.tokenizer_name,
        tokenizer_revision=count.tokenizer_revision,
    )


def degrade_measurement_to_bytes(measurement: ResponseMeasurement) -> ResponseMeasurement:
    """Degrada uma medição para a modalidade conservadora por bytes (D2/T2)."""
    return replace(
        measurement,
        tokens=None,
        count_method="chars_div_4",
        tokenizer_status="unavailable",
    )


# ---------------------------------------------------------------------------
# Contrato de evento v1 (T1) e registro local com retenção limitada (T3)
# ---------------------------------------------------------------------------


def observability_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    values = environ if environ is not None else os.environ
    return values.get(OBSERVABILITY_ENV_FLAG) == "1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_event(
    *,
    tool: str,
    outcome: str,
    duration_ms: float,
    measurement: Optional[ResponseMeasurement] = None,
    error: Optional[BaseException] = None,
    truncated: bool = False,
    warnings: Optional[list] = None,
    extra: Optional[dict] = None,
) -> dict:
    """
    Monta o evento v1. Campos de medição de resposta são `None` em erro — a
    tool não produziu uma string a medir (D1: nunca mede o envelope de erro do
    FastMCP). `warnings` aceita só códigos conhecidos do próprio evento
    (tokenizer_unavailable/truncated_for_budget); nunca texto livre.
    """
    event: dict = {
        "schema_version": OBSERVABILITY_EVENT_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "timestamp": _now_iso(),
        "tool": tool,
        "outcome": outcome,
        "scope": "tool_json_text",
        "duration_ms": round(duration_ms, 3),
    }

    if measurement is not None:
        event.update(
            {
                "response_chars": measurement.chars,
                "response_bytes": measurement.bytes,
                "response_tokens": measurement.tokens,
                "estimated_tokens": measurement.estimated_tokens,
                "count_method": measurement.count_method,
                "tokenizer_sha256": measurement.tokenizer_sha256,
                "tokenizer_status": measurement.tokenizer_status,
                "tokenizer_source": measurement.tokenizer_source,
                "tokenizer_name": measurement.tokenizer_name,
                "tokenizer_revision": measurement.tokenizer_revision,
            }
        )
    else:
        event.update(
            {
                "response_chars": None,
                "response_bytes": None,
                "response_tokens": None,
                "estimated_tokens": None,
                "count_method": None,
                "tokenizer_sha256": None,
                "tokenizer_status": None,
                "tokenizer_source": None,
                "tokenizer_name": None,
                "tokenizer_revision": None,
            }
        )

    event["truncated"] = bool(truncated)
    event["warnings"] = sorted(set(warnings or []))

    if error is not None:
        # Classe segura: nunca a mensagem (poderia carregar query/path/segredo).
        event["error_class"] = type(error).__name__

    if extra:
        event.update(extra)

    return event


# --- Estado em memória (por processo) e sink JSONL com retenção limitada ---

_STATE_LOCK = threading.Lock()
_LAST_EVENTS: dict = {}
_DROPPED_EVENTS = 0
_SINK_STATE = "ok"  # "ok" | "degraded"


def reset_observability_state_for_tests() -> None:
    global _DROPPED_EVENTS, _SINK_STATE
    with _STATE_LOCK:
        _LAST_EVENTS.clear()
        _DROPPED_EVENTS = 0
        _SINK_STATE = "ok"


def _events_dir(index_dir: Path) -> Path:
    return Path(index_dir) / OBSERVABILITY_DIRNAME


def _events_path(index_dir: Path) -> Path:
    return _events_dir(index_dir) / OBSERVABILITY_EVENTS_FILENAME


def _rotate_if_needed(events_path: Path) -> None:
    """Rotaciona `events.jsonl` -> `.1` -> `.2` (descarta o mais antigo) sob o teto."""
    if not events_path.is_file():
        return
    if events_path.stat().st_size < OBSERVABILITY_MAX_FILE_BYTES:
        return
    oldest = events_path.with_name(f"{events_path.name}.{OBSERVABILITY_MAX_BACKUPS}")
    if oldest.exists():
        oldest.unlink()
    for i in range(OBSERVABILITY_MAX_BACKUPS - 1, 0, -1):
        src = events_path.with_name(f"{events_path.name}.{i}")
        if src.exists():
            src.rename(events_path.with_name(f"{events_path.name}.{i + 1}"))
    events_path.rename(events_path.with_name(f"{events_path.name}.1"))


def _persist_event(index_dir: Path, event: dict) -> bool:
    """
    Persiste um evento sob lock exclusivo próprio (não o de reindex).
    `timeout=0`: contenção descarta a persistência deste evento sem esperar.
    """
    events_path = _events_path(index_dir)
    lock_path = _events_dir(index_dir) / OBSERVABILITY_LOCK_FILENAME
    try:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(lock_path), timeout=OBSERVABILITY_LOCK_TIMEOUT_S)
        with lock:
            _rotate_if_needed(events_path)
            line = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
            with open(events_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Timeout:
        return False
    except OSError as e:
        print(f"[atlas] Sink de observabilidade: falha de E/S: {e}", file=sys.stderr)
        return False


def _log_sink_transition(state: str) -> None:
    if state == "degraded":
        print(
            "[atlas] Observabilidade: sink degradado (contenção ou falha de E/S); "
            "eventos seguem só em memória até a próxima persistência bem-sucedida.",
            file=sys.stderr,
        )
    else:
        print("[atlas] Observabilidade: sink recuperado.", file=sys.stderr)


def record_event(index_dir: Path, event: dict) -> None:
    """
    Guarda `event` em memória (último por tool) e tenta persistir em JSONL.
    No-op completo quando `ATLAS_OBSERVABILITY` não é `"1"` (DOD3): nenhum
    arquivo é criado, nenhum estado é tocado.
    """
    if not observability_enabled():
        return

    global _DROPPED_EVENTS, _SINK_STATE

    with _STATE_LOCK:
        _LAST_EVENTS[event["tool"]] = event

    try:
        ok = _persist_event(Path(index_dir), event)
    except Exception as e:
        # Erro de observabilidade nunca propaga para a tool (guardrail do IPD).
        print(f"[atlas] Observabilidade: erro inesperado no sink: {e}", file=sys.stderr)
        ok = False

    with _STATE_LOCK:
        if ok:
            if _SINK_STATE != "ok":
                _SINK_STATE = "ok"
                _log_sink_transition("ok")
        else:
            _DROPPED_EVENTS += 1
            if _SINK_STATE != "degraded":
                _SINK_STATE = "degraded"
                _log_sink_transition("degraded")


def get_observability_status() -> dict:
    """Bloco `atlas_status.observability`; o chamador só o inclui quando habilitado."""
    with _STATE_LOCK:
        last_by_tool = {tool: dict(event) for tool, event in _LAST_EVENTS.items()}
        dropped = _DROPPED_EVENTS
        sink_state = _SINK_STATE
    return {
        "enabled": observability_enabled(),
        "sink_state": sink_state,
        "dropped_events": dropped,
        "last_by_tool": last_by_tool,
    }
