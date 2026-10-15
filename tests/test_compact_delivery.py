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
from codesteer_atlas.models import (  # noqa: E402
    CandidateEvaluation,
    CommitRecord,
    SearchOutcome,
    SearchResult,
)
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


def test_rejected_container_does_not_hide_uncertain_or_exact(server, monkeypatch):
    monkeypatch.setenv("ATLAS_RELEVANCE_CUT", "0")
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


@pytest.mark.parametrize("cut", ["0", "1"])
def test_all_rejected_uses_local_order(server, monkeypatch, cut):
    monkeypatch.setenv("ATLAS_RELEVANCE_CUT", cut)
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


def test_uncertain_pool_never_excluded(server, monkeypatch):
    # Regra da seleção conservadora; o corte do top_k (default) corta incerto de score baixo.
    monkeypatch.setenv("ATLAS_RELEVANCE_CUT", "0")
    hits = [hit(1), hit(2)]
    evaluations = [CandidateEvaluation(candidate_id=str(i), chunk_id=r.chunk_id,
                                      state="uncertain", score=0, confidence=0.1)
                   for i, r in enumerate(hits)]
    outcome = SearchOutcome(results=hits, candidate_pool=hits, pool_evaluations=evaluations)
    text, _ = deliver(server, hits, outcome=outcome)
    assert len(json.loads(text)["results"]) == 2


def scored(results, scores, *, state="evaluated", confidence=0.9):
    return [CandidateEvaluation(candidate_id=str(i), chunk_id=r.chunk_id, file_path=r.file_path,
                                scope_name=r.scope_name, state=state, score=s, confidence=confidence)
            for i, (r, s) in enumerate(zip(results, scores, strict=True))]


def commit_hit(sha):
    return SearchResult(
        file_path="", start_line=0, end_line=0, scope_type="", scope_name="", language="git",
        content="fix: commit", score=0.5, repo="demo", type="commit",
        commit=CommitRecord(id=sha, repo="demo", subject="fix: commit",
                            authored_at="2026-09-24T00:00:00+00:00",
                            committed_at="2026-09-24T00:00:00+00:00"),
    )


def test_relevance_cut_is_default_and_turns_top_k_into_ceiling(server, monkeypatch):
    hits = [hit(1), hit(2), hit(3), hit(4)]
    outcome = SearchOutcome(results=hits[:2], candidate_pool=hits,
                            pool_evaluations=scored(hits, [1.9, 0.4, 1.5, 1.8]))
    monkeypatch.delenv("ATLAS_RELEVANCE_CUT", raising=False)
    text, _ = deliver(server, hits, outcome=outcome, top_k=2)
    payload = json.loads(text)
    assert [r["symbol"] for r in payload["results"]] == ["symbol1"]
    assert payload["omitted"]["relevance"] == 1
    # Desligado, volta a seleção conservadora: 0,4 não é "irrelevante inequívoco" e fica.
    monkeypatch.setenv("ATLAS_RELEVANCE_CUT", "0")
    text, _ = deliver(server, hits, outcome=outcome, top_k=2)
    assert [r["symbol"] for r in json.loads(text)["results"]] == ["symbol1", "symbol2"]


def test_relevance_cut_removes_uncertain_but_keeps_exact(server, monkeypatch):
    monkeypatch.delenv("ATLAS_RELEVANCE_CUT", raising=False)
    hits = [hit(1), hit(2), hit(3)]
    outcome = SearchOutcome(results=hits, candidate_pool=hits,
                            pool_evaluations=scored(hits, [0.1, 0.5, 1.2],
                                                    state="uncertain", confidence=0.3))
    text, _ = deliver(server, hits, outcome=outcome, top_k=3, query="symbol1")
    assert [r["symbol"] for r in json.loads(text)["results"]] == ["symbol1", "symbol3"]


def test_relevance_cut_does_not_hand_slots_to_commits(server, monkeypatch):
    monkeypatch.delenv("ATLAS_RELEVANCE_CUT", raising=False)
    hits = [hit(1), hit(2)]
    outcome = SearchOutcome(results=hits, candidate_pool=hits,
                            history_candidates=[commit_hit("a" * 40)],
                            pool_evaluations=scored(hits, [1.9, 0.3]))
    payload = json.loads(deliver(server, hits, outcome=outcome, top_k=2)[0])
    assert [r.get("symbol") for r in payload["results"]] == ["symbol1"]
    # Vaga que o pool já não preenchia continua disponível para o histórico.
    payload = json.loads(deliver(server, hits, outcome=outcome, top_k=3)[0])
    assert [r["type"] for r in payload["results"]] == ["function", "commit"]


