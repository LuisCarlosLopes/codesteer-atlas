"""
Otimização opt-in do contexto entregue: perfil compacto, deduplicação e expansão.

Módulo puro — consumido por `server` e pelo harness de eval, sem I/O de rede.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

from codesteer_atlas.config import (
    CHUNK_TRUNCATION_MARKER,
    CONTEXT_EXPAND_MAX_REFS,
    CONTEXT_OPTIMIZATION_ENV_FLAG,
    EXPAND_OUTLINE_ENV_FLAG,
    RELEVANCE_CUT_NORMALIZED_BELOW,
    RELEVANCE_DROP_CONFIDENCE_MIN,
    RELEVANCE_DROP_NORMALIZED_MAX,
)
from codesteer_atlas.models import CandidateEvaluation, RelevanceUsage, SearchResult
from codesteer_atlas.relevance import is_exact_match


def context_optimization_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    values = environ if environ is not None else os.environ
    raw = values.get(CONTEXT_OPTIMIZATION_ENV_FLAG)
    if raw is None or raw.strip() == "":
        return False
    return raw.strip().lower() in {"1", "true"}


def context_optimization_status(environ: Optional[Mapping[str, str]] = None) -> dict:
    return {"enabled": context_optimization_enabled(environ)}


def expand_outline_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Ligado por padrão: só `0`/`false` desliga o resumo de classes grandes."""
    values = environ if environ is not None else os.environ
    raw = (values.get(EXPAND_OUTLINE_ENV_FLAG) or "").strip().lower()
    return raw not in {"0", "false"}


def build_class_outline(
    chunk: Mapping[str, Any], members: Sequence[Mapping[str, Any]]
) -> Optional[tuple[int, list[dict]]]:
    """
    (última linha do cabeçalho, métodos diretos) de um chunk de classe, ou None.
    Membro direto é `Classe.nome` que começa depois da linha da classe; o
    cabeçalho vai da classe até a linha anterior ao primeiro membro.
    """
    # @MindRisk: JS minificado põe classe e métodos na mesma linha — sem resumo possível.
    name = chunk.get("scope_name") or ""
    if chunk.get("scope_type") != "class" or not name:
        return None
    start, end = chunk["start_line"], chunk["end_line"]
    direct = sorted(
        (
            m for m in members
            if (m.get("scope_name") or "").startswith(name + ".")
            and "." not in m["scope_name"][len(name) + 1:]
            and start < m["start_line"] <= end
        ),
        key=lambda m: m["start_line"],
    )
    if not direct:
        return None
    outline = [
        {"ref": m["id"], "symbol": m["scope_name"], "type": m["scope_type"],
         "lines": [m["start_line"], m["end_line"]]}
        for m in direct
    ]
    return direct[0]["start_line"] - 1, outline


