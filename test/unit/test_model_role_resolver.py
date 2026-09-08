"""Unit tests for core_graph.model_roles.resolver — no DB, synthetic PoolSnapshot only."""

from __future__ import annotations

import time

import pytest

from core_graph.model_roles.resolver import (
    PoolSnapshot,
    _snapshot_cache,
    compile_selector,
    get_pool_snapshot,
    invalidate_pool_snapshot,
    set_effort_map_override,
)

SNAP = PoolSnapshot(
    entries=(
        {"name": "flash-lite", "strength": 0.2, "provider": "gemini", "model": "gemini-3.1-flash-lite"},
        {"name": "sonnet", "strength": 0.9, "provider": "anthropic", "model": "claude-sonnet-5"},
        {"name": "mid", "strength": 0.5, "provider": "openai", "model": "gpt-mid"},
    ),
    min_strength=0.2,
    max_strength=0.9,
    median_strength=0.5,
    fetched_at=time.monotonic(),
)

EMPTY = PoolSnapshot(entries=(), min_strength=0.0, max_strength=0.0, median_strength=0.0, fetched_at=0.0)


@pytest.fixture(autouse=True)
def _clean_effort_override():
    set_effort_map_override(None)
    yield
    set_effort_map_override(None)


def test_core_selector_returns_none():
    assert compile_selector("core", SNAP) is None


def test_strongest_selects_max_strength():
    assert float(compile_selector("strongest", SNAP)) == 0.9


def test_fast_and_weakest_select_min_strength():
    assert float(compile_selector("fast", SNAP)) == 0.2
    assert float(compile_selector("weakest", SNAP)) == 0.2


def test_balanced_selects_median_strength():
    assert float(compile_selector("balanced", SNAP)) == 0.5


def test_name_passthrough():
    assert compile_selector("sonnet", SNAP) == "sonnet"


def test_numeric_passthrough():
    assert compile_selector("0.7", SNAP) == "0.7"


def test_unknown_name_warns_but_passes_through(caplog):
    with caplog.at_level("WARNING"):
        result = compile_selector("totally-made-up", SNAP)
    assert result == "totally-made-up"
    assert any("matches no active pool entry" in r.message for r in caplog.records)


def test_effort_level_resolves_via_default_map():
    assert compile_selector("effort:low", SNAP) == compile_selector("fast", SNAP)
    assert compile_selector("effort:high", SNAP) == compile_selector("strongest", SNAP)
    assert compile_selector("effort:medium", SNAP) == compile_selector("balanced", SNAP)


def test_unknown_effort_level_falls_back_to_core(caplog):
    with caplog.at_level("WARNING"):
        result = compile_selector("effort:extreme", SNAP)
    assert result is None


def test_db_overridden_effort_map_wins():
    set_effort_map_override({"low": "strongest"})
    assert compile_selector("effort:low", SNAP) == compile_selector("strongest", SNAP)


def test_alias_against_empty_pool_degrades_to_core():
    assert compile_selector("strongest", EMPTY) is None
    assert compile_selector("fast", EMPTY) is None


@pytest.mark.asyncio
async def test_snapshot_ttl_expiry_and_invalidation(monkeypatch):
    calls = {"n": 0}

    async def fake_list_pool(kind=None):
        calls["n"] += 1
        return [
            {
                "name": "x",
                "provider": "openai",
                "model": "gpt-x",
                "strength": 1.0,
                "is_active": True,
            }
        ]

    monkeypatch.setattr("core.llm.pool_manager.list_pool", fake_list_pool)
    invalidate_pool_snapshot()

    snap1 = await get_pool_snapshot()
    assert calls["n"] == 1
    assert snap1.entries[0]["name"] == "x"

    # Cached — no second fetch within TTL.
    snap2 = await get_pool_snapshot()
    assert calls["n"] == 1
    assert snap2 is snap1

    invalidate_pool_snapshot()
    await get_pool_snapshot()
    assert calls["n"] == 2

    _snapshot_cache["expires_at"] = 0.0
