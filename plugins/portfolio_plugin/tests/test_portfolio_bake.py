"""Unit tests for portfolio_plugin bake tools (Feature B "bake & send")."""

import pytest
from unittest.mock import AsyncMock, patch

from plugins.portfolio_plugin.MCPTools.bake_tools import (
    _compose_job_layout,
    bake_portfolio_for_job,
    resolve_job_posting_text,
)
from plugins.portfolio_plugin.MCPTools.bake_admin_tools import list_bake_runs

MODULE = "plugins.portfolio_plugin.MCPTools.bake_tools"
ADMIN_MODULE = "plugins.portfolio_plugin.MCPTools.bake_admin_tools"
# Split out of bake_tools; patch these where they now live.
SIGNALS_MODULE = "plugins.portfolio_plugin.bake.job_signals"
COMPOSE_MODULE = "plugins.portfolio_plugin.bake.compose_flow"
PERSIST_MODULE = "plugins.portfolio_plugin.bake.persist"

_HERO_LAYOUT = {
    "version": 1,
    "meta": {"audience": "recruiter", "generatedAt": "t"},
    "blocks": [{"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}}],
}


def valid_bake_layout() -> dict:
    """A layout that clears the bake quality contract.

    Six blocks / six types / six DAG bands, all db-grounded so no source refs
    are required. Anything thinner is correctly rejected, which is why the
    hero-only fixture above is only usable where the gate is bypassed.
    """
    return {
        "version": 1,
        "meta": {"audience": "recruiter", "generatedAt": "2026-01-01T00:00:00Z"},
        "blocks": [
            {"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}},
            {"type": "kpiGrid", "id": "kpi-1", "props": {"items": [{"label": "x", "value": "1"}]}},
            {
                "type": "card",
                "id": "card-oct",
                "props": {"title": "Whiskers Agent", "body": "b", "tags": ["ai", "mcp"]},
            },
            {"type": "flowAnim", "id": "fa1", "props": {"title": "Flow", "nodes": [], "edges": []}},
            {
                "type": "starStory",
                "id": "star-1",
                "props": {"situation": "s", "task": "t", "action": "a", "result": "r"},
            },
            {
                "type": "quickActions",
                "id": "qa1",
                "props": {"actions": [{"label": "Ask", "prompt": "p"}]},
            },
        ],
    }


def _agent_result(**overrides) -> dict:
    """A run_layout_agent success envelope; override to model degradation."""
    base = {
        "status": "ok",
        "layout": valid_bake_layout(),
        "plan": {"steps": [{"id": "h1", "block_type": "hero"}]},
        "mode": "layout_plan",
        "structure_mode": "free",
        "plan_source": "agent",
        "recipe_id": "r1",
        "jury": {"composite": 8.2},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_bake_portfolio_for_job_success():
    fake_layout = {
        "version": 1,
        "meta": {"audience": "recruiter", "generatedAt": "t"},
        "blocks": [],
    }

    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text)),
        patch(f"{MODULE}._compose_job_layout", new=AsyncMock(return_value=(fake_layout, "recruiter", "measurable impact", []))),
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock(return_value={"short_id": "whiskers_successor_992"})) as mock_create,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
        # Isolate from live SETTINGS.fish_tank_enabled (on by default).
        patch(
            "plugins.portfolio_plugin.plugin_config.SETTINGS",
            {"fish_tank_enabled": False},
        ),
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="We need measurable impact and results delivery from this hire.",
            company="Whiskers Agent",
            role="Successor",
            job_application_job_id="job-123",
        )

    assert res["status"] == "ok"
    assert res["audience"] == "recruiter"
    assert res["short_id"].startswith("whiskers_agent_successor_")
    assert res["query_param"] == f"j={res['short_id']}"
    assert res["plugin"] == "portfolio_plugin"

    mock_create.assert_awaited_once()
    _, create_kwargs = mock_create.call_args
    assert create_kwargs["layout"] == fake_layout
    assert create_kwargs["job_application_job_id"] == "job-123"
    assert create_kwargs["tenant_id"] == 1


