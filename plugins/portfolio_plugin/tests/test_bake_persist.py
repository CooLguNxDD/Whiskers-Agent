"""Short-id allocation, split out of bake_tools into bake/persist.py.

The derived-patch call site had no coverage at all — the split briefly broke it
(NameError on a moved import) and every test still passed. Hence these.
"""

from unittest.mock import AsyncMock, patch

import pytest

from plugins.portfolio_plugin.bake.persist import MAX_SHORT_ID_ATTEMPTS, allocate_short_id

MODULE = "plugins.portfolio_plugin.bake.persist"


@pytest.mark.asyncio
async def test_returns_first_free_candidate():
    with patch(f"{MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)):
        got = await allocate_short_id("Acme", "Engineer")
    assert isinstance(got, str) and got


@pytest.mark.asyncio
async def test_retries_past_collisions():
    calls = {"n": 0}

    async def _exists(_candidate):
        calls["n"] += 1
        return calls["n"] < 3  # first two collide

    with patch(f"{MODULE}.job_layout_short_id_exists", new=_exists):
        got = await allocate_short_id("Acme", "Engineer")
    assert got is not None
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_returns_none_when_attempts_exhausted():
    with patch(
        f"{MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=True)
    ) as exists:
        got = await allocate_short_id("Acme", "Engineer")
    assert got is None
    assert exists.await_count == MAX_SHORT_ID_ATTEMPTS


@pytest.mark.asyncio
async def test_seed_is_variadic_for_the_derived_patch_call_site():
    """patch_job_layout seeds with the parent id plus 'patch', not (company, role)."""
    seen: list[list[str]] = []

    def _gen(parts):
        seen.append(list(parts))
        return "seeded_id_1"

    with patch(f"{MODULE}.generate_short_id", new=_gen), patch(
        f"{MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)
    ):
        got = await allocate_short_id("acme", "eng", "patch")

    assert got == "seeded_id_1"
    assert seen == [["acme", "eng", "patch"]]