def test_relevance_cut_leaves_full_profile_untouched(server, monkeypatch):
    monkeypatch.delenv("ATLAS_RELEVANCE_CUT", raising=False)
    hits = [hit(1), hit(2)]
    outcome = SearchOutcome(results=hits, candidate_pool=hits,
                            pool_evaluations=scored(hits, [1.9, 0.1]))
    storage = storage_for(hits)
    text, _ = server.prepare_search_delivery(storage, storage.get_manifest(), outcome, top_k=2)
    assert [r["symbol"] for r in json.loads(text)["results"]] == ["symbol1", "symbol2"]


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


def test_compact_sem_corte_nao_carrega_bloco_budget(server, tmp_path):
    text, _ = deliver(server, [hit(1), hit(2)], top_k=2)
    assert "budget" not in json.loads(text)
    (tmp_path / "a.py").write_text("body")
    storage = storage_for([])
    storage.get_chunk_by_id.return_value = hit(1, path="a.py", end=1).model_dump()
    storage.get_manifest.return_value.files = {"a.py": sha256_file(tmp_path / "a.py")}
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [f"{1:016x}"])
    payload = json.loads(text)
    assert payload["results"][0]["status"] == "ok"
    assert "budget" not in payload


def _big_class_storage(tmp_path, *, body_lines):
    header = 'class Big:\n    """Classe grande."""\n\n'
    method_a = "    def a(self):\n" + "".join(f"        valor_{i} = {i}\n" for i in range(body_lines))
    method_b = "    def b(self):\n        return 2\n"
    source = header + method_a + method_b
    (tmp_path / "big.py").write_text(source, newline="\n")
    a_start = 4
    a_end = a_start + body_lines
    chunk = {"chunk_id": f"{1:016x}", "file_path": "big.py", "repo": "demo", "start_line": 1,
             "end_line": a_end + 2, "scope_type": "class", "scope_name": "Big",
             "language": "python", "content": "class Big: ...", "references_json": None}
    storage = storage_for([])
    storage.get_chunk_by_id.return_value = chunk
    storage.get_chunks_in_range.return_value = [
        {"id": f"{2:016x}", "scope_name": "Big.a", "scope_type": "method",
         "start_line": a_start, "end_line": a_end},
        {"id": f"{3:016x}", "scope_name": "Big.b", "scope_type": "method",
         "start_line": a_end + 1, "end_line": a_end + 2},
    ]
    storage.get_manifest.return_value.files = {"big.py": sha256_file(tmp_path / "big.py")}
    return storage, header, source


def test_expand_classe_grande_vira_resumo_com_continuacao(server, tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_EXPAND_OUTLINE", raising=False)
    storage, header, source = _big_class_storage(tmp_path, body_lines=400)
    assert len(source) > server.EXPAND_OUTLINE_MIN_CHARS
    payload = json.loads(server.prepare_expand_delivery(storage, tmp_path, [f"{1:016x}"])[0])
    item = payload["results"][0]
    assert item["content"] == header
    assert item["content_complete"] is False
    assert [m["symbol"] for m in item["outline"]] == ["Big.a", "Big.b"]
    assert item["outline"][0]["ref"] == f"{2:016x}"
    assert "expand_class_outline" in payload["warnings"]
    page = json.loads(server.prepare_expand_delivery(storage, tmp_path, [item["next_ref"]])[0])
    assert page["results"][0]["content"].startswith("    def a(self):")
    assert "outline" not in page["results"][0]

    monkeypatch.setenv("ATLAS_EXPAND_OUTLINE", "0")
    item = json.loads(server.prepare_expand_delivery(storage, tmp_path, [f"{1:016x}"])[0])["results"][0]
    assert "outline" not in item
    assert item["content"].startswith(header + "    def a(self):")


def test_expand_classe_pequena_continua_inteira(server, tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_EXPAND_OUTLINE", raising=False)
    storage, _header, source = _big_class_storage(tmp_path, body_lines=5)
    item = json.loads(server.prepare_expand_delivery(storage, tmp_path, [f"{1:016x}"])[0])["results"][0]
    assert "outline" not in item
    assert item["content"] == source
    assert item["content_complete"] is True
    storage.get_chunks_in_range.assert_not_called()


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