@pytest.mark.asyncio
async def test_bake_portfolio_for_job_retries_on_collision():
    fake_layout = {"version": 1, "meta": {"audience": "default"}, "blocks": []}
    exists_calls = []

    async def fake_exists(candidate: str) -> bool:
        exists_calls.append(candidate)
        return len(exists_calls) < 2  # first candidate collides, second is free

    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text)),
        patch(f"{MODULE}._compose_job_layout", new=AsyncMock(return_value=(fake_layout, "default", "q", []))),
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=fake_exists),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock(return_value={})) as mock_create,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="Generic job posting with no strong signals.",
            company="Acme",
            role="Engineer",
        )

    assert res["status"] == "ok"
    assert len(exists_calls) == 2
    mock_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_bake_portfolio_for_job_exhausts_attempts():
    fake_layout = {"version": 1, "meta": {"audience": "default"}, "blocks": []}

    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text)),
        patch(f"{MODULE}._compose_job_layout", new=AsyncMock(return_value=(fake_layout, "default", "q", []))),
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=True)),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock()) as mock_create,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="Generic job posting.",
            company="Acme",
            role="Engineer",
        )

    assert res["status"] == "error"
    assert res["error"] == "short_id_collision"
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_bake_requires_company_role():
    res = await bake_portfolio_for_job(
        job_description="something",
        company="",
        role="Engineer",
    )
    assert res["status"] == "error"
    assert "company" in res.get("missing_fields", [])


@pytest.mark.asyncio
async def test_unresolved_posting_still_records_bake_run():
    """Every attempt writes a row — including the empty-resolve early return."""
    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(return_value="")),
        patch(f"{MODULE}.record_bake_run", new=AsyncMock()) as mock_run,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="",
            company="Acme",
            role="Engineer",
        )

    assert res["status"] == "error"
    assert res["error"] == "missing_required_fields"
    assert "job_description" in res.get("missing_fields", [])
    assert res["run_id"]
    mock_run.assert_awaited_once()
    assert mock_run.call_args.kwargs["status"] == "error"
    assert mock_run.call_args.kwargs.get("degraded") is True
    assert mock_run.call_args.kwargs.get("short_id") is None


@pytest.mark.asyncio
async def test_list_bake_runs_requires_tenant():
    with patch(
        "plugins.portfolio_plugin.tenant.require_tenant_id",
        return_value=None,
    ):
        res = await list_bake_runs()
    assert res["status"] == "error"
    assert res["error"] == "tenant_unresolved"


@pytest.mark.asyncio
async def test_list_bake_runs_returns_tenant_rows():
    rows = [{"run_id": "abc", "status": "error", "short_id": None}]
    with (
        patch(
            "plugins.portfolio_plugin.tenant.require_tenant_id",
            return_value=7,
        ),
        patch(
            "plugins.portfolio_plugin.store.list_bake_runs",
            new=AsyncMock(return_value=rows),
        ) as mock_list,
    ):
        res = await list_bake_runs(limit=10, degraded_only=True)

    assert res["status"] == "ok"
    assert res["runs"] == rows
    assert res["count"] == 1
    mock_list.assert_awaited_once_with(tenant_id=7, limit=10, degraded_only=True)


@pytest.mark.asyncio
async def test_bake_fish_tank_flag_off_skips_append():
    """When fish_tank_enabled is false, baked layouts must not gain a fishTank."""
    fake_layout = {
        "version": 1,
        "meta": {"audience": "recruiter", "generatedAt": "t"},
        "blocks": [{"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}}],
    }
    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text)),
        patch(f"{MODULE}._compose_job_layout", new=AsyncMock(return_value=(fake_layout, "recruiter", "q", []))),
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock(return_value={"short_id": "x_y_1"})) as mock_create,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
        patch(
            "plugins.portfolio_plugin.plugin_config.SETTINGS",
            {"fish_tank_enabled": False},
        ),
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="Platform engineer role with agentic systems.",
            company="Acme",
            role="Engineer",
        )
    assert res["status"] == "ok"
    layout = mock_create.call_args.kwargs.get("layout") or mock_create.call_args[1].get("layout")
    types = {b.get("type") for b in (layout.get("blocks") or [])}
    assert "fishTank" not in types


