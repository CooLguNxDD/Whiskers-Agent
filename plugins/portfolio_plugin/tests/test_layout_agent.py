"""Phase 3 — layout agent + composer dual-path unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core_graph.agent_loop.runner import AgentRunResult, run_agent
from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config, use_agentic_layout


def test_use_agentic_layout_auto_mode():
    assert use_agentic_layout("redesign", {"mode": "auto", "agentic_goal_classes": ["redesign", "bake_for_job"]})
    assert use_agentic_layout("bake_for_job", {"mode": "auto", "agentic_goal_classes": ["redesign", "bake_for_job"]})
    assert not use_agentic_layout("scoped_ask", {"mode": "auto", "agentic_goal_classes": ["redesign"]})
    assert not use_agentic_layout("redesign", {"mode": "fast", "agentic_goal_classes": ["redesign"]})
    assert use_agentic_layout("scoped_ask", {"mode": "agentic", "agentic_goal_classes": []})


def test_portfolio_layout_config_defaults():
    cfg = get_portfolio_layout_config()
    assert cfg["mode"] in ("auto", "agentic", "fast")
    assert "jury_threshold" in cfg
    assert cfg["max_plan_rounds"] >= 1
    # Prompt budget keys must not be silently dropped from _DEFAULTS.
    assert "evidence_max_chars" in cfg
    assert "skill_max_chars" in cfg
    assert "block_catalog_max_chars" in cfg
    assert "context_enrich_interactive" in cfg
    # Sourced from plugin SETTINGS (manifest), not server_config graph
    from plugins.portfolio_plugin.plugin_config import SETTINGS

    plugin_raw = (SETTINGS or {}).get("portfolio_layout") or {}
    assert isinstance(plugin_raw, dict)
    assert cfg["mode"] == str(plugin_raw.get("mode") or "auto").lower()


def test_portfolio_layout_config_carries_layout_harness_defaults():
    """Regression for defect C: get_portfolio_layout_config's merge loop is
    `if k in _DEFAULTS`, so a manifest key not in _DEFAULTS is silently
    dropped -- same failure class as the themeOverrides allowlist bug.
    layout_harness must be present with dark-launch defaults even with no
    manifest override at all."""
    cfg = get_portfolio_layout_config()
    lh = cfg.get("layout_harness")
    assert isinstance(lh, dict)
    for key in ("enabled", "win_top_k", "loss_top_k", "similarity_threshold",
                "max_block_chars", "min_score_to_record", "write_wins",
                "write_losses", "seed_from_memory"):
        assert key in lh
    # shipped manifest.json ships these explicitly false (dark-launch)
    assert lh["write_wins"] is False
    assert lh["write_losses"] is False
    assert lh["seed_from_memory"] is False


def test_portfolio_layout_config_layout_harness_partial_override_merges():
    """A manifest override that only sets one layout_harness key must not
    wipe the rest of the sub-dict's defaults (nested merge, not overwrite)."""
    from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config as _get

    fake_settings = {"portfolio_layout": {"layout_harness": {"write_wins": True}}}
    with patch("plugins.portfolio_plugin.plugin_config.SETTINGS", fake_settings):
        cfg = _get()
    lh = cfg["layout_harness"]
    assert lh["write_wins"] is True
    assert lh["win_top_k"] == 3  # untouched default survived
    assert lh["enabled"] is True


def test_portfolio_layout_config_layout_harness_bad_types_coerced():
    from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config as _get

    fake_settings = {
        "portfolio_layout": {
            "layout_harness": {"win_top_k": "not-an-int", "similarity_threshold": "nope"}
        }
    }
    with patch("plugins.portfolio_plugin.plugin_config.SETTINGS", fake_settings):
        cfg = _get()
    lh = cfg["layout_harness"]
    assert lh["win_top_k"] == 3  # falls back to default rather than raising
    assert lh["similarity_threshold"] == 0.78


