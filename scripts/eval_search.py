"""
Harness de avaliação do ranking de `atlas_search`.

Mede MRR e recall@5 do `StorageBackend.search_hybrid` contra o golden set em
`tests/eval/golden_queries.yaml`, agregando **por classe de query**. A agregação
por classe é o ponto: uma média global esconderia um rerank que melhora
casamento literal enquanto degrada linguagem natural, que é exatamente o risco
de regressão desta linha de trabalho.

Fora do CI de propósito — depende de um índice construído e do modelo ONNX.

Uso:
    uv run python scripts/eval_search.py
    uv run python scripts/eval_search.py --baseline tests/eval/baseline.json
    uv run python scripts/eval_search.py --out tests/eval/baseline.json
    uv run python scripts/eval_search.py --structural
    uv run python scripts/eval_search.py --delivery
    uv run python scripts/eval_search.py --benchmark

`--delivery` (plano observabilidade-tokens-consultas, T6) acrescenta um bloco
`delivery`: mede a QUALIDADE DA RESPOSTA REALMENTE ENTREGUE — depois da
montagem (`server.assemble_search_payload`) e do orçamento de resposta
(`response_budget.finalize_response`) — nos dois modos de conteúdo
(`metadata`: include_content=false: `content`: include_content=true), sobre os
MESMOS candidatos já recuperados por `search_hybrid` (nunca uma segunda busca).
O ranking pré-budget (`overall`/`by_class` histórico) continua medido exatamente
como antes — `--delivery` só ACRESCENTA o bloco novo.
"""

import argparse
import io
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from codesteer_atlas.config import (
    RERANK_ENV_FLAG,
    RERANK_MODEL_ENV_FLAG,
    RESPONSE_BUDGET_SEARCH_MAX_BYTES,
    RESPONSE_BUDGET_SEARCH_MAX_CHARS,
    RESPONSE_BUDGET_SEARCH_MAX_TOKENS,
)
from codesteer_atlas.embeddings import EmbeddingEngine
from codesteer_atlas.storage import StorageBackend

# Profundidade de busca da avaliação. Maior que o `recall@5` medido para que o
# MRR consiga distinguir "caiu para a posição 8" de "sumiu do resultado".
EVAL_TOP_K = 10
RECALL_AT = 5

DELIVERY_MODES = ("metadata", "content")
DELIVERY_VARIANTS = (("full", "projection"), ("compact", "projection"),
                     ("compact", "dedup"), ("compact", "selection"))

# Estratos de warning: só degradação de índice exclui a linha das métricas
# comparáveis de ranking. Jev/orçamento/cobrança aparecem como estrato.
INDEX_DEGRADATION_WARNINGS = frozenset(
    {
        "fts_unavailable",
        "vector_search_unavailable",
        "cross_encoder_unavailable",
        "structural_arm_unavailable",
        "semantic_layer_unavailable",
        "semantic_arm_unavailable",
        "git_history_unavailable",
    }
)
JEV_FALLBACK_WARNINGS = frozenset(
    {
        "relevance_unavailable",
        "relevance_invalid_response",
        "relevance_low_confidence",
        "relevance_budget_exceeded",
        "relevance_unconfirmed_fallback",
    }
)
BUDGET_OMISSION_WARNINGS = frozenset({"truncated_for_budget"})
COST_UNKNOWN_WARNINGS = frozenset({"relevance_cost_unknown"})


def classify_warning_strata(warnings: List[str]) -> Dict[str, bool]:
    codes = set(warnings or [])
    return {
        "index_degraded": bool(codes & INDEX_DEGRADATION_WARNINGS),
        "jev_fallback": bool(codes & JEV_FALLBACK_WARNINGS),
        "budget_omission": bool(codes & BUDGET_OMISSION_WARNINGS),
        "cost_unknown": bool(codes & COST_UNKNOWN_WARNINGS),
    }


def _import_server_and_restore_console():
    """
    Importa `codesteer_atlas.server` para reaproveitar `assemble_search_payload`
    (nunca duplicar a regra de serialização do servidor no harness) e restaura
    IMEDIATAMENTE o console deste processo.

    Importar `server` redireciona `sys.stdout` para stderr e remapeia o fd 1 do
    processo como efeito colateral (proteção do protocolo stdio do MCP — este
    script não é um servidor MCP e não deve herdar essa proteção). `server`
    guarda o stdout original em `original_stdout` exatamente para esse tipo de
    reaproveitamento (§7.1 do IPD: "harness deve invocar helper por runner
    isolado quando necessário, restaurando apenas seu próprio console; não
    mudar proteção do servidor").
    """
    from codesteer_atlas import server as atlas_server

    sys.stdout = atlas_server.original_stdout
    return atlas_server


def _load_golden(path: Path) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    queries = data.get("queries", [])
    if not queries:
        raise SystemExit(f"Golden set vazio ou malformado: {path}")
    return queries