@pytest.mark.asyncio
async def test_bake_fish_tank_flag_on_appends_and_restamps_dag():
    fake_layout = {
        "version": 1,
        "meta": {"audience": "recruiter", "generatedAt": "t", "dag": {"levels": []}},
        "blocks": [{"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}}],
    }
    tank = {
        "type": "fishTank",
        "id": "fish-tank-1",
        "props": {
            "renderer": "webgl",
            "fish": [
                {
                    "slug": "oct",
                    "title": "Whiskers Agent",
                    "species": "ai",
                    "size": 0.8,
                    "depth": 0.1,
                    "speed": 0.5,
                    "glow": 0.9,
                    "school": 0,
                }
            ],
            "highlightSlugs": [],
        },
    }
    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text)),
        patch(f"{MODULE}._compose_job_layout", new=AsyncMock(return_value=(fake_layout, "recruiter", "q", []))),
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock(return_value={"short_id": "x_y_2"})) as mock_create,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
        patch(
            "plugins.portfolio_plugin.plugin_config.SETTINGS",
            {"fish_tank_enabled": True},
        ),
        patch(
            "plugins.portfolio_plugin.store.list_projects",
            new=AsyncMock(return_value=[{"slug": "oct", "name": "Whiskers Agent", "summary": "x" * 80, "tags": ["primary"]}]),
        ),
        patch(
            "plugins.portfolio_plugin.compose.fish.build_fish_tank_block",
            return_value=tank,
        ),
        patch(
            "plugins.portfolio_plugin.compose.dag.stamp_dag_from_blocks",
            return_value={"levels": [{"level": 3, "label": "Architecture", "nodes": ["fish-tank-1"]}]},
        ) as mock_dag,
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="Platform engineer role with agentic systems.",
            company="Acme",
            role="Engineer",
        )
    assert res["status"] == "ok"
    layout = mock_create.call_args.kwargs.get("layout") or mock_create.call_args[1].get("layout")
    types = [b.get("type") for b in (layout.get("blocks") or [])]
    assert "fishTank" in types
    assert layout["meta"]["dag"]["levels"][0]["nodes"] == ["fish-tank-1"]
    mock_dag.assert_called()


@pytest.mark.asyncio
async def test_bake_fish_tank_builder_error_fail_open():
    fake_layout = {
        "version": 1,
        "meta": {"audience": "recruiter", "generatedAt": "t"},
        "blocks": [{"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}}],
    }
    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text)),
        patch(f"{MODULE}._compose_job_layout", new=AsyncMock(return_value=(fake_layout, "recruiter", "q", []))),
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock(return_value={"short_id": "x_y_3"})) as mock_create,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
        patch(
            "plugins.portfolio_plugin.plugin_config.SETTINGS",
            {"fish_tank_enabled": True},
        ),
        patch(
            "plugins.portfolio_plugin.store.list_projects",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="Platform engineer role.",
            company="Acme",
            role="Engineer",
        )
    assert res["status"] == "ok"
    layout = mock_create.call_args.kwargs.get("layout") or mock_create.call_args[1].get("layout")
    assert "fishTank" not in {b.get("type") for b in (layout.get("blocks") or [])}