@pytest.mark.asyncio
async def test_run_layout_agent_seed_materialize_no_llm():
    from plugins.portfolio_plugin.agents.layout_agent import run_layout_agent

    fake_layout = {
        "version": 1,
        "meta": {
            "audience": "peer",
            "generatedAt": "2020-01-01T00:00:00Z",
            "mode": "scoped",
            "theme": "neon",
            "sources": [{"ref": "github:x/y"}],
            "dag": {"levels": [{"level": 0, "nodes": ["h1"]}]},
        },
        "blocks": [
            {"type": "hero", "id": "h1", "props": {"title": "T", "subtitle": "S"}},
            {"type": "card", "id": "c1", "props": {"title": "P", "body": "b"}},
            {"type": "kpiGrid", "id": "k1", "props": {"items": []}},
            {"type": "starStory", "id": "s1", "props": {"situation": "S", "task": "T", "action": "A", "result": "R"}},
            {"type": "archDiagram", "id": "a1", "props": {"title": "A", "kind": "mermaid", "source": "graph TD;A-->B"}},
        ],
    }

    with patch(
        "core.llm_provider_management.llm_available",
        return_value=False,
    ), patch(
        "plugins.portfolio_plugin.layout.layout_plan.materialize_layout_plan",
        new_callable=AsyncMock,
        return_value={"status": "ok", "layout": fake_layout, "audience": "peer", "mode": "scoped"},
    ), patch(
        "plugins.portfolio_plugin.layout.layout_jury.critique_layout",
        new_callable=AsyncMock,
        return_value={
            "status": "ok",
            "composite": 8.5,
            "passed": True,
            "must_fix": [],
            "threshold": 7.5,
            "dimensions": {},
        },
    ):
        res = await run_layout_agent(
            query="redesign portfolio for platform engineer",
            goal_class="redesign",
            theme="neon",
            tenant_id=1,
        )
    assert res["status"] == "ok"
    assert res["engine"] == "layout_agent"
    assert isinstance(res.get("layout"), dict)
    assert res.get("recipe_id")
    assert res.get("direction")


@pytest.mark.asyncio
async def test_composer_uses_layout_agent_when_agentic():
    from plugins.portfolio_plugin.agents.composer import run_composer_agent

    with patch(
        "plugins.portfolio_plugin.layout.layout_config.use_agentic_layout",
        return_value=True,
    ), patch(
        "plugins.portfolio_plugin.agents.layout_agent.run_layout_agent",
        new_callable=AsyncMock,
        return_value={
            "status": "ok",
            "layout": {"version": 1, "meta": {}, "blocks": [{"type": "hero", "id": "h1", "props": {}}]},
            "engine": "layout_agent",
            "goal_class": "redesign",
        },
    ) as mock_agent:
        res = await run_composer_agent(
            query="redesign my portfolio",
            goal_class="redesign",
            tenant_id=1,
        )
    mock_agent.assert_awaited_once()
    assert res["engine"] == "layout_agent"
    assert res["layout"]["blocks"]


@pytest.mark.asyncio
async def test_composer_fast_path_scoped_skips_agent():
    from plugins.portfolio_plugin.agents.composer import run_composer_agent

    with patch(
        "plugins.portfolio_plugin.layout.layout_config.use_agentic_layout",
        return_value=False,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_scoped_layout",
        new_callable=AsyncMock,
        return_value={
            "status": "ok",
            "layout": {"version": 1, "meta": {"mode": "scoped"}, "blocks": []},
            "audience": "default",
            "mode": "scoped",
        },
    ) as mock_compose, patch(
        "plugins.portfolio_plugin.agents.layout_agent.run_layout_agent",
        new_callable=AsyncMock,
    ) as mock_agent:
        res = await run_composer_agent(
            query="show only SRE work",
            goal_class="scoped_ask",
            tenant_id=1,
            fast_path=True,
        )
    mock_agent.assert_not_awaited()
    mock_compose.assert_awaited_once()
    assert res.get("engine") == "compose_scoped"


@pytest.mark.asyncio
async def test_resolve_plan_forwards_tenant_id():
    """Regression for the swallowed TypeError: run_agent requires tenant_id
    keyword-only with no default. autospec=True is load-bearing here — a
    bare AsyncMock accepts any call signature and would pass even if
    production code omitted tenant_id, exactly as it did before this fix."""
    from plugins.portfolio_plugin.agents.layout_agent import _resolve_plan

    fake_result = AgentRunResult(
        status="ok",
        output={"steps": [{"id": "h1", "block_type": "hero"}]},
        steps=1,
    )
    with patch(
        "core.llm_provider_management.llm_available", return_value=True
    ), patch(
        "core_graph.agent_loop.runner.run_agent", autospec=True
    ) as mock_run_agent:
        mock_run_agent.return_value = fake_result
        plan_dict, plan_source = await _resolve_plan(
            query="redesign for staff platform role",
            goal_class="redesign",
            theme="neon",
            recipe=None,
            system_prompt="sys",
            previous_errors=[],
            direction=None,
            theme_overrides=None,
            max_steps=5,
            max_seconds=30.0,
            tenant_id=7,
        )

    mock_run_agent.assert_awaited_once()
    _, kwargs = mock_run_agent.call_args
    assert kwargs.get("tenant_id") == 7
    assert plan_source == "agent"
    assert plan_dict["steps"]


