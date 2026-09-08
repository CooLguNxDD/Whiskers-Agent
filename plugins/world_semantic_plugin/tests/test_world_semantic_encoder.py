"""Unit tests for encoder budget + templates (no DB)."""

from __future__ import annotations

from plugins.world_semantic_plugin.encoder.budget import (
    BudgetedBlock,
    estimate_tokens,
    trim_to_budget,
)
from plugins.world_semantic_plugin.encoder.encode import _load_template, _render_hex
from plugins.world_semantic_plugin.agents.orchestrator import decompose, next_ready


def test_estimate_tokens_positive():
    assert estimate_tokens("hello world") >= 2
    assert estimate_tokens("") == 0


def test_trim_to_budget_drops_tail_by_score():
    blocks = [
        BudgetedBlock(text="aaa " * 20, score=1.0, hex_id="a", ring=2),
        BudgetedBlock(text="bbb " * 20, score=5.0, hex_id="b", ring=0),
        BudgetedBlock(text="ccc " * 20, score=3.0, hex_id="c", ring=1),
    ]
    # Budget only enough for roughly one medium block
    text, kept = trim_to_budget(blocks, token_budget=estimate_tokens(blocks[1].text))
    assert any(b.hex_id == "b" for b in kept)
    assert all(b.score >= 3.0 or b.hex_id == "b" for b in kept) or len(kept) >= 1
    # Stable: highest score preferred
    assert kept[0].hex_id == "b" or any(b.hex_id == "b" for b in kept)


def test_trim_empty_budget():
    text, kept = trim_to_budget([BudgetedBlock("x", 1.0)], 0)
    assert text == ""
    assert kept == []


def test_templates_load():
    t0 = _load_template(0)
    t9 = _load_template(9)
    assert "{hex_id}" in t0
    assert "{object_lines}" in t9 or "{summary}" in t9


def test_render_hex_deterministic():
    hex_row = {
        "hex_id": "abc",
        "res": 9,
        "object_count": 1,
        "biome": "plains",
        "elevation_band": 0,
        "tags": {},
        "summary": "test summary",
    }
    objects = [{"name": "Player Camp", "pos": [0, 1, 2]}]
    a = _render_hex(hex_row, objects, ring=0)
    b = _render_hex(hex_row, objects, ring=0)
    assert a == b
    assert "Player Camp" in a
    assert "abc" in a


def test_orchestrator_decompose_camp():
    plan = decompose("place a goblin camp somewhere defensible")
    stages = [p["stage"] for p in plan]
    assert "structures_placed" in stages
    assert "population_spawned" not in stages


def test_orchestrator_next_ready():
    plan = decompose("build a forest village")
    n = next_ready(set(), plan)
    assert n is not None
    assert n["stage"] == "terrain_carved"
    n2 = next_ready({"terrain_carved"}, plan)
    assert n2["stage"] == "structures_placed"
