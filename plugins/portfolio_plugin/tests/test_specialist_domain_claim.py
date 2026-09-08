"""Portfolio specialist domain claim/decline matrix."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "goal,should_claim",
    [
        ("discover contacts", False),
        ("layout the dashboard", False),
        ("show me the layout of the app", False),
        ("bake portfolio for Acme", True),
        ("redesign my portfolio", True),
        ("discover portfolio projects", True),
        ("portfolio layout for recruiters", True),
        ("catportfolio home page", True),
    ],
)
async def test_portfolio_domain_claim_matrix(goal, should_claim):
    """Domain claims only portfolio goals; declines generic discover/layout."""
    from core_graph.subgraphs.specialist.registry import (
        clear_specialist_domains,
        run_specialist,
    )
    from plugins.portfolio_plugin.plugin_config import PortfolioPlugin

    pipeline_called = {"n": 0}
    generic_called = {"n": 0}

    async def fake_pipeline(goal, **kwargs):
        pipeline_called["n"] += 1
        return {
            "status": "ok",
            "summary": "portfolio claimed",
            "layout": {"version": 1, "blocks": []},
        }

    async def fake_generic(goal, **kwargs):
        generic_called["n"] += 1
        return {
            "status": "ok",
            "summary": "generic claimed",
            "specialist": True,
            "specialist_domain": "generic",
        }

    clear_specialist_domains()
    try:
        # Patch before registration so the domain closure binds the mock.
        with patch(
            "plugins.portfolio_plugin.pipeline.run_portfolio_pipeline",
            new=AsyncMock(side_effect=fake_pipeline),
        ), patch(
            "core_graph.subgraphs.specialist.pipeline.run_specialist_pipeline",
            new=AsyncMock(side_effect=fake_generic),
        ):
            PortfolioPlugin()._register_specialist_domain()
            result = await run_specialist(goal, tenant_id=1)

        if should_claim:
            assert pipeline_called["n"] == 1, f"expected portfolio claim for {goal!r}"
            assert generic_called["n"] == 0
            assert (
                result.get("specialist_domain") == "portfolio"
                or result.get("summary") == "portfolio claimed"
            )
        else:
            assert pipeline_called["n"] == 0, f"expected decline for {goal!r}"
            assert generic_called["n"] == 1
    finally:
        clear_specialist_domains()


def test_portfolio_plugin_registers_subgraph_spec():
    """_register_specialist_domain registers both domain handler and subgraph.

    Moved from test/unit/test_subgraph_registry.py — that file covers the
    generic core_graph.subgraphs.registry mechanism and must not import real
    plugin code (see test_plugin_test_boundary.py); this plugin-specific
    exercise of the mechanism lives with the rest of the plugin's tests.
    """
    from core_graph.subgraphs.registry import ensure_default_subgraphs, get_subgraph
    from plugins.portfolio_plugin.plugin_config import PortfolioPlugin

    ensure_default_subgraphs()
    plugin = PortfolioPlugin()
    plugin._register_specialist_domain()
    sg = get_subgraph("portfolio_specialist")
    assert sg is not None
    assert sg.handler is not None
    assert sg.metadata.get("domain") == "portfolio"
    assert sg.metadata.get("plugin_id") == "portfolio_plugin"