@pytest.mark.asyncio
async def test_bake_prebuilt_layout_passthrough():
    """Specialist draft layout is persisted without re-thinning."""
    prebuilt = {
        "version": 1,
        "meta": {"audience": "peer", "mode": "agentic", "generatedAt": "t"},
        "blocks": [
            {"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}},
            {"type": "kpiGrid", "id": "k1", "props": {"items": [{"label": "x", "value": "1"}]}},
            {"type": "mcpSandbox", "id": "mcp-sandbox", "props": {}},
            {"type": "costSim", "id": "cost-sim", "props": {}},
            {"type": "quickActions", "id": "cta", "props": {
                "prompt": "Ask",
                "actions": [{"label": "a", "prompt": "b"}],
            }},
        ],
    }

    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text or "jd")),
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock(return_value={})) as mock_create,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="Platform engineer with MCP and cloud cost work.",
            company="Acme",
            role="Platform Engineer",
            layout=prebuilt,
        )

    assert res["status"] == "ok"
    types = [b.get("type") for b in (res.get("layout") or {}).get("blocks") or []]
    assert "mcpSandbox" in types
    assert "costSim" in types
    assert (res.get("layout") or {}).get("meta", {}).get("composePath") == "prebuilt_draft"
    mock_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_compose_job_layout_prefers_agentic_not_default_scoped():
    """Agentic path is tried first; thin compose_scoped default is not the happy path."""
    from plugins.portfolio_plugin.MCPTools.bake_tools import _compose_job_layout

    agentic_layout = {
        "version": 1,
        "meta": {"audience": "peer", "mode": "layout_plan"},
        "blocks": [
            {"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}},
            {"type": "card", "id": "c1", "props": {"title": "P"}},
            {"type": "mcpSandbox", "id": "mcp-sandbox", "props": {}},
            {"type": "quickActions", "id": "cta", "props": {
                "prompt": "Ask",
                "actions": [{"label": "a", "prompt": "b"}],
            }},
        ],
    }

    scoped = AsyncMock(return_value={
        "status": "ok",
        "layout": {
            "version": 1,
            "meta": {"mode": "scoped"},
            "blocks": [{"type": "hero", "id": "h1", "props": {"name": "X", "tagline": "y"}}],
        },
        "audience": "peer",
        "star_query": "q",
    })

    with (
        patch(
            "plugins.portfolio_plugin.layout.layout_config.use_agentic_layout",
            return_value=True,
        ),
        patch(
            "plugins.portfolio_plugin.layout.layout_config.get_portfolio_layout_config",
            return_value={"mode": "auto", "agentic_goal_classes": ["bake_for_job"]},
        ),
        patch(
            "plugins.portfolio_plugin.agents.layout_agent.run_layout_agent",
            new=AsyncMock(return_value={
                "status": "ok",
                "layout": agentic_layout,
                "mode": "layout_plan",
                "recipe_id": "job-bake",
                "jury": {"composite": 8.0, "passed": True},
            }),
        ),
        patch(
            "plugins.portfolio_plugin.compose.compose_scoped.compose_scoped_layout",
            new=scoped,
        ),
    ):
        layout, aud, sq, errs = await _compose_job_layout(
            resolved_text="Senior platform engineer MCP cloud cost optimization",
            company="Acme",
            role="Platform Engineer",
            tenant_id=1,
            theme="neon",
        )

    scoped.assert_not_awaited()
    assert layout is not None
    types = [b.get("type") for b in layout.get("blocks") or []]
    assert "mcpSandbox" in types
    assert layout.get("meta", {}).get("composePath") == "agentic_layout_agent"


@pytest.mark.asyncio
async def test_bake_fails_closed_without_tenant_context():
    """No principal tenant → error; never silent tenant-1 bake."""
    with (
        patch(f"{MODULE}.create_job_layout", new=AsyncMock()) as mock_create,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
    ):
        mock_tid.get.return_value = None
        res = await bake_portfolio_for_job(
            job_description="We need measurable impact.",
            company="Acme",
            role="Engineer",
        )
    assert res["status"] == "error"
    assert res["error"] == "missing_tenant_context"
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_bake_uses_principal_tenant_only():
    """Layout is stamped with current_tenant_id, not a caller-supplied override."""
    fake_layout = {"version": 1, "meta": {"audience": "default"}, "blocks": []}
    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text)),
        patch(f"{MODULE}._compose_job_layout", new=AsyncMock(return_value=(fake_layout, "default", "q", []))) as mock_compose,
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock(return_value={})) as mock_create,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
    ):
        mock_tid.get.return_value = 42
        res = await bake_portfolio_for_job(
            job_description="Generic job posting.",
            company="Acme",
            role="Engineer",
        )
    assert res["status"] == "ok"
    assert mock_compose.await_args.kwargs["tenant_id"] == 42
    assert mock_create.await_args.kwargs["tenant_id"] == 42