def resolve_response_profile(
    response_profile: Optional[str],
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve `default|compact|full` → `compact|full`."""
    profile = (response_profile or "default").strip().lower()
    if profile not in {"default", "compact", "full"}:
        raise ValueError(
            "O parâmetro 'response_profile' deve ser 'default', 'compact' ou 'full'."
        )
    if profile == "default":
        return "compact" if context_optimization_enabled(environ) else "full"
    return profile


def encode_expand_ref(*, repo: str, chunk_id: str, file_hash: str) -> str:
    """Codificador legado; novas respostas usam diretamente o chunk_id."""
    payload = {"r": repo, "c": chunk_id, "h": file_hash}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_expand_ref(ref: str) -> Optional[dict]:
    continuation = re.fullmatch(r"([0-9a-f]{16}):([0-9a-f]{64}):([0-9]{1,12})", ref)
    if continuation:
        return {"repo": None, "chunk_id": continuation[1],
                "file_hash": continuation[2], "offset": int(continuation[3])}
    if re.fullmatch(r"[0-9a-f]{16}", ref):
        return {"repo": None, "chunk_id": ref, "file_hash": None}
    try:
        padded = ref + "=" * (-len(ref) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    repo = data.get("r")
    chunk_id = data.get("c")
    file_hash = data.get("h")
    if not all(isinstance(v, str) and v for v in (repo, chunk_id, file_hash)):
        return None
    return {"repo": repo, "chunk_id": chunk_id, "file_hash": file_hash}


def _content_truncated(content: Optional[str]) -> bool:
    if not content:
        return False
    return CHUNK_TRUNCATION_MARKER in content


def _identity(result: SearchResult) -> str:
    if result.chunk_id:
        return result.chunk_id
    return f"{result.repo}:{result.file_path}:{result.start_line}:{result.end_line}:{result.scope_name}"


@dataclass
class DedupeOutcome:
    results: List[SearchResult]
    removed_by_redundancy: int = 0


def deduplicate_results(results: Sequence[SearchResult]) -> DedupeOutcome:
    """
    Deduplicação conservadora antes da seleção final.
    Não une arquivos distintos; overlap de linhas, sozinho, não prova redundância.
    """
    survivors: list[SearchResult] = []
    removed = 0

    for candidate in results:
        if candidate.type == "commit":
            survivors.append(candidate)
            continue

        absorbed = False
        for index, existing in enumerate(survivors):
            if existing.type == "commit":
                continue
            if (existing.repo, existing.file_path) != (candidate.repo, candidate.file_path):
                continue

            same_chunk = (
                existing.chunk_id
                and candidate.chunk_id
                and existing.chunk_id == candidate.chunk_id
            )
            same_content = (
                existing.content is not None
                and candidate.content is not None
                and existing.content == candidate.content
            )
            if same_chunk or same_content:
                covered = list(dict.fromkeys(existing.covered_ids + candidate.covered_ids))
                ident = _identity(candidate)
                if ident not in covered and ident != _identity(existing):
                    covered.append(ident)
                survivors[index] = existing.model_copy(update={"covered_ids": covered})
                removed += 1
                absorbed = True
                break

            if _content_covers(existing, candidate):
                covered = list(dict.fromkeys(existing.covered_ids + candidate.covered_ids))
                ident = _identity(candidate)
                if ident not in covered:
                    covered.append(ident)
                survivors[index] = existing.model_copy(update={"covered_ids": covered})
                removed += 1
                absorbed = True
                break

            if _content_covers(candidate, existing):
                covered = list(candidate.covered_ids) + list(existing.covered_ids)
                ident = _identity(existing)
                if ident not in covered:
                    covered.append(ident)
                # Preferir o container; herdar covered do absorvido.
                survivors[index] = candidate.model_copy(update={"covered_ids": covered})
                removed += 1
                absorbed = True
                break

        if not absorbed:
            survivors.append(candidate)

    return DedupeOutcome(results=survivors, removed_by_redundancy=removed)


def _content_covers(container: SearchResult, contained: SearchResult) -> bool:
    """Cobertura integral por conteúdo, sem truncamento em nenhum dos lados."""
    if (container.repo, container.file_path) != (contained.repo, contained.file_path):
        return False
    left = container.content
    right = contained.content
    if not left or not right:
        return False
    if _content_truncated(left) or _content_truncated(right):
        return False
    if left == right:
        return True
    return (
        container.start_line <= contained.start_line
        and container.end_line >= contained.end_line
        and right in left
    )


@dataclass
class SelectionOutcome:
    results: List[SearchResult]
    removed_by_relevance: int = 0
    unconfirmed: bool = False
    warnings: List[str] = field(default_factory=list)


def select_conservative(
    results: Sequence[SearchResult],
    query: str,
    evaluations: Sequence[CandidateEvaluation],
    *,
    top_k: int,
    local_fallback: Optional[Sequence[SearchResult]] = None,
) -> SelectionOutcome:
    """
    Seleção conservadora do perfil compacto com Jev (ATLAS_RELEVANCE_CUT=0).
    Descarta só score ≤ 1/8 da escala (0,25 na v1) e confiança ≥ 0,90.
    Incertos/não avaliados ficam.
    """
    by_key: dict[str, CandidateEvaluation] = {}
    for ev in evaluations:
        key = ev.chunk_id or f"{ev.file_path}:{ev.scope_name}"
        by_key[key] = ev

    kept: list[SearchResult] = []
    removed = 0
    for result in results:
        if is_exact_match(result, query):
            kept.append(result)
            continue
        key = result.chunk_id or f"{result.file_path}:{result.scope_name}"
        evaluation = by_key.get(key)
        if (
            evaluation is not None
            and evaluation.state == "evaluated"
            and evaluation.score is not None
            and evaluation.confidence is not None
            and evaluation.score <= RELEVANCE_DROP_NORMALIZED_MAX * evaluation.score_max
            and evaluation.confidence >= RELEVANCE_DROP_CONFIDENCE_MIN
        ):
            removed += 1
            continue
        kept.append(result)

    # top_k como teto: não completar vagas com irrelevantes já excluídos.
    kept = kept[:top_k]
    unconfirmed = False
    warnings: list[str] = []
    if not kept:
        fallback_pool = list(local_fallback) if local_fallback is not None else list(results)
        if fallback_pool:
            kept = [fallback_pool[0]]
            unconfirmed = True
            warnings.append("relevance_unconfirmed_fallback")

    return SelectionOutcome(
        results=kept,
        removed_by_relevance=removed,
        unconfirmed=unconfirmed,
        warnings=warnings,
    )


def select_within_top_k(
    results: Sequence[SearchResult],
    query: str,
    evaluations: Sequence[CandidateEvaluation],
    *,
    top_k: int,
    normalized_below: float = RELEVANCE_CUT_NORMALIZED_BELOW,
    local_fallback: Optional[Sequence[SearchResult]] = None,
) -> SelectionOutcome:
    """
    Corte do top_k (ATLAS_RELEVANCE_CUT): recebe a ordem Jev já deduplicada, fica com
    os `top_k` primeiros e remove, sem repor a vaga, quem tem score abaixo de
    `normalized_below` × o nível máximo da rubrica (1,0 na v1, 1,5 na v2).
    Exatos e candidatos sem avaliação ficam; incertos também saem.
    """
    # @MindWhy: exigir confiança ≥ 0,70 anula o corte — ~70% dos candidatos ficam abaixo
    # @MindRisk: o score oscila com o lote (p95 0,56); `omitted.relevance` declara o corte
    by_key: dict[str, CandidateEvaluation] = {}
    for ev in evaluations:
        by_key[ev.chunk_id or f"{ev.file_path}:{ev.scope_name}"] = ev

    window = list(results[:top_k])
    kept: list[SearchResult] = []
    removed = 0
    for result in window:
        evaluation = by_key.get(result.chunk_id or f"{result.file_path}:{result.scope_name}")
        if (
            not is_exact_match(result, query)
            and evaluation is not None
            and evaluation.state in {"evaluated", "uncertain"}
            and evaluation.score is not None
            and evaluation.score < normalized_below * evaluation.score_max
        ):
            removed += 1
            continue
        kept.append(result)

    unconfirmed = False
    warnings: list[str] = []
    if not kept and window:
        fallback = (list(local_fallback) if local_fallback else window)[0]
        kept = [fallback]
        unconfirmed = True
        warnings.append("relevance_unconfirmed_fallback")
        if any(_identity(fallback) == _identity(result) for result in window):
            removed -= 1

    return SelectionOutcome(
        results=kept,
        removed_by_relevance=removed,
        unconfirmed=unconfirmed,
        warnings=warnings,
    )


def project_compact_search_results(
    results: Sequence[SearchResult],
    *,
    include_content: bool,
    file_hashes: Mapping[str, str],
) -> tuple[list[dict], Optional[str]]:
    """
    Projeta resultados no perfil compacto.
    Devolve (itens, repo_envelope) — repo sobe ao envelope se uniforme.
    """
    repos = {r.repo for r in results}
    envelope_repo = next(iter(repos)) if len(repos) == 1 else None
    items: list[dict] = []
    for result in results:
        if result.type == "commit" and result.commit is not None:
            item: dict[str, Any] = {
                "type": "commit",
                "commit": {
                    "id": result.commit.id,
                    "subject": result.commit.subject,
                },
            }
            if include_content:
                item["content"] = result.content
            if envelope_repo is None:
                item["repo"] = result.repo
            items.append(item)
            continue

        ref = result.chunk_id
        item = {
            "file_path": result.file_path,
            "lines": [result.start_line, result.end_line],
            "symbol": result.scope_name,
            "type": result.scope_type,
        }
        if ref:
            item["ref"] = ref
        if envelope_repo is None:
            item["repo"] = result.repo
        if include_content:
            item["content"] = result.content
        if result.covered_ids:
            item["covered_refs"] = result.covered_ids
        items.append(item)
    return items, envelope_repo


def compact_search_envelope(
    *,
    results: list[dict],
    repo: Optional[str],
    warnings: list[str],
    omitted: Optional[dict] = None,
    total_chunks_searched: Optional[int] = None,
) -> dict:
    payload: dict[str, Any] = {
        "results": results,
    }
    if repo is not None:
        payload["repo"] = repo
    if total_chunks_searched is not None:
        payload["total_chunks_searched"] = total_chunks_searched
    if warnings:
        payload["warnings"] = sorted(set(warnings))
    if omitted:
        # Contagens por motivo, sem listar descartados.
        payload["omitted"] = {
            key: value for key, value in omitted.items() if value
        }
    return payload


def compact_context_payload(payload: dict) -> dict:
    """
    Remove representações duplicadas de target/symbol e layer/brief_layer,
    preservando a cópia mais rica (purpose, summary).
    """
    sections = dict(payload.get("sections") or {})
    target = payload.get("target")
    symbol = sections.get("symbol")
    if isinstance(target, dict) and isinstance(symbol, dict):
        richer = _richer_node(target, symbol)
        payload["target"] = richer
        sections.pop("symbol", None)
    elif symbol is not None and target is None:
        payload["target"] = symbol
        sections.pop("symbol", None)

    layer = sections.get("layer")
    brief_layer = sections.get("brief_layer")
    if isinstance(layer, dict) and isinstance(brief_layer, dict):
        sections["layer"] = _richer_layer(layer, brief_layer)
        sections.pop("brief_layer", None)
    elif brief_layer is not None and layer is None:
        sections["layer"] = brief_layer
        sections.pop("brief_layer", None)

    payload["sections"] = sections
    return payload


def _richer_node(a: dict, b: dict) -> dict:
    merged = dict(a)
    for key, value in b.items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    if a.get("purpose"):
        merged["purpose"] = a["purpose"]
    elif b.get("purpose"):
        merged["purpose"] = b["purpose"]
    return merged


def _richer_layer(a: dict, b: dict) -> dict:
    merged = dict(a)
    for key, value in b.items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    if a.get("summary"):
        merged["summary"] = a["summary"]
    elif b.get("summary"):
        merged["summary"] = b["summary"]
    return merged


def sha256_file(path: Path) -> Optional[str]:
    try:
        hasher = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                hasher.update(block)
        return hasher.hexdigest()
    except OSError:
        return None


def validate_expand_refs(refs: Sequence[str]) -> list[str]:
    if len(refs) > CONTEXT_EXPAND_MAX_REFS:
        raise ValueError(
            f"O parâmetro 'refs' aceita no máximo {CONTEXT_EXPAND_MAX_REFS} referências."
        )
    if not refs:
        raise ValueError("O parâmetro 'refs' é obrigatório e não pode ser vazio.")
    return list(refs)


def estimate_bytes(results: Sequence[SearchResult]) -> int:
    total = 0
    for result in results:
        total += len((result.content or "").encode("utf-8"))
        total += len(result.file_path.encode("utf-8"))
        total += len((result.scope_name or "").encode("utf-8"))
    return total


def record_selection_usage(
    usage: RelevanceUsage,
    *,
    recovered: Sequence[SearchResult],
    selected: Sequence[SearchResult],
    removed_by_redundancy: int = 0,
    removed_by_relevance: int = 0,
    removed_by_budget: int = 0,
) -> None:
    """Bytes de conteúdo interno; não equivalem ao tamanho da resposta serializada."""
    usage.bytes_recovered = estimate_bytes(recovered)
    usage.bytes_selected = estimate_bytes(selected)
    usage.removed_by_redundancy = removed_by_redundancy
    usage.removed_by_relevance = removed_by_relevance
    usage.removed_by_budget = removed_by_budget
