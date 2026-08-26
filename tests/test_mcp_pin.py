"""Regressão offline: teto direto de `mcp` no pyproject (uvx ignora o lock)."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mcp_direct_spec(deps: list[str]) -> str:
    matches = [d for d in deps if d.startswith("mcp>=") or d.startswith("mcp==")]
    assert len(matches) == 1, matches
    return matches[0]


def test_pyproject_mcp_specifier_caps_below_2():
    with (ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    spec = _mcp_direct_spec(data["project"]["dependencies"])
    assert "<2" in spec
    assert "<3" not in spec


def test_uv_lock_mcp_metadata_specifier_caps_below_2():
    with (ROOT / "uv.lock").open("rb") as fh:
        data = tomllib.load(fh)
    atlas = next(pkg for pkg in data["package"] if pkg["name"] == "codesteer-atlas")
    mcp_req = next(req for req in atlas["metadata"]["requires-dist"] if req["name"] == "mcp")
    spec = mcp_req["specifier"]
    assert "<2" in spec
    assert "<3" not in spec
