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
"""

import argparse
import io
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from codesteer_atlas.config import RERANK_ENV_FLAG, RERANK_MODEL_ENV_FLAG
from codesteer_atlas.embeddings import EmbeddingEngine
from codesteer_atlas.storage import StorageBackend

# Profundidade de busca da avaliação. Maior que o `recall@5` medido para que o
# MRR consiga distinguir "caiu para a posição 8" de "sumiu do resultado".
EVAL_TOP_K = 10
RECALL_AT = 5


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
        return {"reranker": "none", "rerank_model": None}
    model = os.environ.get(RERANK_MODEL_ENV_FLAG)
    if model:
        return {"reranker": "cross_encoder", "rerank_model": model}
    return {"reranker": "lexical", "rerank_model": None}


def run_eval(
    index_dir: Path, golden_path: Path, *, structural: bool = False
) -> Dict[str, Any]:
    storage = StorageBackend(index_dir=index_dir)
    if not storage.exists():
        raise SystemExit(
            f"Índice não encontrado em {index_dir.resolve()}. "
            "Rode 'uv run atlas-index --workspace . --full' antes."
        )

    queries = _load_golden(golden_path)
    _validate_targets(storage, queries)

    engine = EmbeddingEngine()
    per_query: List[Dict[str, Any]] = []
    query_times_ms: List[float] = []

    for entry in queries:
        query = entry["query"]
        started = time.perf_counter()
        outcome = storage.search_hybrid(
            query_vector=engine.encode_single(query),
            query_text=query,
            filters={},
            top_k=EVAL_TOP_K,
            structural=structural,
        )
        query_times_ms.append((time.perf_counter() - started) * 1000)
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
    manifest = storage.get_manifest()
    return {
        "top_k": EVAL_TOP_K,
        "overall": _agg(per_query),
        "by_class": {k: _agg(v) for k, v in sorted(by_class.items())},
        "per_query": per_query,
        "structural": structural,
        "reranker": reranker_info["reranker"],
        "rerank_model": reranker_info["rerank_model"],
        "total_chunks": manifest.total_chunks,
        "query_time_ms": round(sum(query_times_ms) / len(query_times_ms), 2)
        if query_times_ms
        else 0.0,
    }


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

    misses = [r for r in report["per_query"] if r["rank"] is None]
    if misses:
        print(f"\nFora do top {report['top_k']} ({len(misses)}):")
        for r in misses:
            print(f"  [{r['klass']}] {r['query']}")

    degraded = [r for r in report["per_query"] if r["warnings"]]
    if degraded:
        print("\nBuscas degradadas (a métrica abaixo NÃO é comparável):")
        for r in degraded:
            print(f"  {r['query']!r}: {', '.join(r['warnings'])}")


def main() -> int:
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
        "--structural",
        action="store_true",
        help="Liga o braço estrutural nas queries da avaliação (default: desligado).",
    )
    args = parser.parse_args()

    report = run_eval(Path(args.index_dir), Path(args.golden), structural=args.structural)

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