@pytest.mark.asyncio
async def test_resolve_plan_reports_agent_source():
    from plugins.portfolio_plugin.agents.layout_agent import _resolve_plan

    fake_result = AgentRunResult(
        status="ok",
        output={"steps": [{"id": "h1", "block_type": "hero"}], "brief": "custom"},
        steps=2,
    )
    with patch(
        "core.llm_provider_management.llm_available", return_value=True
    ), patch(
        "core_graph.agent_loop.runner.run_agent", autospec=True, return_value=fake_result
    ):
        plan_dict, plan_source = await _resolve_plan(
            query="q",
            goal_class="redesign",
            theme="",
            recipe=None,
            system_prompt="sys",
            previous_errors=[],
            direction=None,
            theme_overrides=None,
            max_steps=5,
            max_seconds=30.0,
            tenant_id=1,
        )
    assert plan_source == "agent"
    assert plan_dict["brief"] == "custom"


@pytest.mark.asyncio
async def test_resolve_plan_falls_back_on_agent_error():
    from plugins.portfolio_plugin.agents.layout_agent import _resolve_plan

    with patch(
        "core.llm_provider_management.llm_available", return_value=True
    ), patch(
        "core_graph.agent_loop.runner.run_agent",
        autospec=True,
        side_effect=RuntimeError("boom"),
    ):
        plan_dict, plan_source = await _resolve_plan(
            query="redesign",
            goal_class="redesign",
            theme="neon",
            recipe=None,
            system_prompt="sys",
            previous_errors=[],
            direction=None,
            theme_overrides=None,
            max_steps=5,
            max_seconds=30.0,
            tenant_id=1,
        )
    assert plan_source == "seed"
    assert plan_dict["steps"]


@pytest.mark.asyncio
async def test_resolve_plan_no_llm_is_seed_without_calling_run_agent():
    from plugins.portfolio_plugin.agents.layout_agent import _resolve_plan

    with patch(
        "core.llm_provider_management.llm_available", return_value=False
    ), patch(
        "core_graph.agent_loop.runner.run_agent", autospec=True
    ) as mock_run_agent:
        plan_dict, plan_source = await _resolve_plan(
            query="redesign",
            goal_class="redesign",
            theme="neon",
            recipe=None,
            system_prompt="sys",
            previous_errors=[],
            direction=None,
            theme_overrides=None,
            max_steps=5,
            max_seconds=30.0,
            tenant_id=1,
        )
    mock_run_agent.assert_not_awaited()
    assert plan_source == "seed"
    assert plan_dict["steps"]


@pytest.mark.asyncio
async def test_second_round_prompt_contains_must_fix():
    """Direct regression for 'jury feedback can never change the plan': before
    the tenant_id fix, _resolve_plan always returned the seed, so round 2's
    prompt was never even built from must_fix. Now that run_agent is
    reachable, prove round 2's prompt actually carries round 1's must_fix."""
    from plugins.portfolio_plugin.agents.layout_agent import run_layout_agent

    fake_layout = {
        "version": 1,
        "meta": {"audience": "peer", "generatedAt": "2020-01-01T00:00:00Z", "mode": "scoped"},
        "blocks": [{"type": "hero", "id": "h1", "props": {"title": "T", "subtitle": "S"}}],
    }
    fake_result = AgentRunResult(
        status="ok",
        output={"steps": [{"id": "h1", "block_type": "hero"}]},
        steps=1,
    )
    jury_calls = [
        {
            "status": "ok",
            "composite": 5.0,
            "passed": False,
            "must_fix": ["structure: need at least 3 block types"],
            "threshold": 7.5,
            "dimensions": {},
        },
        {
            "status": "ok",
            "composite": 8.5,
            "passed": True,
            "must_fix": [],
            "threshold": 7.5,
            "dimensions": {},
        },
    ]

    with patch(
        "core.llm_provider_management.llm_available", return_value=True
    ), patch(
        "core_graph.agent_loop.runner.run_agent",
        autospec=True,
        return_value=fake_result,
    ) as mock_run_agent, patch(
        "plugins.portfolio_plugin.layout.layout_plan.materialize_layout_plan",
        new_callable=AsyncMock,
        return_value={"status": "ok", "layout": fake_layout, "audience": "peer", "mode": "scoped"},
    ), patch(
        "plugins.portfolio_plugin.layout.layout_jury.critique_layout",
        new_callable=AsyncMock,
        side_effect=jury_calls,
    ):
        res = await run_layout_agent(
            query="redesign portfolio",
            goal_class="redesign",
            theme="neon",
            tenant_id=1,
        )

    assert res["status"] == "ok"
    assert mock_run_agent.await_count == 2
    round2_prompt = mock_run_agent.call_args_list[1].args[1]
    assert "structure: need at least 3 block types" in round2_prompt
    assert res.get("plan_source") == "agent"
