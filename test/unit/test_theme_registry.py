"""Shared Python theme registry — JSON source of truth, oklch→hex, fail-safe."""

from __future__ import annotations

import logging
from pathlib import Path

from utils.theme_registry import (
    SUPPORTED_THEMES,
    THEME_REGISTRY,
    load_raw_themes,
    oklch_to_hex,
    public_raw_defs,
    reload_registry,
    resolve_theme_vars,
)


def test_extends_resolution_ancestors_first():
    raw = {
        "base": {"id": "base", "label": "Base", "vars": {"bg": "oklch(0.1 0.02 40)", "fg": "white"}},
        "child": {
            "id": "child",
            "label": "Child",
            "extends": "base",
            "vars": {"fg": "green"},
        },
    }
    resolved = resolve_theme_vars("child", raw)
    assert resolved["bg"] == "oklch(0.1 0.02 40)"
    assert resolved["fg"] == "green"


def test_cycle_and_missing_ancestor_log_and_own_vars(caplog):
    cyclic = {
        "a": {"id": "a", "label": "A", "extends": "b", "vars": {"color": "red", "bg": "blue"}},
        "b": {"id": "b", "label": "B", "extends": "a", "vars": {"color": "green"}},
    }
    with caplog.at_level(logging.ERROR, logger="whiskers"):
        a_vars = resolve_theme_vars("a", cyclic)
    assert a_vars == {"color": "red", "bg": "blue"}
    assert any("Cycle" in r.message for r in caplog.records)

    missing = {
        "child": {
            "id": "child",
            "label": "Child",
            "extends": "ghost",
            "vars": {"color": "blue"},
        },
    }
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="whiskers"):
        child_vars = resolve_theme_vars("child", missing)
    assert child_vars == {"color": "blue"}
    assert any("missing" in r.message for r in caplog.records)


def test_oklch_to_hex_mocha_danger_near_catppuccin():
    # mocha --danger oklch(0.756 0.130 3) ≈ Catppuccin red #f38ba8
    hex_ = oklch_to_hex("oklch(0.756 0.130 3)", {})
    assert hex_ is not None
    got = int(hex_[1:], 16)
    want = int("f38ba8", 16)

    def _ch(n: int, shift: int) -> int:
        return (n >> shift) & 0xFF

    assert abs(_ch(got, 16) - _ch(want, 16)) <= 24
    assert abs(_ch(got, 8) - _ch(want, 8)) <= 24
    assert abs(_ch(got, 0) - _ch(want, 0)) <= 24


def test_oklch_to_hex_resolves_var_and_passthrough_hex():
    vars_ = {"neon": "oklch(0.86 0.200 145)", "ok": "var(--neon)"}
    via_var = oklch_to_hex("var(--neon)", vars_)
    direct = oklch_to_hex("oklch(0.86 0.200 145)", vars_)
    assert via_var == direct
    assert oklch_to_hex("#0c3048", {}) == "#0c3048"
    assert oklch_to_hex("not-a-color", {}) is None


def test_live_json_registry_includes_builtin_themes():
    assert {"cozy", "neon", "paper", "latte", "frappe", "macchiato", "mocha"} <= set(
        SUPPORTED_THEMES
    )
    mocha = THEME_REGISTRY["mocha"]
    assert mocha.hex.get("danger")
    assert mocha.vars["bg"].startswith("oklch(")
    defs = public_raw_defs()
    assert "extends" in defs["mocha"]
    assert "vars" in defs["mocha"]
    # Unresolved: mocha's JSON does not itself define radius (inherited from cozy).
    assert "radius" not in (defs["mocha"]["vars"] or {})
    assert load_raw_themes()["cozy"]["vars"]["bg"] == "oklch(0.18 0.018 45)"


def test_empty_theme_dir_falls_back(tmp_path: Path, monkeypatch):
    import utils.theme_registry as tr

    empty = tmp_path / "themes"
    empty.mkdir()
    monkeypatch.setattr(tr, "_theme_dir", lambda: empty)
    try:
        tr.reload_registry()
        assert tr.SUPPORTED_THEMES
        assert "cozy" in tr.SUPPORTED_THEMES
        assert "neon" in tr.SUPPORTED_THEMES
        assert "paper" in tr.SUPPORTED_THEMES
    finally:
        monkeypatch.undo()
        reload_registry()
        assert "mocha" in tr.SUPPORTED_THEMES
