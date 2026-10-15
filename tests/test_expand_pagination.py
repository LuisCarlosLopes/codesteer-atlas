"""Expansão integral e continuação verificável, sem cache de sessão."""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_search  # noqa: E402

from codesteer_atlas.config import CHUNK_TRUNCATION_MARKER  # noqa: E402
from codesteer_atlas.response_budget import ResponseBudget  # noqa: E402


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("ATLAS_OBSERVABILITY", "1")
    return eval_search._import_server_and_restore_console()


def fixture_source(tmp_path, content):
    path = tmp_path / "source.py"
    path.write_bytes(content.encode())
    chunk = {"chunk_id": "a" * 16, "repo": "demo", "file_path": "source.py",
             "start_line": 2, "end_line": len(content.splitlines()) - 1,
             "scope_name": "target", "scope_type": "function",
             "content": "prefix" + CHUNK_TRUNCATION_MARKER}
    storage = MagicMock()
    storage.get_chunk_by_id.return_value = chunk
    storage.get_manifest.return_value = SimpleNamespace(files={"source.py": hashlib.sha256(path.read_bytes()).hexdigest()})
    return storage, chunk


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_expand_reads_original_symbol_only(server, tmp_path, newline):
    storage, chunk = fixture_source(tmp_path, "outside\ndef target():\n    return 'ação'\noutside\n".replace("\n", newline))
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [chunk["chunk_id"]])
    item = json.loads(text)["results"][0]
    assert item["content"] == "def target():\n    return 'ação'\n".replace("\n", newline)
    assert item["content_complete"] is True
    assert "expand_indexed_truncated" not in json.loads(text).get("warnings", [])


def test_pagination_reconstructs_unicode_and_long_line_within_budget(server, tmp_path):
    source = "before\n" + "á😀\\\" " * 700 + "\nafter\n"
    storage, chunk = fixture_source(tmp_path, source)
    ref = chunk["chunk_id"]
    parts = []
    for _ in range(100):
        text, measurement = server.prepare_expand_delivery(
            storage, tmp_path, [ref], budget=ResponseBudget("search", 1800, 1800, 450))
        assert len(text.encode()) == measurement.bytes <= 1800
        if measurement.tokens is not None:
            assert measurement.tokens <= 450
        item = json.loads(text)["results"][0]
        assert item["status"] == "ok"
        assert item["content"]
        assert item["content_range"][0] == sum(len(part) for part in parts)
        parts.append(item["content"])
        if item["content_complete"]:
            assert "next_ref" not in item
            break
        ref = item["next_ref"]
    else:
        pytest.fail("A paginação não terminou")
    assert "".join(parts) == source.splitlines(keepends=True)[1]


def test_continuation_invalid_after_file_change_even_with_new_manifest(server, tmp_path):
    storage, chunk = fixture_source(tmp_path, "before\n" + "x" * 5000 + "\nafter\n")
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [chunk["chunk_id"]],
                                             budget=ResponseBudget("search", 1400, 1400, 400))
    ref = json.loads(text)["results"][0]["next_ref"]
    path = tmp_path / "source.py"
    path.write_text("before\nchanged\nafter\n")
    storage.get_manifest.return_value.files["source.py"] = hashlib.sha256(path.read_bytes()).hexdigest()
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [ref])
    assert json.loads(text)["results"][0]["status"] == "obsolete"


def test_invalid_offset_is_not_complete_content(server, tmp_path):
    storage, chunk = fixture_source(tmp_path, "before\nbody\nafter\n")
    digest = storage.get_manifest.return_value.files["source.py"]
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [f'{chunk["chunk_id"]}:{digest}:9999'])
    assert json.loads(text)["results"][0]["status"] == "invalid_ref"