def _validate_targets(storage: StorageBackend, queries: List[Dict[str, Any]]) -> None:
    """
    Falha alto quando um alvo declarado não existe mais no índice.

    Sem isso, um símbolo renomeado vira queda silenciosa de métrica e o harness
    passa a medir apodrecimento do golden set em vez de qualidade de ranking.
    """
    indexed = {(row["file_path"], row["scope_name"]) for row in storage.get_symbols()}

    missing = []
    for entry in queries:
        for target in entry["targets"]:
            key = (target["file_path"], target["scope_name"])
            if key not in indexed:
                missing.append(f"  {entry['query']!r} -> {key[0]}::{key[1]}")

    if missing:
        raise SystemExit(
            "Alvos do golden set ausentes do índice (símbolo renomeado/removido, "
            "ou índice desatualizado):\n"
            + "\n".join(missing)
            + "\n\nRode 'uv run atlas-index --workspace . --full' e, se o símbolo "
            "mudou mesmo, corrija tests/eval/golden_queries.yaml."
        )


def _first_hit_rank(results: List[Any], targets: List[Dict[str, str]]) -> Optional[int]:
    """Posição 1-indexed do primeiro resultado que casa com qualquer alvo aceitável."""
    wanted = {(t["file_path"], t["scope_name"]) for t in targets}
    for position, result in enumerate(results, start=1):
        if (result.file_path, result.scope_name) in wanted:
            return position
    return None


def _active_reranker() -> Dict[str, Any]:
    """Registra qual reordenador está ativo — sem isso o A/B de 2.1 fica opaco."""
    if os.environ.get(RERANK_ENV_FLAG, "1") == "0":
        return {"reranker": "none", "rerank_model": None, "rubric": None}
    from codesteer_atlas.relevance import resolve_config, resolve_rubric

    cfg = resolve_config()
    if cfg.enabled:
        return {"reranker": "jev", "rerank_model": cfg.model, "rubric": resolve_rubric()[0].version}
    model = os.environ.get(RERANK_MODEL_ENV_FLAG)
    if model:
        return {"reranker": "cross_encoder", "rerank_model": model, "rubric": None}
    return {"reranker": "lexical", "rerank_model": None, "rubric": None}


def new_relevance_tracker() -> Dict[str, Any]:
    from codesteer_atlas.relevance import resolve_rubric

    return {
        "requests": 0,
        "valid_evaluations": 0,
        "fallbacks": 0,
        "cost_usd_known": 0.0,
        "cost_known_n": 0,
        "cost_unknown_count": 0,
        "stage_ms": [],
        "complete": True,
        "stop_reason": None,
        "model": None,
        "rubric": resolve_rubric()[0].version,
    }


def observe_relevance_usage(tracker: Dict[str, Any], usage: Any, max_cost_usd: float) -> bool:
    """Atualiza o tracker. True = parar antes da próxima chamada remota."""
    if usage is None:
        return False
    tracker["model"] = usage.resolved_model or usage.requested_model or tracker["model"]
    tracker["requests"] += usage.request_count
    if usage.status == "success":
        tracker["valid_evaluations"] += 1
    elif usage.status == "fallback":
        tracker["fallbacks"] += 1
    if usage.duration_ms is not None:
        tracker["stage_ms"].append(usage.duration_ms)
    if usage.cost_status == "reported" and usage.cost_usd is not None:
        tracker["cost_usd_known"] += usage.cost_usd
        tracker["cost_known_n"] += 1
        if tracker["cost_usd_known"] >= max_cost_usd:
            tracker["complete"] = False
            tracker["stop_reason"] = "max_cost"
            return True
    elif usage.request_count > 0 and usage.cost_status in {"unknown", "partially_known"}:
        if usage.cost_usd is not None:
            tracker["cost_usd_known"] += usage.cost_usd
            tracker["cost_known_n"] += 1
        tracker["cost_unknown_count"] += 1
        tracker["complete"] = False
        tracker["stop_reason"] = "unknown_cost"
        return True
    return False


def finalize_relevance_report(tracker: Dict[str, Any]) -> Dict[str, Any]:
    known = (
        round(tracker["cost_usd_known"], 6) if tracker["cost_known_n"] else None
    )
    experimental_success = bool(
        tracker["complete"]
        and tracker["valid_evaluations"] > 0
        and tracker["fallbacks"] < max(tracker["requests"], 1)
    )
    if tracker["requests"] > 0 and tracker["valid_evaluations"] == 0:
        experimental_success = False
    return {
        "model": tracker["model"],
        "rubric": tracker["rubric"],
        "requests": tracker["requests"],
        "valid_evaluations": tracker["valid_evaluations"],
        "fallbacks": tracker["fallbacks"],
        "cost_usd_known": known,
        "cost_unknown_count": tracker["cost_unknown_count"],
        "complete": tracker["complete"],
        "stop_reason": tracker["stop_reason"],
        "experimental_success": experimental_success,
        "stage_ms_p50": _percentile(tracker["stage_ms"], 50),
        "stage_ms_p95": _percentile(tracker["stage_ms"], 95),
    }


