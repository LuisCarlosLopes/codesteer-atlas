"""Compara Jev sempre/dispensa exata e expansão 5/2/1 sobre a mesma recuperação.

Apenas a baseline chama Jev; custos evitáveis da política são contrafactuais.
Não modifica a configuração do MCP nem liga a política em produção.
"""

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import eval_search as harness
import yaml

from codesteer_atlas import response_budget
from codesteer_atlas.config import RELEVANCE_GATE_ENV_FLAG
from codesteer_atlas.context_optimization import sha256_file
from codesteer_atlas.embeddings import EmbeddingEngine
from codesteer_atlas.relevance import (
    exact_gate_reason,
    new_usage,
    order_by_relevance,
    resolve_config,
)
from codesteer_atlas.storage import StorageBackend


def gated_outcome(storage, outcome, query):
    """Aplica a mesma promoção local usada pelo servidor quando o gate dispensa Jev."""
    reason = exact_gate_reason(outcome.candidate_pool or [], query,
                              environ={RELEVANCE_GATE_ENV_FLAG: "1"})
    if reason is None:
        return outcome, None
    pool = order_by_relevance(outcome.local_fallback, query, {})
    usage = new_usage()
    usage.status, usage.reason = "skipped", reason
    return outcome.model_copy(update={
        "results": storage._merge_typed(pool, outcome.history_candidates, harness.EVAL_TOP_K),
        "candidate_pool": pool, "local_fallback": pool,
        "evaluations": [], "pool_evaluations": [], "relevance_usage": usage,
        "warnings": [w for w in outcome.warnings if not w.startswith("relevance_")],
    }), reason


def validate_workspace(manifest, workspace):
    """Recusa uma comparação paga com arquivos diferentes dos usados no índice."""
    root = workspace.resolve()
    stale = [path for path, digest in manifest.files.items()
             if not (root / path).resolve().is_relative_to(root)
             or sha256_file(root / path) != digest]
    if stale:
        raise ValueError(f"Workspace diverge do índice em {len(stale)} arquivos; reindexe ou use uma cópia congelada.")


def run_comparison(index_dir, workspace, golden, tasks, max_cost_usd=0.025, *, tasks_only=False):
    if not resolve_config().configured or not resolve_config().enabled:
        raise ValueError("A comparação exige Jev configurado e ATLAS_RELEVANCE=1")
    if os.environ.get("ATLAS_RERANK") == "0":
        raise ValueError("A comparação exige reordenação habilitada")
    if max_cost_usd <= 0:
        raise ValueError("max_cost_usd deve ser positivo")
    storage = StorageBackend(index_dir=index_dir)
    queries = [] if tasks_only else harness._load_golden(golden)
    harness._validate_targets(storage, queries)
    server = harness._import_server_and_restore_console()
    manifest = storage.get_manifest()
    validate_workspace(manifest, workspace)
    engine = EmbeddingEngine()
    tracker = harness.new_relevance_tracker()
    ranking_rows, task_rows, usage_rows = [], [], []
    previous = os.environ.get(RELEVANCE_GATE_ENV_FLAG)
    os.environ[RELEVANCE_GATE_ENV_FLAG] = "0"
    try:
        scenarios = yaml.safe_load(tasks.read_text(encoding="utf-8"))["scenarios"]
        entries = [("golden", entry) for entry in queries] + [("task", entry) for entry in scenarios]
        for sequence, (kind, entry) in enumerate(entries):
            query = entry["query"]
            usage = new_usage()
            started = time.perf_counter()
            outcome = storage.search_hybrid(
                query_vector=engine.encode_single(query), query_text=query,
                filters={}, top_k=harness.EVAL_TOP_K, include_candidates=True,
                relevance_usage=usage,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            gated, reason = gated_outcome(storage, outcome, query)
            scenario_id = entry.get("id", f"golden-{sequence:02d}")
            usage_rows.append({
                "scenario_id": scenario_id, "kind": kind, "query": query,
                "gate_reason": reason, "request_count": usage.request_count,
                "cost_usd": usage.cost_usd, "cost_status": usage.cost_status,
                "status": usage.status, "model": usage.resolved_model,
                "retrieval_ms": elapsed_ms, "jev_ms": usage.duration_ms,
                "warnings": outcome.warnings,
            })
            stop = harness.observe_relevance_usage(tracker, usage, max_cost_usd)
            for policy, selected in [("always", outcome), ("exact_gate", gated)]:
                if kind == "golden":
                    for mode in harness.DELIVERY_MODES:
                        row = harness._delivery_for_query(
                            server, response_budget, storage, manifest, selected, entry["targets"],
                            mode=mode, profile="compact", query=query,
                        )
                        row.update(scenario_id=scenario_id, policy=policy, klass=entry["klass"])
                        ranking_rows.append(row)
                else:
                    for batch_size in [5, 2, 1]:
                        for mode in harness.DELIVERY_MODES:
                            row = harness.measure_task_delivery(
                                server, storage, manifest, selected, entry, workspace=workspace,
                                profile="compact", mode=mode, expansion_batch_size=batch_size,
                            )
                            row["policy"] = policy
                            task_rows.append(row)
            if stop:
                break
    finally:
        if previous is None:
            os.environ.pop(RELEVANCE_GATE_ENV_FLAG, None)
        else:
            os.environ[RELEVANCE_GATE_ENV_FLAG] = previous

    grouped = defaultdict(list)
    for row in ranking_rows:
        grouped[(row["policy"], row["klass"], row["mode"])].append(row)
    by_class = [{"policy": policy, "klass": klass, "mode": mode, **harness._agg_delivery(rows)}
                for (policy, klass, mode), rows in grouped.items()]
    avoidable = [row for row in usage_rows if row["gate_reason"]]
    known = all(row["cost_status"] in {"reported", "not_incurred"} and row["cost_usd"] is not None
                for row in avoidable)
    return {
        "method": "one retrieval and one Jev evaluation per query; gate cost is counterfactual",
        "total_chunks": manifest.total_chunks,
        "actual_relevance": harness.finalize_relevance_report(tracker),
        "counterfactual_avoided": {
            "queries": len(avoidable),
            "requests": sum(row["request_count"] for row in avoidable),
            "cost_usd": sum(row["cost_usd"] for row in avoidable) if known else None,
            "jev_ms": sum(row["jev_ms"] or 0 for row in avoidable),
        },
        "by_class": by_class, "ranking": ranking_rows,
        "tasks": task_rows, "usage": usage_rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=Path(".code-index"))
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--golden", type=Path, default=Path("tests/eval/golden_queries.yaml"))
    parser.add_argument("--tasks", type=Path, default=Path("tests/eval/task_scenarios.yaml"))
    parser.add_argument("--max-cost-usd", type=float, default=0.025)
    parser.add_argument("--tasks-only", action="store_true", help="Repete apenas cenários, sem novas chamadas para o golden set.")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = run_comparison(args.index_dir, args.workspace, args.golden, args.tasks, args.max_cost_usd,
                            tasks_only=args.tasks_only)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"actual": report["actual_relevance"],
                      "counterfactual_avoided": report["counterfactual_avoided"]}, indent=2))


if __name__ == "__main__":
    main()