@pytest.mark.asyncio
async def test_resolve_job_posting_prefers_fetch_url_body():
    """Sibling plugins are reached through the catalog, not imported."""
    stored = "short stored blurb"

    async def _dispatch(plugin_id, operation_id, args):
        assert plugin_id == "search_plugin"
        if operation_id == "fetch_url":
            return {
                "status": "ok",
                "content": "We need ownership tradeoffs leadership delivery architecture systems.",
            }
        return {"status": "error"}

    with patch(f"{SIGNALS_MODULE}._call_plugin_op", new=_dispatch):
        text = await resolve_job_posting_text(
            stored,
            posting_url="https://example.com/jobs/1",
            company="",
        )
    assert "ownership tradeoffs" in text


@pytest.mark.asyncio
async def test_resolve_job_posting_falls_back_on_fetch_error():
    """A failed dispatch stays fail-open: the stored description survives."""
    stored = "measurable impact results delivery"
    with patch(
        f"{SIGNALS_MODULE}._call_plugin_op",
        new_callable=AsyncMock,
        side_effect=RuntimeError("network down"),
    ):
        text = await resolve_job_posting_text(
            stored,
            posting_url="https://example.com/jobs/1",
            company="",
        )
    assert text == stored


@pytest.mark.asyncio
async def test_resolve_job_posting_records_fetch_error_code():
    """The BakeContext error code must survive the move to execute_operation."""
    from plugins.portfolio_plugin.bake.run_context import BakeContext, BakeErrorCode

    ctx = BakeContext()
    with patch(
        f"{SIGNALS_MODULE}._call_plugin_op",
        new_callable=AsyncMock,
        side_effect=RuntimeError("network down"),
    ):
        await resolve_job_posting_text(
            "stored",
            posting_url="https://example.com/jobs/1",
            company="",
            ctx=ctx,
        )
    codes = {e.code for e in ctx.errors}
    assert BakeErrorCode.RESOLVE_FETCH_FAILED in codes


# --- Ladder attribution / observability (PR1) --------------------------------


@pytest.mark.asyncio
async def test_compose_ladder_records_each_declined_rung():
    """The degraded ladder was untested: every rung fails open, so without
    attributed errors a floor/template layout is indistinguishable from a clean
    agentic one."""
    with (
        patch(
            "plugins.portfolio_plugin.layout.layout_config.use_agentic_layout",
            return_value=True,
        ),
        patch(
            "plugins.portfolio_plugin.layout.evidence_pack.build_evidence_pack",
            new=AsyncMock(side_effect=RuntimeError("pack down")),
        ),
        patch(
            "plugins.portfolio_plugin.agents.layout_agent.run_layout_agent",
            new=AsyncMock(side_effect=RuntimeError("agent down")),
        ),
        patch(
            "plugins.portfolio_plugin.compose.floor.build_floor_layout",
            new=AsyncMock(side_effect=RuntimeError("floor down")),
        ),
        patch(f"{COMPOSE_MODULE}.compose_layout", new=AsyncMock(return_value={**_HERO_LAYOUT})),
    ):
        layout, _audience, _star_query, errors = await _compose_job_layout(
            resolved_text="Platform engineer building agentic systems.",
            company="Acme",
            role="Engineer",
            tenant_id=1,
        )

    assert layout is not None, "template rung should still produce a layout"
    codes = [e["code"] for e in errors]
    assert "evidence_pack_failed" in codes
    assert "agentic_failed" in codes
    # The floor rung is attempted exactly once on the agentic path — the
    # unconditional floor_fast retry is skipped once agentic's own floor
    # attempt already failed (see _compose_job_layout's `else:` branch),
    # since repeating the identical call would only reproduce the same error.
    assert {e["stage"] for e in errors if e["stage"].startswith("floor")} == {"floor"}
    assert codes.count("floor_failed") == 1
    assert all(e["message"] for e in errors), "a cause without a message is useless"