def _percentile(values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank (sem interpolar): não inventa precisão que a amostra não tem."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[k - 1]


def _delivery_rank(final_payload: dict, targets: List[Dict[str, str]], candidates=()) -> Optional[int]:
    """Mesma semântica de `_first_hit_rank`, mas sobre os itens JÁ serializados
    (e possivelmente cortados pelo orçamento), não sobre `SearchResult` cru."""
    wanted = {(t["file_path"], t["scope_name"]) for t in targets}
    by_id = {r.chunk_id: r for r in candidates if r.chunk_id}
    for position, item in enumerate(final_payload.get("results") or [], start=1):
        if (item.get("file_path"), item.get("symbol")) in wanted:
            return position
        for ref in item.get("covered_refs", []):
            covered = by_id.get(ref)
            if covered is None or (covered.file_path, covered.scope_name) not in wanted:
                continue
            if "content" in item and (not covered.content or covered.content not in item["content"]):
                continue
            return position
    return None


def _delivery_for_query(
    atlas_server: Any,
    response_budget_mod: Any,
    storage: StorageBackend,
    manifest: Any,
    outcome: Any,
    targets: List[Dict[str, str]],
    *,
    mode: str,
    profile: str = "full",
    query: str = "",
    optimization_stage: str = "selection",
) -> Dict[str, Any]:
    """
    Monta e finaliza a resposta de `atlas_search` para os MESMOS candidatos já
    recuperados (`outcome.results`) — nunca uma segunda busca — no modo de
    conteúdo pedido, e mede o que sobrevive ao orçamento (V11/V12 do plano
    observabilidade-tokens-consultas).
    """
    include_content = mode == "content"
    budget = None
    if profile == "full":
        budget = response_budget_mod.ResponseBudget(
            "search", RESPONSE_BUDGET_SEARCH_MAX_CHARS,
            RESPONSE_BUDGET_SEARCH_MAX_BYTES, RESPONSE_BUDGET_SEARCH_MAX_TOKENS,
        )
    started = time.perf_counter()
    text, measurement = atlas_server.prepare_search_delivery(
        storage, manifest, outcome, query=query, top_k=EVAL_TOP_K,
        include_content=include_content, profile=profile, budget=budget,
        optimization_stage=optimization_stage,
    )
    delivery_ms = (time.perf_counter() - started) * 1000
    final = json.loads(text)
    rank = _delivery_rank(final, targets, getattr(outcome, "candidate_pool", None) or outcome.results)
    strata = classify_warning_strata(final.get("warnings", []))
    # Só degradação de índice tira a linha do estrato comparável de ranking.
    degraded = strata["index_degraded"]
    return {
        "mode": mode,
        "profile": profile,
        "optimization_stage": optimization_stage,
        "delivery_ms": delivery_ms,
        "rank": rank,
        "rr": (1.0 / rank) if rank else 0.0,
        "hit_at_5": bool(rank and rank <= RECALL_AT),
        "degraded": degraded,
        "strata": strata,
        "response_chars": measurement.chars,
        "response_bytes": measurement.bytes,
        "response_tokens": measurement.tokens,
        "estimated_tokens": measurement.estimated_tokens,
        "count_method": measurement.count_method,
        "tokenizer_sha256": measurement.tokenizer_sha256,
        "results_omitted": (final.get("truncated") or {}).get("results", 0),
        "truncated": bool(final.get("truncated")),
    }


def _agg_delivery(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Agrega delivery por classe/modo. Métricas globais incluem todos os casos;
    `comparable` exclui só degradação de índice. Estratos Jev/orçamento/cobrança
    são reportados ao lado, sem exclusão silenciosa.
    """
    n = len(rows)
    comparable = [r for r in rows if not r["degraded"]]
    chars = [r["response_chars"] for r in rows if r["response_chars"] is not None]
    tokens = [r["response_tokens"] for r in rows if r["response_tokens"] is not None]
    strata_counts = {
        "index_degraded": sum(1 for r in rows if (r.get("strata") or {}).get("index_degraded")),
        "jev_fallback": sum(1 for r in rows if (r.get("strata") or {}).get("jev_fallback")),
        "budget_omission": sum(1 for r in rows if (r.get("strata") or {}).get("budget_omission")),
        "cost_unknown": sum(1 for r in rows if (r.get("strata") or {}).get("cost_unknown")),
    }
    return {
        "n": n,
        "comparable": len(comparable),
        "degraded": n - len(comparable),
        "strata": strata_counts,
        "mrr": round(sum(r["rr"] for r in comparable) / len(comparable), 4) if comparable else None,
        "mrr_all": round(sum(r["rr"] for r in rows) / n, 4) if n else None,
        "recall_at_5": (
            round(sum(1 for r in comparable if r["hit_at_5"]) / len(comparable), 4)
            if comparable
            else None
        ),
        "recall_at_5_all": round(sum(1 for r in rows if r["hit_at_5"]) / n, 4) if n else None,
        "truncated_ratio": round(sum(1 for r in rows if r["truncated"]) / n, 4) if n else 0.0,
        "results_omitted_total": sum(r["results_omitted"] for r in rows),
        "response_tokens_total": sum(tokens) if len(tokens) == n else None,
        "response_bytes_total": sum(r.get("response_bytes", 0) for r in rows),
        "delivery_ms_p50": _percentile([r.get("delivery_ms", 0) for r in rows], 50),
        "delivery_ms_p95": _percentile([r.get("delivery_ms", 0) for r in rows], 95),
        "response_chars_p50": _percentile(chars, 50),
        "response_chars_p95": _percentile(chars, 95),
        "response_tokens_p50": _percentile(tokens, 50),
        "response_tokens_p95": _percentile(tokens, 95),
        "count_method": rows[0]["count_method"] if rows else None,
        "tokenizer_sha256": rows[0]["tokenizer_sha256"] if rows else None,
    }


def _complete_expansion_items(items):
    """Só recompõe cadeias contíguas iniciadas no offset zero, sem pular páginas."""
    by_ref = {item.get("ref"): item for item in items if item.get("status") == "ok"}
    complete = [item for item in items if "content_range" not in item]
    for first in items:
        if first.get("status") != "ok" or first.get("content_range", [-1])[0] != 0:
            continue
        item = first
        parts = []
        offset = 0
        seen = set()
        while item and item.get("ref") not in seen:
            seen.add(item.get("ref"))
            bounds = item.get("content_range", [])
            content = item.get("content")
            if (len(bounds) != 2 or bounds[0] != offset or not isinstance(content, str)
                    or bounds[1] - bounds[0] != len(content)
                    or (item.get("file_path"), item.get("symbol"), item.get("lines"))
                    != (first.get("file_path"), first.get("symbol"), first.get("lines"))):
                break
            parts.append(content)
            offset = bounds[1]
            if item.get("content_complete"):
                complete.append({**first, "content": "".join(parts), "content_complete": True})
                break
            item = by_ref.get(item.get("next_ref"))
    return complete


def measure_task_delivery(atlas_server, storage, manifest, outcome, scenario, *,
                          workspace, profile, mode, optimization_stage="selection",
                          max_expansion_calls=100, expansion_batch_size=5):
    """Política reproduzível: expande em ordem, até cobrir evidências ou esgotar refs.

    Não simula decisões de um agente; relações ainda não verificáveis ficam pendentes.
    """
    from codesteer_atlas.config import CHUNK_TRUNCATION_MARKER

    if not 1 <= expansion_batch_size <= 5:
        raise ValueError("expansion_batch_size deve estar entre 1 e 5")

    started = time.perf_counter()
    text, measurement = atlas_server.prepare_search_delivery(
        storage, manifest, outcome, query=scenario["query"], top_k=EVAL_TOP_K,
        profile=profile, include_content=mode == "content",
        optimization_stage=optimization_stage,
    )
    initial = json.loads(text)
    candidates = getattr(outcome, "candidate_pool", None) or outcome.results
    by_identity = {(r.file_path, r.scope_name, r.start_line, r.end_line): r.chunk_id for r in candidates}
    measurements = [measurement]
    evidence = list(initial.get("results", []))
    content_required = scenario["intent"] != "locate"

    def covered(target):
        for item in _complete_expansion_items(evidence):
            if item.get("status") not in (None, "ok"):
                continue
            if content_required and (not item.get("content") or (
                    not item.get("content_complete") and CHUNK_TRUNCATION_MARKER in item["content"])):
                continue
            if _delivery_rank({"results": [item]}, [target], candidates):
                return True
        return False

    refs = list(dict.fromkeys(
        ref for item in initial.get("results", [])
        if (ref := item.get("ref") or by_identity.get((item.get("file_path"), item.get("symbol"), *item.get("lines", []))))
        and (not item.get("content") or CHUNK_TRUNCATION_MARKER in item["content"])
    ))
    expansion_calls = 0
    obsolete = 0
    coverage_by_ref = {item.get("ref"): item.get("covered_refs", []) for item in evidence}
    seen_refs = set()
    continuation_count = 0
    while refs and expansion_calls < max_expansion_calls:
        if all(covered(t) for t in scenario["required_evidence"]):
            break
        batch, refs = refs[:expansion_batch_size], refs[expansion_batch_size:]
        seen_refs.update(batch)
        expanded, measured = atlas_server.prepare_expand_delivery(
            storage, workspace, batch, include_content=True,
        )
        expansion_calls += 1
        measurements.append(measured)
        items = json.loads(expanded).get("results", [])
        for item in items:
            coverage = coverage_by_ref.get(item.get("ref"), [])
            if coverage:
                item["covered_refs"] = coverage
            if item.get("next_ref"):
                coverage_by_ref[item["next_ref"]] = coverage
        evidence.extend(items)
        continuations = [item["next_ref"] for item in items
                         if item.get("next_ref") and item["next_ref"] not in seen_refs]
        continuation_count += len(continuations)
        refs = list(dict.fromkeys(continuations + refs))
        obsolete += sum(item.get("status") == "obsolete" for item in items)
    missing = [t for t in scenario["required_evidence"] if not covered(t)]
    return {
        "scenario_id": scenario["id"], "intent": scenario["intent"],
        "profile": profile, "mode": mode, "optimization_stage": optimization_stage,
        "evidence_complete": not missing,
        "complete": not missing and not scenario.get("relations"),
        "missing_evidence": missing, "unverified_relations": scenario.get("relations", []),
        "expansion_calls": expansion_calls, "obsolete_refs": obsolete,
        "expansion_batch_size": expansion_batch_size,
        "continuations": continuation_count,
        "expansion_limit_reached": bool(missing and refs and expansion_calls >= max_expansion_calls),
        "search_tokens": measurement.tokens,
        "total_tokens": sum(m.tokens for m in measurements) if all(m.tokens is not None for m in measurements) else None,
        "estimated_tokens_total": sum(m.estimated_tokens or 0 for m in measurements),
        "total_bytes": sum(m.bytes for m in measurements),
        "delivery_and_expansion_ms": (time.perf_counter() - started) * 1000,
    }


def run_eval(
    index_dir: Path,
    golden_path: Path,
    *,
    structural: bool = False,
    delivery: bool = False,
    max_cost_usd: float = 1.0,
    repeats: int = 1,
    tasks_path: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> Dict[str, Any]:
    delivery = delivery or tasks_path is not None
    storage = StorageBackend(index_dir=index_dir)
    if not storage.exists():
        raise SystemExit(
            f"Índice não encontrado em {index_dir.resolve()}. "
            "Rode 'uv run atlas-index --workspace . --full' antes."
        )

    queries = _load_golden(golden_path)
    _validate_targets(storage, queries)

    atlas_server = None
    response_budget_mod = None
    if delivery:
        # Import isolado (§7.1 do IPD): server.py redireciona sys.stdout como
        # efeito colateral do import; restaurado imediatamente pelo helper.
        atlas_server = _import_server_and_restore_console()
        from codesteer_atlas import response_budget as response_budget_mod

    engine = EmbeddingEngine()
    per_query: List[Dict[str, Any]] = []
    query_times_ms: List[float] = []
    delivery_by_class_mode: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    manifest = storage.get_manifest()
    relevance_tracker = new_relevance_tracker()
    stopped_early = False

    for _repeat in range(max(1, repeats)):
        for entry in queries:
            query = entry["query"]
            started = time.perf_counter()
            outcome = storage.search_hybrid(
                query_vector=engine.encode_single(query),
                query_text=query,
                filters={},
                top_k=EVAL_TOP_K,
                structural=structural,
                include_candidates=delivery,
            )
            query_times_ms.append((time.perf_counter() - started) * 1000)
            if observe_relevance_usage(
                relevance_tracker, getattr(outcome, "relevance_usage", None), max_cost_usd
            ):
                stopped_early = True
            # Ranking pré-budget: mede exatamente como antes, sobre TODOS os
            # candidatos recuperados — `--delivery` nunca altera esta medição.
            rank = _first_hit_rank(outcome.results, entry["targets"])
            per_query.append(
                {
                    "query": query,
                    "klass": entry["klass"],
                    "rank": rank,
                    "rr": (1.0 / rank) if rank else 0.0,
                    "hit_at_5": bool(rank and rank <= RECALL_AT),
                    "warnings": outcome.warnings,
                }
            )

            if delivery:
                for profile, stage in DELIVERY_VARIANTS:
                    for mode in DELIVERY_MODES:
                        row = _delivery_for_query(
                            atlas_server, response_budget_mod, storage, manifest, outcome,
                            entry["targets"], mode=mode, profile=profile,
                            query=query, optimization_stage=stage,
                        )
                        row["query"] = query
                        row["scenario_id"] = f"golden-{_repeat}-{len(per_query):02d}"
                        key = mode if profile == "full" else f"compact_{stage}_{mode}"
                        delivery_by_class_mode[entry["klass"]][key].append(row)
            if stopped_early:
                break
        if stopped_early:
            break

    task_rows = []
    if tasks_path and not stopped_early:
        scenarios = yaml.safe_load(tasks_path.read_text(encoding="utf-8"))["scenarios"]
        for scenario in scenarios:
            started = time.perf_counter()
            outcome = storage.search_hybrid(
                query_vector=engine.encode_single(scenario["query"]),
                query_text=scenario["query"], filters={}, top_k=EVAL_TOP_K,
                structural=structural, include_candidates=True,
            )
            retrieval_ms = (time.perf_counter() - started) * 1000
            stop = observe_relevance_usage(relevance_tracker, outcome.relevance_usage, max_cost_usd)
            for profile, stage in DELIVERY_VARIANTS:
                for mode in DELIVERY_MODES:
                    row = measure_task_delivery(
                        atlas_server, storage, manifest, outcome, scenario,
                        workspace=workspace or index_dir.resolve().parent,
                        profile=profile, mode=mode, optimization_stage=stage,
                    )
                    row["retrieval_ms"] = retrieval_ms
                    task_rows.append(row)
            if stop:
                stopped_early = True
                break

    by_class: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in per_query:
        by_class[row["klass"]].append(row)

    def _agg(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(rows)
        return {
            "n": n,
            "mrr": round(sum(r["rr"] for r in rows) / n, 4),
            "recall_at_5": round(sum(1 for r in rows if r["hit_at_5"]) / n, 4),
        }

    reranker_info = _active_reranker()
    report: Dict[str, Any] = {
        "task_scenarios": task_rows,
        "stopped_early": stopped_early,
        "top_k": EVAL_TOP_K,
        "overall": _agg(per_query),
        "by_class": {k: _agg(v) for k, v in sorted(by_class.items())},
        "per_query": per_query,
        "structural": structural,
        "reranker": reranker_info["reranker"],
        "rerank_model": reranker_info["rerank_model"],
        "rubric": reranker_info.get("rubric"),
        "total_chunks": manifest.total_chunks,
        "query_time_ms": round(sum(query_times_ms) / len(query_times_ms), 2)
        if query_times_ms
        else 0.0,
        "query_time_ms_p50": _percentile(query_times_ms, 50),
        "query_time_ms_p95": _percentile(query_times_ms, 95),
        "relevance": finalize_relevance_report(relevance_tracker),
        "repeats": repeats,
        "max_cost_usd": max_cost_usd,
    }

    if delivery:
        all_rows_by_mode: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for modes in delivery_by_class_mode.values():
            for mode, rows in modes.items():
                all_rows_by_mode[mode].extend(rows)
        report["delivery"] = {
            "per_query": dict(all_rows_by_mode),
            "budget": {
                "max_chars": RESPONSE_BUDGET_SEARCH_MAX_CHARS,
                "max_bytes": RESPONSE_BUDGET_SEARCH_MAX_BYTES,
                "max_tokens": RESPONSE_BUDGET_SEARCH_MAX_TOKENS,
            },
            "overall": {mode: _agg_delivery(all_rows_by_mode.get(mode, [])) for mode in all_rows_by_mode},
            "by_class": {
                klass: {mode: _agg_delivery(rows) for mode, rows in modes.items()}
                for klass, modes in sorted(delivery_by_class_mode.items())
            },
        }

    return report


def _fmt_delta(current: float, baseline: Optional[float]) -> str:
    if baseline is None:
        return ""
    delta = current - baseline
    if abs(delta) < 1e-9:
        return "     ="
    return f"  {delta:+.4f}"


def print_report(report: Dict[str, Any], baseline: Optional[Dict[str, Any]]) -> None:
    base_classes = (baseline or {}).get("by_class", {})
    base_overall = (baseline or {}).get("overall")

    print(f"\n{'classe':<20} {'n':>3}  {'MRR':>7}{'':>8}  {'recall@5':>8}")
    print("-" * 60)
    for klass, agg in report["by_class"].items():
        b = base_classes.get(klass, {})
        print(
            f"{klass:<20} {agg['n']:>3}  {agg['mrr']:>7.4f}{_fmt_delta(agg['mrr'], b.get('mrr')):>8}"
            f"  {agg['recall_at_5']:>8.4f}{_fmt_delta(agg['recall_at_5'], b.get('recall_at_5'))}"
        )
    print("-" * 60)
    o = report["overall"]
    bo = base_overall or {}
    print(
        f"{'TOTAL':<20} {o['n']:>3}  {o['mrr']:>7.4f}{_fmt_delta(o['mrr'], bo.get('mrr')):>8}"
        f"  {o['recall_at_5']:>8.4f}{_fmt_delta(o['recall_at_5'], bo.get('recall_at_5'))}"
    )
    print(
        f"\nreranker={report.get('reranker', 'lexical')}"
        f"  model={report.get('rerank_model') or '-'}"
        f"  structural={report.get('structural', False)}"
        f"  total_chunks={report.get('total_chunks', '?')}"
        f"  query_time_ms={report.get('query_time_ms', '?')}"
    )
    relevance = report.get("relevance")
    if relevance:
        print(
            "\nrelevância: "
            f"model={relevance.get('model') or '-'} "
            f"rubric={relevance.get('rubric') or '-'} "
            f"requests={relevance.get('requests')} "
            f"valid={relevance.get('valid_evaluations')} "
            f"fallback={relevance.get('fallbacks')} "
            f"cost_known={relevance.get('cost_usd_known')} "
            f"cost_unknown={relevance.get('cost_unknown_count')} "
            f"complete={relevance.get('complete')} "
            f"experimental_success={relevance.get('experimental_success')}"
        )
        if relevance.get("stop_reason"):
            print(f"  execução incompleta: {relevance['stop_reason']}")

    misses = [r for r in report["per_query"] if r["rank"] is None]
    if misses:
        print(f"\nFora do top {report['top_k']} ({len(misses)}):")
        for r in misses:
            print(f"  [{r['klass']}] {r['query']}")

    index_degraded = []
    other_warnings = []
    for r in report["per_query"]:
        if not r["warnings"]:
            continue
        strata = classify_warning_strata(r["warnings"])
        if strata["index_degraded"]:
            index_degraded.append(r)
        else:
            other_warnings.append(r)
    if index_degraded:
        print("\nDegradação de índice (métrica comparável exclui estas):")
        for r in index_degraded:
            print(f"  {r['query']!r}: {', '.join(r['warnings'])}")
    if other_warnings:
        print("\nAvisos não-índice (incluídos nas métricas globais):")
        for r in other_warnings:
            print(f"  {r['query']!r}: {', '.join(r['warnings'])}")

    delivery = report.get("delivery")
    if delivery:
        print("\n--- Resposta entregue (pós-montagem/orçamento) ---")
        budget = delivery.get("budget", {})
        print(
            f"orçamento: max_chars={budget.get('max_chars')} "
            f"max_bytes={budget.get('max_bytes')} max_tokens={budget.get('max_tokens')}"
        )
        # O perfil compacto com seleção é o que o agente recebe com ATLAS_CONTEXT_OPTIMIZATION
        # (e, com Jev, o que passa pelo corte do top_k).
        shown = list(DELIVERY_MODES) + [f"compact_selection_{mode}" for mode in DELIVERY_MODES]
        for mode in shown:
            agg = delivery.get("overall", {}).get(mode)
            if not agg:
                continue
            print(
                f"\n[{mode}] n={agg['n']} comparável={agg['comparable']} "
                f"degradado={agg['degraded']}  tokens_total={agg['response_tokens_total']}"
            )
            print(
                f"  mrr={agg['mrr']}  recall@5={agg['recall_at_5']}  "
                f"truncated_ratio={agg['truncated_ratio']}  "
                f"results_omitted_total={agg['results_omitted_total']}"
            )
            print(
                f"  chars p50={agg['response_chars_p50']} p95={agg['response_chars_p95']}  "
                f"tokens p50={agg['response_tokens_p50']} p95={agg['response_tokens_p95']}  "
                f"count_method={agg['count_method']}"
            )
        for klass, modes in delivery.get("by_class", {}).items():
            for mode, agg in modes.items():
                if agg["truncated_ratio"] > 0:
                    print(
                        f"  [{klass}/{mode}] truncated_ratio={agg['truncated_ratio']} "
                        f"mrr={agg['mrr']} recall@5={agg['recall_at_5']}"
                    )


def _synthetic_search_payload(target_bytes: int) -> dict:
    """Payload fixo (não depende do índice) para o benchmark de overhead."""
    item_template = {
        "file_path": "src/codesteer_atlas/example_module.py",
        "lines": [10, 42],
        "symbol": "example_function",
        "type": "function",
        "language": "python",
        "score": 0.42,
        "repo": "codesteer-atlas",
        "match_arms": ["vector", "fts"],
    }
    items = []
    size = 0
    while size < target_bytes:
        items.append(dict(item_template))
        size = len(json.dumps({"results": items}, ensure_ascii=False))
    return {"results": items, "total_chunks_searched": 1000, "query_time_ms": 12.34}


def _write_benchmark_tokenizer(path: Path) -> Path:
    """
    Tokenizer local minúsculo, gerado em memória (nada é baixado da rede),
    só para medir o custo de uma contagem exata real no benchmark.
    """
    from tokenizers import Tokenizer, models, pre_tokenizers

    vocab = {"[UNK]": 0}
    for word in (
        "src",
        "codesteer_atlas",
        "example_module",
        "py",
        "example_function",
        "function",
        "python",
        "codesteer-atlas",
        "vector",
        "fts",
        "results",
        "total_chunks_searched",
        "query_time_ms",
    ):
        vocab.setdefault(word, len(vocab))
    tokenizer = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.save(str(path))
    return path


def run_overhead_benchmark(*, payload_bytes: int = 24 * 1024, iterations: int = 200) -> Dict[str, Any]:
    """
    Compara o custo de `response_budget.finalize_response` + evento de
    observabilidade em quatro estados (desligado / embarcado / estimativa / custom),
    separando a primeira chamada (carga fria) do estado quente, sobre um
    payload fixo de ~24 KiB (search) e um payload mínimo/vazio — sem depender
    de índice real (§4.4 do plano observabilidade-tokens-consultas).

    Limitação documentada: a persistência do evento (escrita do JSONL) entra
    junto da medição "com evento" — não há aqui um isolamento cirúrgico entre
    o custo de serialização/contagem e o custo de I/O da escrita; ambos ficam
    sob o mesmo número, e o relatório declara isso explicitamente.
    """
    import copy
    import shutil
    import tempfile

    atlas_server = _import_server_and_restore_console()
    from codesteer_atlas import observability as obs
    from codesteer_atlas import response_budget as rb

    def _time_calls(fn, n: int) -> Dict[str, float]:
        cold_start = time.perf_counter()
        fn()
        cold_ms = (time.perf_counter() - cold_start) * 1000
        hot_times = []
        for _ in range(n):
            started = time.perf_counter()
            fn()
            hot_times.append((time.perf_counter() - started) * 1000)
        return {
            "cold_ms": round(cold_ms, 4),
            "hot_mean_ms": round(sum(hot_times) / len(hot_times), 4),
            "hot_p95_ms": round(_percentile(hot_times, 95) or 0.0, 4),
        }

    payloads = {
        "search_24kib": _synthetic_search_payload(payload_bytes),
        "minimal_empty": {"results": [], "total_chunks_searched": 0, "query_time_ms": 0.0},
    }
    budget = rb.ResponseBudget(
        "search",
        RESPONSE_BUDGET_SEARCH_MAX_CHARS,
        RESPONSE_BUDGET_SEARCH_MAX_BYTES,
        RESPONSE_BUDGET_SEARCH_MAX_TOKENS,
    )

    results: Dict[str, Any] = {}
    tmp_dir = tempfile.mkdtemp(prefix="atlas_eval_bench_")
    try:
        for name, base_payload in payloads.items():
            results[name] = {}

            def _finalize_only(base_payload=base_payload):
                payload = copy.deepcopy(base_payload)
                rb.finalize_response(
                    payload,
                    budget,
                    cut_once=atlas_server._search_cut_once,
                    minimal_envelope=atlas_server._search_minimal_envelope,
                )

            os.environ.pop("ATLAS_OBSERVABILITY", None)
            os.environ.pop("ATLAS_TOKENIZER_PATH", None)
            obs.reset_token_counter_for_tests()
            results[name]["observability_off"] = _time_calls(_finalize_only, iterations)

            def _finalize_with_event(base_payload=base_payload):
                payload = copy.deepcopy(base_payload)
                text, measurement = rb.finalize_response(
                    payload,
                    budget,
                    cut_once=atlas_server._search_cut_once,
                    minimal_envelope=atlas_server._search_minimal_envelope,
                )
                event = obs.build_event(
                    tool="atlas_search", outcome="success", duration_ms=0.0, measurement=measurement
                )
                obs.record_event(Path(tmp_dir), event)

            os.environ["ATLAS_OBSERVABILITY"] = "1"
            os.environ.pop("ATLAS_TOKENIZER_PATH", None)
            obs.reset_token_counter_for_tests()
            obs.reset_observability_state_for_tests()
            results[name]["observability_bundled"] = _time_calls(_finalize_with_event, iterations)

            os.environ["ATLAS_TOKENIZER_PATH"] = str(Path(tmp_dir) / "missing.json")
            obs.reset_token_counter_for_tests()
            obs.reset_observability_state_for_tests()
            results[name]["observability_estimate"] = _time_calls(_finalize_with_event, iterations)

            tokenizer_path = _write_benchmark_tokenizer(Path(tmp_dir) / "tokenizer.json")
            os.environ["ATLAS_OBSERVABILITY"] = "1"
            os.environ["ATLAS_TOKENIZER_PATH"] = str(tokenizer_path)
            obs.reset_token_counter_for_tests()
            obs.reset_observability_state_for_tests()
            results[name]["observability_tokenizer"] = _time_calls(_finalize_with_event, iterations)

    finally:
        os.environ.pop("ATLAS_OBSERVABILITY", None)
        os.environ.pop("ATLAS_TOKENIZER_PATH", None)
        obs.reset_token_counter_for_tests()
        obs.reset_observability_state_for_tests()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    results["note"] = (
        "observability_bundled/observability_estimate/observability_tokenizer incluem a persistência "
        "do evento (I/O da escrita do JSONL) junto da serialização/contagem — "
        "não há isolamento cirúrgico entre os dois custos nesta versão do "
        "benchmark; primeira carga (cold_ms) medida separada do estado quente."
    )
    return results


def main() -> int:
    # Constante de módulo lida em tempo de chamada pela recuperação, entrega e cenários.
    global EVAL_TOP_K
    # O console do Windows abre em cp1252 e transformaria as queries em pt-BR do
    # relatório em mojibake, escondendo justamente qual query regrediu.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Avalia o ranking de atlas_search.")
    parser.add_argument("--index-dir", default=".code-index", help="Diretório do índice.")
    parser.add_argument(
        "--golden", default="tests/eval/golden_queries.yaml", help="Golden set YAML."
    )
    parser.add_argument("--baseline", default=None, help="JSON de baseline para comparar.")
    parser.add_argument("--out", default=None, help="Grava o relatório JSON neste caminho.")
    parser.add_argument(
        "--top-k", type=int, default=EVAL_TOP_K,
        help=f"top_k das buscas avaliadas (default: {EVAL_TOP_K}; o default da tool é 5).",
    )
    parser.add_argument(
        "--structural",
        action="store_true",
        help="Liga o braço estrutural nas queries da avaliação (default: desligado).",
    )
    parser.add_argument(
        "--delivery",
        action="store_true",
        help=(
            "Acrescenta o bloco 'delivery': qualidade pós-montagem/orçamento nos "
            "modos metadata/content, sobre os mesmos candidatos recuperados "
            "(default: desligado; não altera o ranking pré-budget histórico)."
        ),
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=1.0,
        help="Teto de custo conhecido por execução (default: 1.0). Custo desconhecido interrompe.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Repetições do golden set (use 3 para expor variância do Jev).",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help=(
            "Roda só o benchmark de overhead (payloads sintéticos fixos, sem "
            "precisar de índice): observabilidade desligada vs. estimativa vs. "
            "tokenizer embarcado/custom, carga fria separada do estado quente."
        ),
    )
    parser.add_argument("--tasks", help="YAML de cenários; conta busca e expansões.")
    parser.add_argument("--workspace", help="Workspace para validar hashes das expansões.")
    args = parser.parse_args()
    if not 1 <= args.top_k <= 50:
        parser.error("--top-k deve estar entre 1 e 50")
    EVAL_TOP_K = args.top_k

    if args.benchmark:
        bench = run_overhead_benchmark()
        print(json.dumps(bench, indent=2, ensure_ascii=False))
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(bench, f, indent=2, ensure_ascii=False)
            print(f"\nBenchmark gravado em {out_path}")
        return 0

    report = run_eval(
        Path(args.index_dir),
        Path(args.golden),
        structural=args.structural,
        delivery=args.delivery,
        max_cost_usd=args.max_cost_usd,
        repeats=args.repeats,
        tasks_path=Path(args.tasks) if args.tasks else None,
        workspace=Path(args.workspace) if args.workspace else None,
    )

    baseline = None
    if args.baseline:
        baseline_path = Path(args.baseline)
        if baseline_path.exists():
            with open(baseline_path, encoding="utf-8") as f:
                baseline = json.load(f)
        else:
            print(f"[aviso] baseline não encontrado: {baseline_path}", file=sys.stderr)

    print_report(report, baseline)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nRelatório gravado em {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
