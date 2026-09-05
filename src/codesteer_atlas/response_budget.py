"""
Finalizador de resposta compartilhado pelas quatro tools de recuperação
(D3 da arquitetura). Não decide QUAL conteúdo cortar primeiro — isso continua
em cada módulo (`graph.enforce_graph_response_budget`,
`context._enforce_context_budget`, `brief._enforce_budget`, e o corte de
resultados inteiros implementado em `server.py` para `atlas_search`) — aqui
apenas garante a pós-condição final: caracteres, bytes UTF-8 e (quando há
tokenizer local disponível) tokens exatos dentro do teto declarado, com
envelope mínimo quando nada mais couber. Não importa `server` (mantém D3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

from codesteer_atlas.config import (
    RESPONSE_BUDGET_BRIEF0_MAX_BYTES,
    RESPONSE_BUDGET_BRIEF0_MAX_TOKENS,
    RESPONSE_BUDGET_BRIEF1_MAX_BYTES,
    RESPONSE_BUDGET_BRIEF1_MAX_TOKENS,
    RESPONSE_BUDGET_CONTEXT_MAX_BYTES,
    RESPONSE_BUDGET_CONTEXT_MAX_TOKENS,
    RESPONSE_BUDGET_GRAPH_MAX_BYTES,
    RESPONSE_BUDGET_GRAPH_MAX_TOKENS,
    RESPONSE_BUDGET_MAX_CUT_ATTEMPTS,
    RESPONSE_BUDGET_SEARCH_MAX_BYTES,
    RESPONSE_BUDGET_SEARCH_MAX_CHARS,
    RESPONSE_BUDGET_SEARCH_MAX_TOKENS,
)
from codesteer_atlas.observability import (
    ResponseMeasurement,
    count_tokens,
    degrade_measurement_to_bytes,
    get_token_counter,
    measure_response,
)


@dataclass(frozen=True)
class ResponseBudget:
    tool: str
    max_chars: int
    max_bytes: int
    max_tokens: int


_SEARCH_BUDGET = ResponseBudget(
    "search",
    RESPONSE_BUDGET_SEARCH_MAX_CHARS,
    RESPONSE_BUDGET_SEARCH_MAX_BYTES,
    RESPONSE_BUDGET_SEARCH_MAX_TOKENS,
)
_CONTEXT_BUDGET = ResponseBudget(
    "context",
    RESPONSE_BUDGET_CONTEXT_MAX_BYTES,
    RESPONSE_BUDGET_CONTEXT_MAX_BYTES,
    RESPONSE_BUDGET_CONTEXT_MAX_TOKENS,
)
_GRAPH_BUDGET = ResponseBudget(
    "graph",
    RESPONSE_BUDGET_GRAPH_MAX_BYTES,
    RESPONSE_BUDGET_GRAPH_MAX_BYTES,
    RESPONSE_BUDGET_GRAPH_MAX_TOKENS,
)
_BRIEF0_BUDGET = ResponseBudget(
    "brief",
    RESPONSE_BUDGET_BRIEF0_MAX_BYTES,
    RESPONSE_BUDGET_BRIEF0_MAX_BYTES,
    RESPONSE_BUDGET_BRIEF0_MAX_TOKENS,
)
_BRIEF1_BUDGET = ResponseBudget(
    "brief",
    RESPONSE_BUDGET_BRIEF1_MAX_BYTES,
    RESPONSE_BUDGET_BRIEF1_MAX_BYTES,
    RESPONSE_BUDGET_BRIEF1_MAX_TOKENS,
)


def get_budget(tool: str, *, level: Optional[int] = None) -> ResponseBudget:
    if tool == "search":
        return _SEARCH_BUDGET
    if tool == "context":
        return _CONTEXT_BUDGET
    if tool == "graph":
        return _GRAPH_BUDGET
    if tool == "brief":
        return _BRIEF0_BUDGET if level == 0 else _BRIEF1_BUDGET
    raise ValueError(f"Tool desconhecida para orçamento de resposta: {tool!r}")


def serialize(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _tokens_fit(measurement: ResponseMeasurement, budget: ResponseBudget, reserve_tokens: int) -> bool:
    # Sem contagem exata (tokenizer indisponível), o teto de
    # tokens não se aplica: o limite conservador passa a ser o de bytes (D2).
    if measurement.tokens is None:
        return True
    return measurement.tokens <= budget.max_tokens - reserve_tokens


def _fits(
    measurement: ResponseMeasurement,
    budget: ResponseBudget,
    *,
    reserve_chars: int = 0,
    reserve_tokens: int = 0,
) -> bool:
    return (
        measurement.chars <= budget.max_chars - reserve_chars
        and measurement.bytes <= budget.max_bytes - reserve_chars
        and _tokens_fit(measurement, budget, reserve_tokens)
    )


def build_budget_block(measurement: ResponseMeasurement, budget: ResponseBudget) -> dict:
    exact = measurement.tokens is not None
    return {
        "mode": "tokenizer_exact" if exact else "byte_bpe_upper_bound",
        "max_chars": budget.max_chars,
        "max_bytes": budget.max_bytes,
        "max_tokens": budget.max_tokens if exact else None,
        "tokenizer_sha256": measurement.tokenizer_sha256,
    }


def _reserve_for_budget_block(budget: ResponseBudget) -> tuple[int, int]:
    """
    Tamanho (chars, tokens) a reservar durante o corte para o próprio bloco
    `budget` — incluindo `used_chars` — caber sem estourar o teto real.
    Calculado a partir de um protótipo do maior formato possível
    (`mode="byte_bpe_upper_bound"`, sha256 presente), contado com o MESMO
    contador (tokenizer, se configurado) usado no resto do orçamento — uma
    estimativa por caracteres divergiria do tokenizer real e reservaria
    tokens de menos ou de mais. Margem pequena extra para `used_chars`
    crescer um dígito na estabilização final (§4.2).
    """
    prototype_block = {
        "mode": "byte_bpe_upper_bound",
        "max_chars": budget.max_chars,
        "max_bytes": budget.max_bytes,
        "max_tokens": budget.max_tokens,
        "tokenizer_sha256": count_tokens("").tokenizer_sha256 or ("0" * 64),
        "used_chars": budget.max_chars,
    }
    prototype_text = json.dumps(prototype_block, separators=(",", ":"), ensure_ascii=False)
    reserve_chars = len(prototype_text) + 16

    prototype_measurement = measure_response(prototype_text)
    if prototype_measurement.tokens is not None:
        reserve_tokens = prototype_measurement.tokens + 4
    else:
        reserve_tokens = (reserve_chars // 4) + 4
    return reserve_chars, reserve_tokens


def finalize_response(
    payload: dict,
    budget: ResponseBudget,
    *,
    cut_once: Callable[[dict], bool],
    minimal_envelope: Callable[[dict], dict],
) -> tuple[str, ResponseMeasurement]:
    """
    Aplica `cut_once` (política específica de cada tool: remove um item por
    vez, nunca corta string por substring) até o payload caber em TODAS as
    dimensões do orçamento — chars, bytes e, com tokenizer exato, tokens —
    reservando espaço para o próprio bloco `budget` embutido ao final. Se
    `cut_once` esgotar (retorna `False`) sem couber, substitui por
    `minimal_envelope`; se nem o envelope mínimo couber no teto de tokens
    exatos, degrada a medição para a modalidade conservadora por bytes e
    memoriza a degradação no contador até reinício (D2/T2).

    Retorna `(json_final, measurement)`. Nunca mede a string ANTES de embutir
    o bloco `budget` nela — a medição final sempre inclui esse bloco.
    """
    reserve_chars, reserve_tokens = _reserve_for_budget_block(budget)

    attempts = 0
    text = serialize(payload)
    measurement = measure_response(text)
    while not _fits(measurement, budget, reserve_chars=reserve_chars, reserve_tokens=reserve_tokens):
        if attempts >= RESPONSE_BUDGET_MAX_CUT_ATTEMPTS or not cut_once(payload):
            payload = minimal_envelope(payload)
            text = serialize(payload)
            measurement = measure_response(text)
            if not _fits(measurement, budget, reserve_chars=reserve_chars, reserve_tokens=reserve_tokens):
                # Guarda final (D2): nem o envelope mínimo cabe no teto de
                # tokens exatos do tokenizer configurado — degrada para bytes
                # e memoriza até reinício, sem prometer um teto inexequível.
                get_token_counter().force_unavailable()
                measurement = degrade_measurement_to_bytes(measurement)
            break
        attempts += 1
        text = serialize(payload)
        measurement = measure_response(text)

    payload["budget"] = build_budget_block(measurement, budget)
    text = serialize(payload)
    payload["budget"]["used_chars"] = len(text)
    text = serialize(payload)
    # Estabilização numérica limitada (§4.2): inserir `used_chars` pode mudar o
    # comprimento em poucos dígitos; a margem reservada acima garante que isso
    # não estoura o teto real. Poucas iterações bastam para convergir.
    for _ in range(3):
        length = len(text)
        if payload["budget"]["used_chars"] == length:
            break
        payload["budget"]["used_chars"] = length
        text = serialize(payload)

    final_measurement = measure_response(text)
    return text, final_measurement
