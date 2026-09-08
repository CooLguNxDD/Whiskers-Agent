"""Specialist composer / validator / bake / pipeline unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core_graph.node.routers import specialist_entry_router
from plugins.portfolio_plugin.compose.blackboard import get_draft_store
from plugins.portfolio_plugin.compose.recipes import (
    classify_specialist_goal,
    layout_quality_ok,
    plan_for_goal,
)
from plugins.portfolio_plugin.pipeline import run_portfolio_pipeline


def test_classify_goals():
    assert classify_specialist_goal("bake portfolio for SRE at Acme") == "bake_for_job"
    assert classify_specialist_goal("redesign my portfolio layout") == "redesign"
    assert classify_specialist_goal("discover github projects") == "discover"
    assert classify_specialist_goal("show me your systems work") == "scoped_ask"


def test_plan_for_bake_has_quality_mix():
    plan = plan_for_goal("bake_for_job")
    types = [p["block_type"] for p in plan]
    assert "hero" in types
    assert "kpiGrid" in types
    assert types.count("starStory") >= 2
    assert "quickActions" in types
    assert len(plan) >= 5


def test_layout_quality_gate():
    thin = {"blocks": [{"type": "hero"}, {"type": "hero"}]}
    ok, errs = layout_quality_ok(thin, goal_class="bake_for_job")
    assert ok is False
    assert errs

    rich = {
        "blocks": [
            {"type": "hero"},
            {"type": "kpiGrid"},
            {"type": "flowAnim"},
            {"type": "chart"},
            {"type": "starStory"},
            {"type": "quickActions"},
        ]
    }
    ok2, errs2 = layout_quality_ok(rich, goal_class="bake_for_job")
    assert ok2 is True
    assert errs2 == []


def test_specialist_entry_router_done_vs_plan():
    assert (
        specialist_entry_router(
            {"response": {"status": "ok", "specialist": True, "layout": {"blocks": []}}}
        )
        == "done"
    )
    assert specialist_entry_router({"response": None}) == "plan"
    assert specialist_entry_router({}) == "plan"


@pytest.mark.asyncio
async def test_composer_agent_folds_sections():
    from plugins.portfolio_plugin.agents.composer import run_composer_agent

    draft = get_draft_store().create(goal="show systems work", query="systems")
    layout = {
        "version": 1,
        "meta": {},
        "blocks": [
            {"type": "hero", "props": {}},
            {"type": "projectGrid", "props": {}},
            {"type": "starStory", "props": {}},
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_scoped_layout",
        new_callable=AsyncMock,
        return_value={"status": "ok", "layout": layout, "audience": "default"},
    ):
        out = await run_composer_agent(draft=draft, query="systems", goal_class="scoped_ask", tenant_id=1)
    assert out["status"] == "ok"
    assert draft.layout is not None
    assert len(draft.sections) == 3
    assert draft.phase == "compose"


@pytest.mark.asyncio
async def test_validator_rejects_empty():
    from plugins.portfolio_plugin.agents.validator import run_validator_agent

    draft = get_draft_store().create(goal="x", query="x")
    draft.layout = None
    out = await run_validator_agent(draft=draft, goal_class="redesign")
    assert out["status"] == "error"
    assert out.get("should_retriage") is True


@pytest.mark.asyncio
async def test_pipeline_scoped_ask_success():
    layout = {
        "version": 1,
        "meta": {"mode": "scoped"},
        "blocks": [
            {"type": "hero"},
            {"type": "projectGrid"},
            {"type": "kpiGrid"},
            {"type": "starStory"},
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_scoped_layout",
        new_callable=AsyncMock,
        return_value={"status": "ok", "layout": layout, "audience": "default"},
    ), patch(
        "plugins.portfolio_plugin.schema.ui_layout_schema.validate_layout",
        return_value=(layout, []),
    ):
        out = await run_portfolio_pipeline("show me your platform work", tenant_id=1)
    assert out["status"] == "ok"
    assert out.get("specialist") is True
    assert out.get("layout", {}).get("blocks")
    assert out.get("carry", {}).get("layout")


@pytest.mark.asyncio
async def test_pipeline_discover_only():
    with patch(
        "plugins.portfolio_plugin.pipeline.run_discovery_agent",
        new_callable=AsyncMock,
        return_value={"status": "ok", "doc_count": 2, "docs": []},
    ):
        out = await run_portfolio_pipeline("discover github portfolio context", tenant_id=1)
    assert out["status"] == "ok"
    assert out.get("goal_class") == "discover"


@pytest.mark.asyncio
async def test_pipeline_bake_uses_bake_tool():
    layout = {
        "version": 1,
        "meta": {},
        "blocks": [
            {"type": "hero"},
            {"type": "kpiGrid"},
            {"type": "flowAnim"},
            {"type": "chart"},
            {"type": "starStory"},
            {"type": "quickActions"},
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_scoped_layout",
        new_callable=AsyncMock,
        return_value={"status": "ok", "layout": layout, "audience": "default"},
    ), patch(
        "plugins.portfolio_plugin.schema.ui_layout_schema.validate_layout",
        return_value=(layout, []),
    ), patch(
        "plugins.portfolio_plugin.MCPTools.bake_tools.bake_portfolio_for_job",
        new_callable=AsyncMock,
        return_value={
            "status": "ok",
            "short_id": "acme_sre_001",
            "layout": layout,
        },
    ):
        out = await run_portfolio_pipeline(
            "bake portfolio for SRE at Acme",
            tenant_id=1,
            job_signals={"company": "Acme", "role": "SRE"},
        )
    assert out["status"] in ("ok", "partial")
    assert out.get("short_id") == "acme_sre_001"
    assert "j=" in (out.get("query_param") or "j=acme_sre_001")
