"""Origens explícitas para geração semântica opt-in."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from codesteer_atlas.config import (
    SEMANTIC_API_KEY_ENV,
    SEMANTIC_API_URL_ENV,
    SEMANTIC_LOCAL_URL_ENV,
    SEMANTIC_MODEL_ENV,
    SEMANTIC_TIMEOUT_S,
)


@dataclass(frozen=True)
class OriginChoice:
    """Descreve uma origem disponível e sua fronteira de dados."""

    name: str
    egress: str


@dataclass(frozen=True)
class OriginResult:
    """Resultado de uma tentativa de geração e a origem que respondeu."""

    text: str
    origin: str
    egress: str


def _await_if_needed(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value

    async def _wait() -> Any:
        return await value

    try:
        import anyio

        return anyio.from_thread.run(_wait)
    except Exception:
        return asyncio.run(_wait())


def _response_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    for attribute in ("text", "result"):
        candidate = getattr(value, attribute, None)
        if isinstance(candidate, str):
            return candidate
    if isinstance(value, Mapping):
        for key in ("purpose", "text", "result", "content", "summary", "response"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        choices = value.get("choices")
        if isinstance(choices, list) and choices:
            return _response_text(choices[0])
        message = value.get("message")
        if message is not None:
            return _response_text(message)
    message = getattr(value, "message", None)
    if message is not None:
        return _response_text(message)
    if isinstance(value, (list, tuple)) and value:
        parts = [_response_text(part) for part in value]
        if all(parts):
            return "".join(parts)
    if value is None:
        return ""
    raise ValueError("A resposta da origem semântica não contém texto utilizável.")


class OriginResolver:
    """Resolve e tenta origens sem escolher provedor implícito."""

    def __init__(
        self,
        ctx: Any = None,
        environ: Optional[Mapping[str, str]] = None,
        timeout_s: float = SEMANTIC_TIMEOUT_S,
    ) -> None:
        self.ctx = ctx
        self.environ = environ if environ is not None else os.environ
        self.timeout_s = timeout_s
        self.last_result: Optional[OriginResult] = None
        self.used_origins: list[str] = []
        self.used_egresses: list[str] = []

    def choices(self) -> list[OriginChoice]:
        choices: list[OriginChoice] = []
        if self.ctx is not None and callable(getattr(self.ctx, "sample", None)):
            choices.append(
                OriginChoice(
                    "sampling",
                    "Nenhum egresso adicional; o código já está no contexto do cliente MCP.",
                )
            )
        if self.environ.get(SEMANTIC_LOCAL_URL_ENV):
            choices.append(OriginChoice("local", "O conteúdo permanece no host via endpoint local explícito."))
        if self.environ.get(SEMANTIC_API_URL_ENV):
            choices.append(OriginChoice("api", "Envia conteúdo e metadados mínimos ao endpoint de API explicitamente configurado."))
        return choices

    def resolve(self) -> Optional[OriginChoice]:
        """Retorna a primeira origem que a sessão escolheria agora."""
        choices = self.choices()
        return choices[0] if choices else None

    def describe(self) -> tuple[Optional[str], Optional[str]]:
        choice = self.resolve()
        return (choice.name, choice.egress) if choice else (None, None)

    def _call_sampling(self, payload: Mapping[str, Any]) -> str:
        sample = self.ctx.sample
        prompt = payload.get("prompt") or payload.get("content") or ""
        messages = [{"role": "user", "content": str(prompt)}]
        try:
            value = sample(messages=messages)
        except TypeError:
            value = sample(str(prompt))
        return _response_text(_await_if_needed(value))

    def _record_use(self, result: OriginResult) -> None:
        if result.origin not in self.used_origins:
            self.used_origins.append(result.origin)
        if result.egress not in self.used_egresses:
            self.used_egresses.append(result.egress)

    def _call_http(self, url: str, payload: Mapping[str, Any], *, api: bool) -> str:
        request_payload = dict(payload)
        model = self.environ.get(SEMANTIC_MODEL_ENV) if api else None
        if model:
            prompt = payload.get("prompt") or payload.get("content") or ""
            request_payload = {
                "model": model,
                "messages": [{"role": "user", "content": str(prompt)}],
            }
        body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api:
            key = self.environ.get(SEMANTIC_API_KEY_ENV)
            if key:
                headers["Authorization"] = f"Bearer {key}"
        request = Request(url, data=body, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
        try:
            return _response_text(json.loads(raw))
        except json.JSONDecodeError:
            return raw

    def generate(self, payload: Mapping[str, Any]) -> Optional[OriginResult]:
        """Tenta cada elo configurado; uma falha segue para o próximo."""
        for choice in self.choices():
            try:
                if choice.name == "sampling":
                    text = self._call_sampling(payload)
                elif choice.name == "local":
                    text = self._call_http(self.environ[SEMANTIC_LOCAL_URL_ENV], payload, api=False)
                else:
                    text = self._call_http(self.environ[SEMANTIC_API_URL_ENV], payload, api=True)
                result = OriginResult(text=text, origin=choice.name, egress=choice.egress)
                self.last_result = result
                self._record_use(result)
                return result
            except (OSError, URLError, TimeoutError, ValueError, RuntimeError, TypeError) as error:
                print(
                    f"[atlas] Origem semântica {choice.name} indisponível ({type(error).__name__}).",
                    file=sys.stderr,
                )
            except Exception as error:
                print(
                    f"[atlas] Origem semântica {choice.name} falhou ({type(error).__name__}).",
                    file=sys.stderr,
                )
        return None
