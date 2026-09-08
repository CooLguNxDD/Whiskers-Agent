"""Patch quality contract — the ask bar, deliberately not the bake bar."""

from plugins.portfolio_plugin.ask.contract import MAX_FISH, assess_patch_quality

BASE_INDEX = [
    {"id": "h1", "type": "hero"},
    {"id": "card-x", "type": "card"},
    {"id": "fish-tank-1", "type": "fishTank"},
]


def _tank(n=3):
    return {
        "type": "fishTank",
        "id": "fish-tank-1",
        "props": {"renderer": "webgl", "fish": [{"slug": f"p{i}"} for i in range(n)]},
    }


def test_empty_patch_is_rejected():
    assert assess_patch_quality([])["ok"] is False


def test_minimal_two_block_patch_passes():
    """A 2-block patch must not be judged by whole-page diversity/band rules."""
    blocks = [_tank(2), {"type": "card", "id": "card-x", "props": {"title": "X"}}]
    verdict = assess_patch_quality(
        blocks, requested_ids=["fish-tank-1", "card-x"], base_index=BASE_INDEX
    )
    assert verdict["ok"] is True, verdict["errors"]
    assert verdict["checked"] == 2


def test_null_optionals_are_rejected():
    """CatPortfolio's Zod mirror rejects null; one bad block fails the layout."""
    block = {"type": "card", "id": "card-x", "props": {"title": "X", "body": None}}
    verdict = assess_patch_quality(block and [block], requested_ids=["card-x"])
    assert verdict["ok"] is False
    assert any("null optionals" in e for e in verdict["errors"])


def test_fish_cap_enforced():
    verdict = assess_patch_quality([_tank(MAX_FISH + 1)], requested_ids=["fish-tank-1"])
    assert verdict["ok"] is False
    assert any("exceeds cap" in e for e in verdict["errors"])


def test_empty_tank_rejected():
    verdict = assess_patch_quality([_tank(0)], requested_ids=["fish-tank-1"])
    assert verdict["ok"] is False


def test_unrequested_block_is_rejected():
    """The router decides what changes; the builder cannot smuggle extras in."""
    verdict = assess_patch_quality(
        [{"type": "prose", "id": "prose-surprise", "props": {"markdown": "hi"}}],
        requested_ids=["card-x"],
    )
    assert verdict["ok"] is False
    assert any("not a requested target" in e for e in verdict["errors"])


def test_type_change_under_a_reused_id_is_rejected():
    """Reusing an id with a new type silently re-bands the DAG."""
    verdict = assess_patch_quality(
        [{"type": "prose", "id": "card-x", "props": {"markdown": "hi"}}],
        requested_ids=["card-x"],
        base_index=BASE_INDEX,
    )
    assert verdict["ok"] is False
    assert any("re-bands the DAG" in e for e in verdict["errors"])


def test_duplicate_ids_rejected():
    verdict = assess_patch_quality(
        [_tank(1), _tank(1)], requested_ids=["fish-tank-1"]
    )
    assert verdict["ok"] is False
    assert any("duplicate" in e for e in verdict["errors"])


def test_new_block_is_a_warning_not_an_error():
    verdict = assess_patch_quality(
        [{"type": "card", "id": "card-new", "props": {"title": "N"}}],
        requested_ids=["card-new"],
        base_index=BASE_INDEX,
    )
    assert verdict["ok"] is True
    assert any("inserts new blocks" in w for w in verdict["warnings"])
