"""Unit tests for the assess_bake_quality MCP tool wrapper.

Thin wrapper over bake.contract.assess_bake_quality (the single quality
contract) — these tests only exercise the wrapper's shape (status envelope,
"passed" promoted to a top-level key), not the contract's scoring logic
(that's covered by test_bake_contract.py).
"""

from __future__ import annotations

import pytest

from plugins.portfolio_plugin.MCPTools.bake_tools import assess_bake_quality
from plugins.portfolio_plugin.tests.test_portfolio_bake import valid_bake_layout


@pytest.mark.asyncio
async def test_valid_layout_passes():
    out = await assess_bake_quality(valid_bake_layout(), goal_class="bake_for_job")
    assert out["status"] == "ok"
    assert out["passed"] is True
    assert out["violations"] == []


@pytest.mark.asyncio
async def test_empty_layout_fails():
    out = await assess_bake_quality({"blocks": []}, goal_class="bake_for_job")
    assert out["status"] == "ok"
    assert out["passed"] is False
    assert any(v["code"] == "blocks_empty" for v in out["violations"])


@pytest.mark.asyncio
async def test_none_layout_fails_gracefully():
    out = await assess_bake_quality(None, goal_class="bake_for_job")
    assert out["passed"] is False
