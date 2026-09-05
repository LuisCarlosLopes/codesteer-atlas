"""
Testes do bloco `delivery` de `scripts/eval_search.py` (T6 do plano
observabilidade-tokens-consultas). Cobre V11 (alvo presente pré-budget e
removido pós-budget, sem alterar o ranking histórico) e a compatibilidade de
`print_report` com uma baseline antiga sem o bloco `delivery`.

Não depende de índice real: os testes chamam as funções de agregação/medição
diretamente, com `SearchResult`/`SearchOutcome` sintéticos.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_search  # noqa: E402

from codesteer_atlas.models import IndexManifest, SearchOutcome, SearchResult  # noqa: E402


def _manifest() -> IndexManifest:
    return IndexManifest(
        total_chunks=2,
        repos_indexed=["demo"],
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        embedding_dim=384,
        last_indexed_at="2026-09-05T00:00:00+00:00",
        git_head_sha="abc1234",
        languages_indexed=["python"],
        index_version="2.3.0",
        files={"src/a.py": "hash", "src/b.py": "hash"},
    )


def _result(symbol: str, content: str, *, score: float) -> SearchResult:
    return SearchResult(
        file_path=f"src/{symbol}.py",
        start_line=1,
        end_line=5,
        scope_type="function",
        scope_name=symbol,
        language="python",
        content=content,
        score=score,
        repo="demo",
    )


def _fake_storage():
    storage = MagicMock()
    storage.get_sections_by_file_path.return_value = []
    return storage


@pytest.fixture(autouse=True)
def _import_server_once():
    """
    Importa `codesteer_atlas.server` uma vez (import pesado, mas necessário
    para reaproveitar `assemble_search_payload`) e devolve o módulo já com o
    console do processo de teste restaurado.
    """
    return eval_search._import_server_and_restore_console()


def test_percentile_nearest_rank_no_interpolation():
    assert eval_search._percentile([], 50) is None
    assert eval_search._percentile([10.0], 95) == 10.0
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert eval_search._percentile(values, 50) == 5.0
    assert eval_search._percentile(values, 95) == 10.0


def test_delivery_rank_reads_serialized_symbol_field():
    final_payload = {
        "results": [
            {"file_path": "src/b.py", "symbol": "other"},
            {"file_path": "src/a.py", "symbol": "target"},
        ]
    }
    targets = [{"file_path": "src/a.py", "scope_name": "target"}]
    assert eval_search._delivery_rank(final_payload, targets) == 2
    assert eval_search._delivery_rank({"results": []}, targets) is None


def test_delivery_detects_target_removed_by_budget_without_changing_pre_budget_rank(
    _import_server_once, monkeypatch
):
    """
    V11: o alvo está presente e em 1º lugar antes do orçamento; um orçamento
    de caracteres propositalmente minúsculo remove-o da resposta ENTREGUE
    (`content`), mas a leitura pré-budget (`_first_hit_rank`) continua achando
    o alvo em 1º — a prova de que `--delivery` nunca modifica o ranking
    histórico, só acrescenta a leitura pós-montagem/orçamento.
    """
    atlas_server = _import_server_once
    manifest = _manifest()
    storage = _fake_storage()

    target_result = _result("target", "x" * 500, score=0.9)
    other_result = _result("other", "y" * 10, score=0.1)
    outcome = SearchOutcome(results=[target_result, other_result], warnings=[])
    targets = [{"file_path": "src/target.py", "scope_name": "target"}]

    # Ranking pré-budget: o alvo está em 1º — precisa continuar assim.
    pre_budget_rank = eval_search._first_hit_rank(outcome.results, targets)
    assert pre_budget_rank == 1

    # Orçamento minúsculo o bastante para o corte remover o resultado grande
    # (o alvo) da cauda antes do pequeno.
    monkeypatch.setattr(eval_search, "RESPONSE_BUDGET_SEARCH_MAX_CHARS", 260)
    monkeypatch.setattr(eval_search, "RESPONSE_BUDGET_SEARCH_MAX_BYTES", 260)

    delivery_row = eval_search._delivery_for_query(
        atlas_server,
        __import__("codesteer_atlas.response_budget", fromlist=["response_budget"]),
        storage,
        manifest,
        outcome,
        targets,
        mode="content",
    )

    # Pós-budget: o alvo (maior, no fim da lista de sobreviventes por ordem
    # decrescente de score -> ficou por último) foi cortado da resposta entregue.
    assert delivery_row["truncated"] is True
    assert delivery_row["results_omitted"] >= 1
    assert delivery_row["rank"] is None or delivery_row["rank"] != pre_budget_rank

    # O ranking pré-budget não foi tocado por essa chamada.
    assert eval_search._first_hit_rank(outcome.results, targets) == 1


def test_delivery_metadata_mode_survives_small_budget_that_cuts_content_mode(
    _import_server_once, monkeypatch
):
    """Mesmo cenário, modo metadata (sem 'content'): cabe onde o modo content não cabia."""
    atlas_server = _import_server_once
    response_budget_mod = __import__(
        "codesteer_atlas.response_budget", fromlist=["response_budget"]
    )
    manifest = _manifest()
    storage = _fake_storage()

    target_result = _result("target", "x" * 500, score=0.9)
    outcome = SearchOutcome(results=[target_result], warnings=[])
    targets = [{"file_path": "src/target.py", "scope_name": "target"}]

    monkeypatch.setattr(eval_search, "RESPONSE_BUDGET_SEARCH_MAX_CHARS", 450)
    monkeypatch.setattr(eval_search, "RESPONSE_BUDGET_SEARCH_MAX_BYTES", 450)

    metadata_row = eval_search._delivery_for_query(
        atlas_server, response_budget_mod, storage, manifest, outcome, targets, mode="metadata"
    )
    assert metadata_row["rank"] == 1
    assert metadata_row["truncated"] is False


def test_agg_delivery_excludes_degraded_from_mrr_but_counts_them():
    rows = [
        {
            "rank": 1,
            "rr": 1.0,
            "hit_at_5": True,
            "degraded": False,
            "response_chars": 100,
            "response_bytes": 100,
            "response_tokens": None,
            "estimated_tokens": 25,
            "count_method": "chars_div_4",
            "tokenizer_sha256": None,
            "results_omitted": 0,
            "truncated": False,
        },
        {
            "rank": None,
            "rr": 0.0,
            "hit_at_5": False,
            "degraded": True,  # busca degradada: não deve contar no mrr comparável
            "response_chars": 50,
            "response_bytes": 50,
            "response_tokens": None,
            "estimated_tokens": 13,
            "count_method": "chars_div_4",
            "tokenizer_sha256": None,
            "results_omitted": 2,
            "truncated": True,
        },
    ]
    agg = eval_search._agg_delivery(rows)
    assert agg["n"] == 2
    assert agg["comparable"] == 1
    assert agg["degraded"] == 1
    assert agg["mrr"] == 1.0  # só a linha não-degradada entra
    assert agg["results_omitted_total"] == 2
    assert agg["truncated_ratio"] == 0.5


def test_agg_delivery_empty_returns_none_not_zero():
    agg = eval_search._agg_delivery([])
    assert agg["n"] == 0
    assert agg["mrr"] is None
    assert agg["recall_at_5"] is None


def test_print_report_accepts_old_baseline_without_delivery_block(capsys):
    """Baseline antiga (sem 'delivery') continua legível para o ranking histórico."""
    report_with_delivery = {
        "top_k": 10,
        "overall": {"n": 1, "mrr": 1.0, "recall_at_5": 1.0},
        "by_class": {"exact_symbol": {"n": 1, "mrr": 1.0, "recall_at_5": 1.0}},
        "per_query": [{"query": "q", "klass": "exact_symbol", "rank": 1, "warnings": []}],
        "reranker": "lexical",
        "rerank_model": None,
        "structural": False,
        "total_chunks": 10,
        "query_time_ms": 1.0,
        "delivery": {
            "budget": {"max_chars": 24000, "max_bytes": 24000, "max_tokens": 6000},
            "overall": {
                "metadata": eval_search._agg_delivery([]),
                "content": eval_search._agg_delivery([]),
            },
            "by_class": {},
        },
    }
    old_baseline = {
        "overall": {"mrr": 0.9, "recall_at_5": 0.9},
        "by_class": {"exact_symbol": {"mrr": 0.9, "recall_at_5": 0.9}},
    }

    eval_search.print_report(report_with_delivery, old_baseline)
    captured = capsys.readouterr()
    assert "TOTAL" in captured.out
    assert "Resposta entregue" in captured.out
