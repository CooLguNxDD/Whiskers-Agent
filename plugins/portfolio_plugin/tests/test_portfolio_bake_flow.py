"""End-to-end coverage for the portfolio_bake_v1 FlowSpec (Phase 5 port).

Verifies:
1. The manifest-declared flow_specs/portfolio_bake_v1.json loads and registers
   via the real plugin-loader path (PluginLifecycleManager._load_flow_specs).
2. The registered flow is selected for a natural-language bake goal.
3. run_flow drives resolve_signals -> bake through the real execute_operation
   plane (mocking only the underlying tool bodies), producing the same
   short_id-bearing envelope shape the legacy pipeline.py path produces.
4. The signal-resolution stage is load-bearing: without it, a free-text goal
   with no structured company/role would fail the bake stage for no reason.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.plugin_loader.lifecycle_manager import PluginLifecycleManager
from core_graph.subgraphs.specialist.flow_registry import (
    clear_flows,
    get_flow_registry,
    select_flow,
)
from core_graph.subgraphs.specialist.flow_runner import run_flow

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_FLOW_PATH = _PLUGIN_DIR / "flow_specs" / "portfolio_bake_v1.json"


@pytest.fixture(autouse=True)
def _clean():
    clear_flows()
    yield
    clear_flows()


def _load_real_spec():
    from types import SimpleNamespace

    spec = SimpleNamespace(name="portfolio_plugin", manifest_path=_PLUGIN_DIR / "manifest.json")
    PluginLifecycleManager._load_flow_specs(spec, ["flow_specs/portfolio_bake_v1.json"])


def test_flow_spec_file_is_valid_json():
    data = json.loads(_FLOW_PATH.read_text(encoding="utf-8"))
    assert data["flow_id"] == "portfolio_bake_v1"


def test_manifest_declares_the_flow_spec():
    manifest = json.loads((_PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    declared = manifest["settings"]["specialist_agent"]["flow_specs"]
    assert "flow_specs/portfolio_bake_v1.json" in declared


def test_loader_registers_real_flow_spec():
    _load_real_spec()
    flow = get_flow_registry().get("portfolio_bake_v1")
    assert flow is not None
    assert flow.owner == "portfolio_plugin"
    assert [s.id for s in flow.stages] == ["resolve_signals", "author_display", "bake"]


def test_select_flow_claims_natural_language_bake_goal():
    _load_real_spec()
    flow = select_flow("bake portfolio for Senior Engineer at Acme", goal_class="bake_for_job")
    assert flow is not None
    assert flow.flow_id == "portfolio_bake_v1"


@pytest.mark.asyncio
async def test_run_flow_resolves_signals_then_bakes():
    """Free-text goal with no structured job_signals still reaches bake_portfolio_for_job
    with company/role populated, because resolve_signals ran first."""
    _load_real_spec()
    flow = get_flow_registry().get("portfolio_bake_v1")

    calls: list[tuple] = []

    async def fake_execute(plugin_id, operation_id, args, **kwargs):
        calls.append((operation_id, dict(args)))
        if operation_id.endswith("resolve_bake_job_signals"):
            from plugins.portfolio_plugin.MCPTools.bake_tools import resolve_bake_job_signals
            return await resolve_bake_job_signals(**args)
        if operation_id.endswith("author_project_display_copy"):
            return {
                "status": "ok",
                "display_copy_by_slug": {
                    "demo": {
                        "fish_blurb": "Demo — MCP. Authored blurb",
                        "card_body": "Demo — MCP. Authored card body.",
                    }
                },
            }
        if operation_id.endswith("bake_portfolio_for_job"):
            assert args.get("company") and args.get("role"), "bake stage must receive resolved signals"
            return {"status": "ok", "short_id": "abc123", "layout": {"blocks": []}, "query_param": "j=abc123"}
        raise AssertionError(f"unexpected op {operation_id}")

    with patch("core.route_registry.execute.execute_operation", new=AsyncMock(side_effect=fake_execute)):
        env = await run_flow(
            flow, "bake portfolio for Senior Engineer at Acme", tenant_id=1
        )

    assert env["status"] == "ok"
    assert env["short_id"] == "abc123"
    ops_called = [c[0] for c in calls]
    assert ops_called == [
        "portfolio_plugin__resolve_bake_job_signals",
        "portfolio_plugin__author_project_display_copy",
        "portfolio_plugin__bake_portfolio_for_job",
    ]
    # resolve_signals actually extracted structured fields from free text.
    bake_args = calls[2][1]
    assert bake_args["company"] == "Acme"
    assert "Senior Engineer" in bake_args["role"]
    # author_display wrote blobs onto the board for bake to read.
    assert isinstance(bake_args.get("display_copy_by_slug"), dict)
    assert "demo" in bake_args["display_copy_by_slug"]


@pytest.mark.asyncio
async def test_run_flow_fails_closed_when_no_company_or_role_derivable():
    _load_real_spec()
    flow = get_flow_registry().get("portfolio_bake_v1")

    async def fake_execute(plugin_id, operation_id, args, **kwargs):
        if operation_id.endswith("resolve_bake_job_signals"):
            from plugins.portfolio_plugin.MCPTools.bake_tools import resolve_bake_job_signals
            return await resolve_bake_job_signals(**args)
        raise AssertionError("bake stage must not run when signals unresolved")

    with patch("core.route_registry.execute.execute_operation", new=AsyncMock(side_effect=fake_execute)):
        env = await run_flow(flow, "please make my portfolio nicer", tenant_id=1)

    assert env["status"] == "error"
    assert env["stage"] == "resolve_signals"
