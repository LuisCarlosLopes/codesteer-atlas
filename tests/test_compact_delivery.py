"""Contratos da entrega compartilhada e referências compatíveis."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_search  # noqa: E402

from codesteer_atlas.context_optimization import encode_expand_ref, sha256_file  # noqa: E402
from codesteer_atlas.models import CandidateEvaluation, SearchOutcome, SearchResult  # noqa: E402
from codesteer_atlas.observability import measure_response  # noqa: E402
from codesteer_atlas.relevance import new_usage  # noqa: E402
from codesteer_atlas.response_budget import ResponseBudget  # noqa: E402
from codesteer_atlas.storage import StorageBackend  # noqa: E402


@pytest.fixture
def server():
    return eval_search._import_server_and_restore_console()


def hit(n, *, path="src/a.py", content=None, start=1, end=5):
    return SearchResult(
        chunk_id=f"{n:016x}", file_path=path, scope_name=f"symbol{n}",
        start_line=start, end_line=end, scope_type="function", repo="demo",
        language="python", content=content or f"body{n}", score=1 / n,
    )


def storage_for(results):
    storage = MagicMock()
    storage._merge_typed.side_effect = lambda code, history, k: StorageBackend._merge_typed(storage, code, history, k)
    storage.get_sections_by_file_path.return_value = []
    storage.get_manifest.return_value = SimpleNamespace(files={}, total_chunks=len(results))
    return storage


def deliver(server, results, **kwargs):
    storage = storage_for(results)
    outcome = kwargs.pop("outcome", SearchOutcome(results=results))
    return server.prepare_search_delivery(storage, storage.get_manifest(), outcome,
                                          profile="compact", **kwargs)


def test_pool_selection_then_dedup_then_limit(server):
    first = hit(1, content="outer inner", end=10)
    duplicate = hit(2, content="inner", start=2, end=4)
    useful = hit(3)
    outcome = SearchOutcome(results=[first, duplicate], candidate_pool=[first, duplicate, useful])
    text, _ = deliver(server, outcome.results, outcome=outcome, top_k=2)
    payload = json.loads(text)
    assert [r["symbol"] for r in payload["results"]] == ["symbol1", "symbol3"]
    assert payload["results"][0]["covered_refs"] == [duplicate.chunk_id]
    assert "expand_hint" not in payload
    assert outcome.results == [first, duplicate]


def test_rejected_container_does_not_hide_uncertain_or_exact(server):
    first = hit(1, content="outer inner", end=10)
    inner = hit(2, content="inner", start=2, end=4)
    evaluation = CandidateEvaluation(candidate_id="1", chunk_id=first.chunk_id,
                                     state="evaluated", score=0, confidence=1)
    outcome = SearchOutcome(results=[first], candidate_pool=[first, inner],
                            pool_evaluations=[evaluation])
    text, _ = deliver(server, outcome.results, outcome=outcome, top_k=1)
    assert json.loads(text)["results"][0]["symbol"] == inner.scope_name
    text, _ = deliver(server, outcome.results, outcome=outcome, top_k=1, query="symbol1")
    assert json.loads(text)["results"][0]["symbol"] == first.scope_name


def test_all_rejected_uses_local_order(server):
    hits = [hit(1), hit(2)]
    evaluations = [CandidateEvaluation(candidate_id=str(i), chunk_id=r.chunk_id,
                                      state="evaluated", score=0, confidence=1)
                   for i, r in enumerate(hits)]
    outcome = SearchOutcome(results=hits[:1], candidate_pool=hits,
                            pool_evaluations=evaluations, local_fallback=list(reversed(hits)))
    text, _ = deliver(server, hits, outcome=outcome, top_k=1)
    payload = json.loads(text)
    assert payload["results"][0]["ref"] == hits[1].chunk_id
    assert "relevance_unconfirmed_fallback" in payload["warnings"]


def test_uncertain_pool_never_excluded(server):
    hits = [hit(1), hit(2)]
    evaluations = [CandidateEvaluation(candidate_id=str(i), chunk_id=r.chunk_id,
                                      state="uncertain", score=0, confidence=0.1)
                   for i, r in enumerate(hits)]
    outcome = SearchOutcome(results=hits, candidate_pool=hits, pool_evaluations=evaluations)
    text, _ = deliver(server, hits, outcome=outcome)
    assert len(json.loads(text)["results"]) == 2


@pytest.mark.parametrize("ceiling", [400, 500, 650, 850])
def test_budget_and_telemetry_measure_final_text(server, monkeypatch, ceiling):
    monkeypatch.setenv("ATLAS_OBSERVABILITY", "1")
    usage = new_usage()
    text, measurement = deliver(server, [hit(i, content=f"{i} ação " * 50) for i in range(1, 6)],
                                include_content=True, relevance_usage=usage,
                                budget=ResponseBudget("search", ceiling, ceiling, 1000))
    payload = json.loads(text)
    assert len(text.encode()) <= ceiling
    assert measurement.bytes == usage.bytes_delivered == len(text.encode())
    assert measurement.tokens == measure_response(text).tokens
    assert payload["omitted"]["budget"] == 5 - len(payload["results"])


def test_full_stays_identical(server):
    hits = [hit(1)]
    storage = storage_for(hits)
    outcome = SearchOutcome(results=hits, candidate_pool=[*hits, hit(2)])
    text, _ = server.prepare_search_delivery(storage, storage.get_manifest(), outcome)
    expected = server.assemble_search_payload(storage, hits, storage.get_manifest(),
                                              include_content=False, query_time_ms=0.0, warnings=[])
    expected_text, _ = server.response_budget.finalize_response(
        expected, server.response_budget.get_budget("search"),
        cut_once=server._search_cut_once, minimal_envelope=server._search_minimal_envelope)
    assert text == expected_text


@pytest.mark.parametrize("legacy", [False, True])
def test_expand_hash_and_compatibility(server, tmp_path, legacy):
    path = tmp_path / "a.py"
    path.write_text("body")
    chunk = hit(1, path="a.py", end=1).model_dump()
    storage = storage_for([])
    storage.get_chunk_by_id.return_value = chunk
    file_hash = sha256_file(path)
    storage.get_manifest.return_value.files = {"a.py": file_hash}
    ref = encode_expand_ref(repo="demo", chunk_id=chunk["chunk_id"], file_hash=file_hash) if legacy else chunk["chunk_id"]
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [ref])
    assert json.loads(text)["results"][0]["status"] == "ok"
    path.write_text("changed")
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [ref])
    assert json.loads(text)["results"][0]["status"] == "obsolete"
    if legacy:
        storage.get_manifest.return_value.files = {"a.py": sha256_file(path)}
        text, _ = server.prepare_expand_delivery(storage, tmp_path, [ref])
        assert json.loads(text)["results"][0]["status"] == "obsolete"


@pytest.mark.parametrize("condition,status", [("removed", "obsolete"), ("outside", "forbidden"), ("no_hash", "obsolete"), ("wrong_repo", "obsolete")])
def test_expand_invalid_index_evidence(server, tmp_path, condition, status):
    (tmp_path / "a.py").write_text("body")
    storage = storage_for([])
    chunk = hit(1, path="../a.py" if condition == "outside" else "a.py").model_dump()
    storage.get_chunk_by_id.return_value = None if condition == "removed" else chunk
    ref = chunk["chunk_id"]
    if condition == "wrong_repo":
        digest = sha256_file(tmp_path / "a.py")
        storage.get_manifest.return_value.files = {"a.py": digest}
        ref = encode_expand_ref(repo="other", chunk_id=ref, file_hash=digest)
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [ref])
    assert json.loads(text)["results"][0]["status"] == status


def test_covered_symbols_need_present_evidence():
    covered = hit(2, content="inner")
    target = [{"file_path": covered.file_path, "scope_name": covered.scope_name}]
    item = {"covered_refs": [covered.chunk_id], "content": "outer inner"}
    assert eval_search._delivery_rank({"results": [item]}, target, [covered]) == 1
    item["content"] = "unrelated"
    assert eval_search._delivery_rank({"results": [item]}, target, [covered]) is None
    assert eval_search._delivery_rank({"results": []}, target, [covered]) is None


def test_harness_and_mcp_share_delivery(server):
    hits = [hit(1)]
    storage = storage_for(hits)
    outcome = SearchOutcome(results=hits)
    text, measurement = server.prepare_search_delivery(storage, storage.get_manifest(), outcome,
                                                        profile="compact", include_content=True)
    row = eval_search._delivery_for_query(server, server.response_budget, storage,
                                         storage.get_manifest(), outcome, [],
                                         mode="content", profile="compact")
    assert row["response_bytes"] == len(text.encode()) == measurement.bytes
    assert row["response_tokens"] == measurement.tokens
    storage.search_hybrid.assert_not_called()


def test_task_counts_expansion_and_does_not_claim_missing_evidence(server, tmp_path):
    (tmp_path / "a.py").write_text("body")
    result = hit(1, path="a.py", content="body", end=1)
    storage = storage_for([result])
    storage.get_manifest.return_value.files = {"a.py": sha256_file(tmp_path / "a.py")}
    storage.get_chunk_by_id.return_value = result.model_dump()
    scenario = {"id": "scenario", "query": "symbol1", "intent": "understand",
                "required_evidence": [{"file_path": "a.py", "scope_name": "symbol1"}]}
    row = eval_search.measure_task_delivery(server, storage, storage.get_manifest(),
                                            SearchOutcome(results=[result]), scenario,
                                            workspace=tmp_path, profile="compact", mode="metadata")
    assert row["complete"]
    assert row["expansion_calls"] == 1
    assert row["total_bytes"] > 0
    storage.get_chunk_by_id.return_value = None
    row = eval_search.measure_task_delivery(server, storage, storage.get_manifest(),
                                            SearchOutcome(results=[result]), scenario,
                                            workspace=tmp_path, profile="compact", mode="metadata")
    assert not row["complete"]
    assert row["obsolete_refs"] == 1


def test_mcp_path_matches_harness_text(server, monkeypatch):
    hits = [hit(1), hit(2)]
    storage = storage_for(hits)
    outcome = SearchOutcome(results=hits, candidate_pool=hits)
    storage.search_hybrid.return_value = outcome
    monkeypatch.setattr(server, "StorageBackend", lambda **kwargs: storage)
    monkeypatch.setattr(server, "EmbeddingEngine", MagicMock())
    monkeypatch.setattr(server, "_resolve_index_dir_via_roots", lambda ctx: None)
    monkeypatch.setattr(server, "_finish_observed", lambda tool, start, text, **kwargs: text)
    actual = server._atlas_search_impl("query", 10, None, None, None, True,
                                      None, False, "compact", None, new_usage(), 0.0)
    expected, _ = server.prepare_search_delivery(storage, storage.get_manifest(), outcome,
                                                 query="query", profile="compact", include_content=True)
    assert actual == expected
    assert storage.search_hybrid.call_count == 1
    assert storage.search_hybrid.call_args.kwargs["include_candidates"] is True
