"""Tests for in-memory, TTL-capped fish pool staging."""

from __future__ import annotations

import pytest

from plugins.portfolio_plugin.ask import fish_pool as fp


@pytest.fixture(autouse=True)
def _reset():
    fp.reset_pools()
    yield
    fp.reset_pools()


def test_stash_get_take_round_trip():
    projects = [
        {
            "slug": "fisoul",
            "name": "Fisoul",
            "blurb": "Virtual fish companion",
            "reason": "closest name match",
            "in_tank": False,
        },
        {
            "slug": "catportfolio",
            "name": "CatPortfolio",
            "summary": "Portfolio UI",
            "tags": ["frontend"],
        },
    ]
    pool_id = fp.stash_pool("sess-1", projects, tenant_id=2)
    assert isinstance(pool_id, str)
    assert len(pool_id) == 16

    # get_pool round trip
    pool = fp.get_pool(pool_id)
    assert pool["pool_id"] == pool_id
    assert "created_at" in pool
    assert len(pool["projects"]) == 2
    assert pool["projects"][0]["slug"] == "fisoul"
    assert pool["projects"][1]["slug"] == "catportfolio"

    # take_from_pool round trip
    taken = fp.take_from_pool(pool_id, ["catportfolio", "fisoul"])
    assert len(taken) == 2
    # preserves requested slugs order
    assert taken[0]["slug"] == "catportfolio"
    assert taken[1]["slug"] == "fisoul"


def test_normalized_shape():
    raw_projects = [
        {
            "slug": "my-project",
            "name": "My Project",
            "blurb": "A fallback summary from blurb",
            "reason": "closest tag match",
            "tags": ["python", "ai"],
            "custom_meta": 42,
        },
        {
            "slug": "tagged-pooled",
            "summary": "Explicit summary",
            "tags": ["Pooled", "backend"],
        },
    ]
    pool_id = fp.stash_pool("sess-norm", raw_projects)
    pool = fp.get_pool(pool_id)
    projs = pool["projects"]

    p0 = projs[0]
    assert p0["virtual"] is True
    assert "pooled" in p0["tags"]
    assert p0["tags"] == ["python", "ai", "pooled"]
    assert p0["summary"] == "A fallback summary from blurb"
    assert p0["reason"] == "closest tag match"
    assert p0["blurb"] == "A fallback summary from blurb"
    assert p0["custom_meta"] == 42
    assert p0["metrics"] == []
    assert p0["links"] == []
    assert p0["context_sources"] == []

    p1 = projs[1]
    assert p1["virtual"] is True
    assert p1["summary"] == "Explicit summary"
    # Case-insensitive check prevents duplicate pooled tag
    assert [t.lower() for t in p1["tags"]].count("pooled") == 1
    assert p1["tags"] == ["Pooled", "backend"]


def test_ttl_expiry_get_and_take(monkeypatch):
    projects = [{"slug": "stale-fish", "name": "Stale"}]
    pool_id = fp.stash_pool("sess-ttl", projects)

    assert fp.get_pool(pool_id) != {}
    assert len(fp.take_from_pool(pool_id, ["stale-fish"])) == 1

    # Advance time beyond default TTL or monkeypatch ttl_s
    monkeypatch.setattr(fp, "_ttl_s", lambda: 10.0)
    current_time = fp._pools[pool_id]["created_at"] + 15.0
    monkeypatch.setattr(fp.time, "time", lambda: current_time)

    assert fp.get_pool(pool_id) == {}
    assert fp.take_from_pool(pool_id, ["stale-fish"]) == []


def test_max_items_cap(monkeypatch):
    monkeypatch.setattr(fp, "_max_items", lambda: 3)
    projects = [
        {"slug": f"proj-{i}", "name": f"Project {i}"}
        for i in range(10)
    ]
    pool_id = fp.stash_pool("sess-cap", projects)
    pool = fp.get_pool(pool_id)

    assert len(pool["projects"]) == 3
    assert [p["slug"] for p in pool["projects"]] == ["proj-0", "proj-1", "proj-2"]


def test_max_pools_eviction(monkeypatch):
    monkeypatch.setattr(fp, "_MAX_POOLS", 4)
    pids = []
    base_time = 1000.0
    for i in range(6):
        monkeypatch.setattr(fp.time, "time", lambda t=base_time + i: t)
        pid = fp.stash_pool(f"sess-{i}", [{"slug": f"item-{i}"}])
        pids.append(pid)

    # Stashing 6 pools when MAX_POOLS=4 means oldest pools are evicted
    # Oldest pools (0, 1, 2) evicted, newest (3, 4, 5) remain
    assert fp.get_pool(pids[0]) == {}
    assert fp.get_pool(pids[1]) == {}
    assert fp.get_pool(pids[2]) == {}
    assert fp.get_pool(pids[3]) != {}
    assert fp.get_pool(pids[4]) != {}
    assert fp.get_pool(pids[5]) != {}


def test_take_from_pool_unknown_pool_id():
    assert fp.take_from_pool("non-existent-id", ["any-slug"]) == []
    assert fp.take_from_pool("", ["any-slug"]) == []
    assert fp.take_from_pool(None, ["any-slug"]) == []


def test_take_from_pool_missing_or_empty_slugs():
    pool_id = fp.stash_pool("sess-slugs", [{"slug": "fish-1"}, {"slug": "fish-2"}])
    # Slug not in pool is omitted
    assert fp.take_from_pool(pool_id, ["missing-slug"]) == []
    assert fp.take_from_pool(pool_id, ["fish-1", "missing-slug"]) == [
        {"slug": "fish-1", "name": "fish-1", "summary": "", "tags": ["pooled"], "metrics": [], "links": [], "context_sources": [], "virtual": True}
    ]
    # Empty or invalid slugs argument
    assert fp.take_from_pool(pool_id, []) == []
    assert fp.take_from_pool(pool_id, None) == []


def test_take_from_pool_case_insensitive_matching():
    pool_id = fp.stash_pool("sess-case", [{"slug": "Fish-Alpha"}, {"slug": "BETA-FISH"}])
    taken = fp.take_from_pool(pool_id, ["beta-fish", "fish-ALPHA"])
    assert len(taken) == 2
    assert taken[0]["slug"] == "BETA-FISH"
    assert taken[1]["slug"] == "Fish-Alpha"


def test_taking_does_not_empty_the_pool():
    pool_id = fp.stash_pool("sess-reuse", [{"slug": "reusable-fish"}])
    take1 = fp.take_from_pool(pool_id, ["reusable-fish"])
    assert len(take1) == 1
    take2 = fp.take_from_pool(pool_id, ["reusable-fish"])
    assert len(take2) == 1
    assert take1 == take2


def test_stash_pool_skips_invalid_and_deduplicates():
    invalid_and_duplicates = [
        None,
        "not-a-dict",
        {"name": "No slug"},
        {"slug": ""},
        {"slug": "duplicate-slug", "name": "First"},
        {"slug": "DUPLICATE-SLUG", "name": "Second"},
    ]
    pool_id = fp.stash_pool("sess-dedup", invalid_and_duplicates)
    pool = fp.get_pool(pool_id)
    assert len(pool["projects"]) == 1
    assert pool["projects"][0]["slug"] == "duplicate-slug"
    assert pool["projects"][0]["name"] == "First"


def test_tenant_int_coercion():
    assert fp._tenant_int(None) == 1
    assert fp._tenant_int("invalid") == 1
    assert fp._tenant_int(5) == 5
    assert fp._tenant_int("7") == 7