def test_harness_requires_contiguous_pages():
    first = {"ref": "first", "status": "ok", "file_path": "a.py", "symbol": "f", "lines": [1, 5],
             "content": "abc", "content_range": [0, 3], "content_complete": False, "next_ref": "last"}
    last = {**first, "ref": "last", "content": "def", "content_range": [3, 6], "content_complete": True}
    assert eval_search._complete_expansion_items([last]) == []
    assert eval_search._complete_expansion_items([first]) == []
    assert eval_search._complete_expansion_items([first, last])[0]["content"] == "abcdef"
    last["content_range"] = [4, 7]
    assert eval_search._complete_expansion_items([first, last]) == []


def test_harness_follows_pages_and_counts_all_tokens(server, tmp_path, monkeypatch):
    from codesteer_atlas.models import SearchOutcome, SearchResult

    source = "before\n" + "long statement á😀 " * 200 + "\nafter\n"
    storage, chunk = fixture_source(tmp_path, source)
    storage.get_manifest.return_value.total_chunks = 1
    result = SearchResult(**{k: v for k, v in chunk.items() if k != "content"},
                          content=chunk["content"], score=1, language="python")
    scenario = {"id": "pages", "query": "target", "intent": "understand",
                "required_evidence": [{"file_path": "source.py", "scope_name": "target"}]}
    original = server.prepare_expand_delivery

    def bounded(*args, **kwargs):
        return original(*args, **kwargs, budget=ResponseBudget("search", 1500, 1500, 400))

    monkeypatch.setattr(server, "prepare_expand_delivery", bounded)
    outcome = SearchOutcome(results=[result])
    limited = eval_search.measure_task_delivery(server, storage, storage.get_manifest(), outcome,
                                                scenario, workspace=tmp_path, profile="compact", mode="metadata",
                                                max_expansion_calls=1)
    assert not limited["complete"]
    assert limited["expansion_limit_reached"]
    complete = eval_search.measure_task_delivery(server, storage, storage.get_manifest(), outcome,
                                                 scenario, workspace=tmp_path, profile="compact", mode="metadata")
    assert complete["complete"]
    assert complete["expansion_calls"] > 1
    assert complete["total_bytes"] > limited["total_bytes"]


def test_batched_expansions_preserve_each_ref_and_bound_total(server, tmp_path):
    storage, chunk = fixture_source(tmp_path, "before\n" + "á😀 word " * 1000 + "\nafter\n")
    refs = [f"{i:016x}" for i in range(5)]
    storage.get_chunk_by_id.side_effect = lambda ref: {**chunk, "chunk_id": ref}
    text, measured = server.prepare_expand_delivery(storage, tmp_path, refs)
    items = json.loads(text)["results"]
    assert [item["ref"] for item in items] == refs
    assert measured.bytes <= server.RESPONSE_BUDGET_SEARCH_MAX_BYTES
    assert all(item["content"] for item in items)
    assert any(item.get("next_ref") for item in items)


@pytest.mark.parametrize("condition,reason", [("encoding", "invalid_encoding"), ("range", "invalid_line_range")])
def test_source_errors_do_not_return_partial_evidence(server, tmp_path, condition, reason):
    storage, chunk = fixture_source(tmp_path, "before\nbody\nafter\n")
    if condition == "range":
        chunk["end_line"] = 10000
    else:
        path = tmp_path / "source.py"
        path.write_bytes(b"before\n\xff\nafter\n")
        storage.get_manifest.return_value.files["source.py"] = hashlib.sha256(path.read_bytes()).hexdigest()
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [chunk["chunk_id"]])
    item = json.loads(text)["results"][0]
    assert item["reason"] == reason
    assert "content" not in item


def test_unicode_separator_does_not_change_indexed_line_numbers(server, tmp_path):
    storage, chunk = fixture_source(tmp_path, "before\nvalue = 'a\u2028b'\nafter\n")
    chunk["end_line"] = 2
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [chunk["chunk_id"]])
    assert json.loads(text)["results"][0]["content"] == "value = 'a\u2028b'\n"
