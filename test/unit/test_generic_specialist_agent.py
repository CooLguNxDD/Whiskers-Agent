"""Generic specialist stack: tool globs, discovery, pipeline envelope."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core_graph.agent_loop import AgentRunResult, ToolRef
from core_graph.subgraphs.specialist.agents.specialist_agent import (
    discover_tools_from_catalog,
    parse_tool_globs,
)
from core_graph.subgraphs.specialist.pipeline import run_specialist_pipeline
from core_graph.subgraphs.specialist.registry import (
    clear_specialist_domains,
    list_specialist_domains,
    register_specialist_domain,
    run_specialist,
    unregister_specialist_domain,
)


def test_parse_tool_globs_shapes():
    refs = parse_tool_globs(
        [
            "portfolio_plugin/*search*",
            "proxy_github*:list_repos*",
            "search_plugin",
            "*web_search*",
        ]
    )
    by_pid = {(r.plugin_id, r.operation_id) for r in refs}
    assert ("portfolio_plugin", "*search*") in by_pid
    assert ("proxy_github*", "list_repos*") in by_pid
    assert ("search_plugin", "*") in by_pid
    assert ("*", "*web_search*") in by_pid


def test_parse_tool_globs_default_nonempty():
    refs = parse_tool_globs(None)
    assert len(refs) >= 1
    assert all(isinstance(r, ToolRef) for r in refs)


def test_discover_tools_from_catalog_scores_goal():
    class Op:
        def __init__(self, plugin_id, operation_id, description=""):
            self.plugin_id = plugin_id
            self.operation_id = operation_id
            self.description = description

    fake_ops = [
        Op("portfolio_plugin", "search_portfolio_context", "search projects"),
        Op("portfolio_plugin", "design_layout", "compose layout"),
        Op("other_plugin", "unrelated_tool", "zzz"),
    ]
    catalog = MagicMock()
    catalog.all.return_value = fake_ops

    with patch(
        "core.route_registry.operation_catalog.get_operation_catalog",
        return_value=catalog,
    ):
        refs = discover_tools_from_catalog(
            "search portfolio projects",
            tool_globs=["portfolio_plugin/*"],
            max_tools=10,
        )
    oids = [r.operation_id for r in refs]
    assert "search_portfolio_context" in oids
    assert "design_layout" in oids
    assert all(r.plugin_id == "portfolio_plugin" for r in refs)


@pytest.mark.asyncio
async def test_run_specialist_pipeline_envelope():
    fake = AgentRunResult(
        status="ok",
        output={"summary": "Found three matching tools.", "answer": "done"},
        steps=2,
        tool_calls=[{"name": "search_plugin__web_search"}],
        errors=[],
    )
    with patch(
        "core_graph.subgraphs.specialist.agents.specialist_agent.run_agent",
        new_callable=AsyncMock,
        return_value=fake,
    ), patch(
        "core_graph.subgraphs.specialist.agents.specialist_agent.discover_tools_from_catalog",
        return_value=[ToolRef("search_plugin", "*web_search*")],
    ):
        out = await run_specialist_pipeline(
            "search for whiskers docs",
            tenant_id=1,
            tool_globs=["search_plugin/*"],
            session_id="sess-1",
        )
    assert out["status"] == "ok"
    assert out["specialist"] is True
    assert "Found three" in out["summary"]
    assert out.get("session_id") == "sess-1"
    assert any(p.get("phase") == "discover_tools" for p in out.get("phases") or [])
    assert any(p.get("phase") == "execute" for p in out.get("phases") or [])


@pytest.mark.asyncio
async def test_run_specialist_pipeline_missing_goal():
    out = await run_specialist_pipeline("  ", tenant_id=1)
    assert out["status"] == "error"
    assert out["error"] == "missing_goal"


@pytest.mark.asyncio
async def test_domain_registry_prefers_domain():
    # Isolate from portfolio (or other) domains registered by prior imports.
    clear_specialist_domains()

    async def _domain(goal, **kwargs):
        return {
            "status": "ok",
            "summary": f"domain handled: {goal}",
            "specialist": True,
        }

    register_specialist_domain("test_domain", _domain)
    try:
        assert "test_domain" in list_specialist_domains()
        with patch(
            "core_graph.subgraphs.specialist.pipeline.run_specialist_pipeline",
            new_callable=AsyncMock,
        ) as generic:
            out = await run_specialist("hello domain", tenant_id=1)
            generic.assert_not_awaited()
        assert out["status"] == "ok"
        assert "domain handled" in out["summary"]
        assert out.get("specialist_domain") == "test_domain"
    finally:
        clear_specialist_domains()


@pytest.mark.asyncio
async def test_domain_registry_falls_back_to_generic():
    clear_specialist_domains()

    async def _decline(goal, **kwargs):
        return None

    register_specialist_domain("decline_domain", _decline)
    try:
        with patch(
            "core_graph.subgraphs.specialist.pipeline.run_specialist_pipeline",
            new_callable=AsyncMock,
            return_value={"status": "ok", "summary": "generic", "specialist": True},
        ) as generic:
            out = await run_specialist("fallback please", tenant_id=1)
            generic.assert_awaited_once()
        assert out["summary"] == "generic"
    finally:
        clear_specialist_domains()
