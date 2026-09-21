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
def server():
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


def test_expand_reads_original_symbol_only(server, tmp_path):
    storage, chunk = fixture_source(tmp_path, "outside\ndef target():\n    return 'ação'\noutside\n")
    text, _ = server.prepare_expand_delivery(storage, tmp_path, [chunk["chunk_id"]])
    item = json.loads(text)["results"][0]
    assert item["content"] == "def target():\n    return 'ação'\n"
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
