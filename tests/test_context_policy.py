"""Dispensa exata pareada e expansão incremental sem usar os alvos para reordenar."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_context_policy as policy  # noqa: E402
import eval_search as harness  # noqa: E402

from codesteer_atlas.models import SearchOutcome, SearchResult  # noqa: E402
from codesteer_atlas.relevance import new_usage  # noqa: E402
from codesteer_atlas.storage import StorageBackend  # noqa: E402


def hit(n, name):
    return SearchResult(chunk_id=f"{n:016x}", file_path=f"{name}.py", start_line=1,
                        end_line=1, scope_name=name, scope_type="function", language="python",
                        repo="demo", content="body", score=1)


def test_counterfactual_keeps_actual_usage_and_promotes_exact():
    items = [hit(1, "other"), hit(2, "target")]
    usage = new_usage()
    usage.request_count, usage.cost_status, usage.cost_usd = 1, "reported", 0.001
    outcome = SearchOutcome(results=items, candidate_pool=items, local_fallback=items, relevance_usage=usage)
    storage = MagicMock()
    storage._merge_typed.side_effect = lambda code, history, k: StorageBackend._merge_typed(storage, code, history, k)
    gated, reason = policy.gated_outcome(storage, outcome, "target")
    assert reason == "unique_exact_symbol"
    assert gated.results[0].scope_name == "target"
    assert gated.relevance_usage.request_count == 0
    assert outcome.relevance_usage.request_count == 1
    assert outcome.relevance_usage.cost_usd == 0.001
    assert outcome.results == items
    assert policy.gated_outcome(storage, outcome, "how target works") == (outcome, None)


def test_partial_cost_preserves_known_part_and_stops():
    usage = new_usage()
    usage.request_count, usage.cost_status, usage.cost_usd = 2, "partially_known", 0.001
    tracker = harness.new_relevance_tracker()
    assert harness.observe_relevance_usage(tracker, usage, 1)
    report = harness.finalize_relevance_report(tracker)
    assert report["cost_usd_known"] == 0.001
    assert report["cost_unknown_count"] == 1
    assert not report["complete"]


def test_single_expansion_stops_after_evidence_without_fetching_extra():
    server = harness._import_server_and_restore_console()
    items = [hit(1, "target"), hit(2, "extra")]
    storage = MagicMock()
    manifest = SimpleNamespace(files={}, total_chunks=2)
    storage.get_sections_by_file_path.return_value = []
    scenario = {"id": "single", "query": "target", "intent": "understand",
                "required_evidence": [{"file_path": "target.py", "scope_name": "target"}]}
    fetched = []

    def expand(_storage, _workspace, refs, **kwargs):
        import json

        fetched.append(refs)
        results = [{"ref": ref, "status": "ok", "file_path": item.file_path,
                    "symbol": item.scope_name, "content": "complete"}
                   for ref in refs for item in items if ref == item.chunk_id]
        text = json.dumps({"results": results})
        return text, server.response_budget.measure_response(text)

    proxy = SimpleNamespace(prepare_search_delivery=server.prepare_search_delivery,
                            prepare_expand_delivery=expand)
    outcome = SearchOutcome(results=items)
    single = harness.measure_task_delivery(proxy, storage, manifest, outcome, scenario,
                                           workspace=Path.cwd(), profile="compact", mode="metadata",
                                           expansion_batch_size=1)
    assert single["complete"]
    assert fetched == [[items[0].chunk_id]]
    fetched.clear()
    batch = harness.measure_task_delivery(proxy, storage, manifest, outcome, scenario,
                                          workspace=Path.cwd(), profile="compact", mode="metadata",
                                          expansion_batch_size=5)
    assert batch["complete"]
    assert fetched == [[item.chunk_id for item in items]]
    assert single["total_bytes"] < batch["total_bytes"]


@pytest.mark.parametrize("size", [0, 6])
def test_expansion_batch_size_validation(size):
    with pytest.raises(ValueError, match="expansion_batch_size"):
        harness.measure_task_delivery(None, None, None, None, None, workspace=Path.cwd(),
                                      profile="compact", mode="metadata", expansion_batch_size=size)


def test_comparison_rejects_changed_workspace_before_spending(tmp_path):
    import hashlib

    path = tmp_path / "source.py"
    path.write_bytes(b"old")
    manifest = SimpleNamespace(files={"source.py": hashlib.sha256(b"old").hexdigest()})
    policy.validate_workspace(manifest, tmp_path)
    path.write_bytes(b"new")
    with pytest.raises(ValueError, match="Workspace diverge"):
        policy.validate_workspace(manifest, tmp_path)
    manifest.files = {"../outside.py": "unused"}
    with pytest.raises(ValueError, match="Workspace diverge"):
        policy.validate_workspace(manifest, tmp_path)
