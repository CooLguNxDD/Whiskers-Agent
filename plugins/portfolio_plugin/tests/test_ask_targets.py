"""Ask-mode block addressing: (type, slug) -> real block id."""

import pytest

from plugins.portfolio_plugin.ask.targets import (
    SACRED_BLOCK_TYPES,
    canonical_block_id,
    resolve_targets,
    slug_of_entry,
    tank_entry,
)

INDEX = [
    {"id": "h1", "type": "hero"},
    {"id": "kpi-master", "type": "kpiGrid"},
    {"id": "card-helix-devops", "type": "card"},
    {"id": "proj-ai", "type": "card", "slug": "helix-ai"},
    {"id": "fish-tank-1", "type": "fishTank"},
    {"id": "cta-floor", "type": "quickActions"},
]


def test_canonical_id_slugifies():
    assert canonical_block_id("card", "Helix AI!") == "card-helix-ai"
    assert canonical_block_id("card", "") == "card-1"


def test_exact_canonical_id_match():
    assert resolve_targets(INDEX, "card", "helix-devops") == ["card-helix-devops"]


def test_legacy_agent_id_resolves_via_slug_prop():
    """Layout-agent ids like ``proj-ai`` carry a slug prop; that must win."""
    assert resolve_targets(INDEX, "card", "helix-ai") == ["proj-ai"]


def test_unknown_slug_returns_empty_so_caller_inserts():
    assert resolve_targets(INDEX, "card", "not-a-project") == []


def test_singleton_resolves_without_slug():
    assert resolve_targets(INDEX, "fishTank") == ["fish-tank-1"]


def test_non_singleton_without_slug_is_ambiguous():
    assert resolve_targets(INDEX, "card") == []


@pytest.mark.parametrize("btype", sorted(SACRED_BLOCK_TYPES))
def test_sacred_blocks_never_targeted(btype):
    """Page furniture is bake-owned; an ask turn must not rewrite it."""
    assert resolve_targets(INDEX, btype) == []


def test_sacred_opt_in_still_resolves_for_bake_callers():
    assert resolve_targets(INDEX, "hero", allow_sacred=True) == ["h1"]


def test_slug_suffix_only_counts_when_prefix_is_the_type():
    """``arch-floor`` must not read as project slug ``floor``."""
    assert slug_of_entry({"id": "arch-floor", "type": "archDiagram"}) == ""
    assert slug_of_entry({"id": "card-x", "type": "card"}) == "x"


def test_tank_entry():
    assert tank_entry(INDEX)["id"] == "fish-tank-1"
    assert tank_entry([{"id": "h1", "type": "hero"}]) is None