@pytest.mark.asyncio
async def test_failed_bake_records_run_with_null_short_id():
    """A bake that ships nothing must still leave a durable trace."""
    compose_errors = [{"stage": "template", "code": "template_failed", "message": "boom"}]
    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text)),
        patch(
            f"{MODULE}._compose_job_layout",
            new=AsyncMock(return_value=(None, "recruiter", "q", compose_errors)),
        ),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock()) as mock_create,
        patch(f"{MODULE}.record_bake_run", new=AsyncMock()) as mock_run,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="Platform engineer role.",
            company="Acme",
            role="Engineer",
        )

    assert res["status"] == "error"
    assert res["error"] == "layout_compose_failed"
    assert res["run_id"]
    # The generic "layout composition failed" placeholder is gone.
    assert res["errors"] == compose_errors
    mock_create.assert_not_awaited()
    mock_run.assert_awaited_once()
    assert mock_run.call_args.kwargs["status"] == "error"
    assert mock_run.call_args.kwargs.get("short_id") is None


@pytest.mark.asyncio
async def test_successful_bake_persists_plan_and_compose_path():
    """plan_json was always NULL; compose_path/mode/degraded lived only in JSONB."""
    agent_res = _agent_result()
    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text)),
        patch(
            "plugins.portfolio_plugin.layout.layout_config.use_agentic_layout",
            return_value=True,
        ),
        patch(
            "plugins.portfolio_plugin.layout.evidence_pack.build_evidence_pack",
            new=AsyncMock(return_value={"pack_hash": "abc123", "inventory": {}}),
        ),
        patch(
            "plugins.portfolio_plugin.agents.layout_agent.run_layout_agent",
            new=AsyncMock(return_value=agent_res),
        ),
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock(return_value={"short_id": "a_b_1"})) as mock_create,
        patch(f"{MODULE}.record_bake_run", new=AsyncMock()) as mock_run,
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="Agentic platform engineer building LLM systems.",
            company="Acme",
            role="AI Engineer",
        )

    assert res["status"] == "ok"
    assert res["degraded"] is False
    kwargs = mock_create.call_args.kwargs
    assert kwargs["plan_json"] == agent_res["plan"]
    assert kwargs["compose_path"] == "context_first_layout_agent"
    assert kwargs["degraded"] is False
    assert mock_run.call_args.kwargs["job_brief_hash"], "run row needs the brief hash"
    assert mock_run.call_args.kwargs["short_id"] == res["short_id"]


@pytest.mark.asyncio
async def test_ship_best_layout_is_flagged_as_degraded():
    """ship_best means the jury never passed. Shipping it silently as 'ok' is
    the defect; it must surface as a cause and mark the row degraded."""
    agent_res = _agent_result(
        ship_best=True,
        jury={"composite": 4.1},
        jury_history=[{}, {}, {}],
    )
    with (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda text, **k: text)),
        patch(
            "plugins.portfolio_plugin.layout.layout_config.use_agentic_layout",
            return_value=True,
        ),
        patch(
            "plugins.portfolio_plugin.layout.evidence_pack.build_evidence_pack",
            new=AsyncMock(return_value={"pack_hash": "abc123", "inventory": {}}),
        ),
        patch(
            "plugins.portfolio_plugin.agents.layout_agent.run_layout_agent",
            new=AsyncMock(return_value=agent_res),
        ),
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)),
        patch(f"{MODULE}.create_job_layout", new=AsyncMock(return_value={"short_id": "a_b_2"})) as mock_create,
        patch(f"{MODULE}.record_bake_run", new=AsyncMock()),
        patch(f"{MODULE}.current_tenant_id") as mock_tid,
    ):
        mock_tid.get.return_value = 1
        res = await bake_portfolio_for_job(
            job_description="Agentic platform engineer building LLM systems.",
            company="Acme",
            role="AI Engineer",
        )

    assert res["status"] == "ok"
    assert res["degraded"] is True
    assert "agentic_jury_never_passed" in [e["code"] for e in res["errors"]]
    assert mock_create.call_args.kwargs["degraded"] is True
