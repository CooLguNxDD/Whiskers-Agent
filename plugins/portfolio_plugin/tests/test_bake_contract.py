"""The shared bake quality contract, and the hard-fail gate it drives.

The defect this replaces: the MCP tool gated not at all, the bake agent
post-gated and downgraded to "partial", and ``ship_best`` forced status="ok"
onto a layout the jury had rejected every round. Whether a page was "good
enough to send a recruiter" depended on which door you came through.
"""

from __future__ import annotations

from contextlib import ExitStack

import pytest
from unittest.mock import AsyncMock, patch

from plugins.portfolio_plugin.bake.contract import (
    assess_bake_quality,
    structural_quality,
)
from plugins.portfolio_plugin.compose.recipes import layout_quality_ok
from plugins.portfolio_plugin.MCPTools.bake_tools import bake_portfolio_for_job
from plugins.portfolio_plugin.tests.test_portfolio_bake import valid_bake_layout

MODULE = "plugins.portfolio_plugin.MCPTools.bake_tools"
# Short-id allocation split out of bake_tools.
PERSIST_MODULE = "plugins.portfolio_plugin.bake.persist"


def test_valid_layout_passes_cleanly():
    report = assess_bake_quality(valid_bake_layout(), goal_class="bake_for_job")
    assert report.passed is True
    assert report.violations == []
    assert report.dimensions["band_coverage"] >= 3


@pytest.mark.parametrize(
    "mutate, expected_code",
    [
        (lambda lay: None, "layout_missing"),
        (lambda lay: {**lay, "blocks": []}, "blocks_empty"),
        (lambda lay: {**lay, "blocks": lay["blocks"][:2]}, "block_count_lt"),
        (
            lambda lay: {**lay, "meta": {**lay["meta"], "mode": "template"}},
            "flat_template_fallback",
        ),
        (
            # Six hero blocks: count is fine, type diversity and bands are not.
            lambda lay: {
                **lay,
                "blocks": [
                    {"type": "hero", "id": f"h{i}", "props": {"name": "A", "tagline": "t"}}
                    for i in range(6)
                ],
            },
            "block_types_lt",
        ),
    ],
)
def test_blocking_violations(mutate, expected_code):
    report = assess_bake_quality(mutate(valid_bake_layout()), goal_class="bake_for_job")
    assert report.passed is False
    assert expected_code in report.codes()


def test_unknown_block_type_is_reported_not_silently_dropped():
    """validate_layout filters unknown types instead of rejecting them, so a
    hallucinated block used to vanish with no trace."""
    lay = valid_bake_layout()
    lay["blocks"].append({"type": "totallyMadeUp", "id": "x1", "props": {}})
    report = assess_bake_quality(lay, goal_class="bake_for_job")
    assert "unknown_block_dropped" in report.codes()
    # Warn-only: a dropped block is a defect, not a reason to withhold the page.
    assert report.passed is True


def test_authored_block_without_source_refs_is_ungrounded():
    """Reported, but warn-only: _sourceRefs is popped before the gate sees the
    layout, so treating this as blocking would reject every real bake."""
    lay = valid_bake_layout()
    lay["blocks"].append({"type": "prose", "id": "prose-oct", "props": {"markdown": "hi"}})
    report = assess_bake_quality(lay, goal_class="bake_for_job")
    assert "ungrounded_block" in report.codes()
    assert report.dimensions["ungrounded_blocks"] == 1
    assert report.passed is True

    lay["blocks"][-1]["_sourceRefs"] = ["github:CooLguNxDD/OpenCat-Mcp-Full"]
    clean = assess_bake_quality(lay, goal_class="bake_for_job")
    assert "ungrounded_block" not in clean.codes()


def test_ship_best_fails_the_contract():
    """A best loser must not read as an approved page."""
    agent_result = {"ship_best": True, "jury_history": [{}, {}, {}], "jury": {"composite": 4.1}}
    report = assess_bake_quality(
        valid_bake_layout(), goal_class="bake_for_job", agent_result=agent_result
    )
    assert report.passed is False
    assert "jury_never_passed" in report.codes()
    assert "jury_below_threshold" in report.codes()
    assert report.score == 4.1


def test_jury_above_threshold_passes():
    report = assess_bake_quality(
        valid_bake_layout(),
        goal_class="bake_for_job",
        agent_result={"jury": {"composite": 8.4}},
        cfg={"jury_threshold": 7.5},
    )
    assert report.passed is True


def test_legacy_shim_is_structural_only():
    """layout_quality_ok backs callers that ask 'is this substantial enough',
    not 'is this fit to send' — it must not inherit schema/grounding checks."""
    bare = {
        "blocks": [
            {"type": "hero"},
            {"type": "kpiGrid"},
            {"type": "flowAnim"},
            {"type": "chart"},
            {"type": "starStory"},
            {"type": "quickActions"},
        ]
    }
    ok, errs = layout_quality_ok(bare, goal_class="bake_for_job")
    assert ok is True and errs == []
    # Same input through the full contract is stricter (no version/meta/ids).
    assert assess_bake_quality(bare, goal_class="bake_for_job").passed is False


def test_legacy_shim_preserves_error_string_format():
    ok, errs = structural_quality({"blocks": [{"type": "hero"}]}, goal_class="bake_for_job")
    assert ok is False
    assert "block_count_1_lt_6" in errs


# --- gate wiring -------------------------------------------------------------


_THIN_LAYOUT = {
    "version": 1,
    "meta": {"audience": "recruiter", "generatedAt": "t"},
    "blocks": [{"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}}],
}


def _enter_bake_patches(stack, create_mock, run_mock, agent_result, *, hard_fail):
    """Drive a real bake down the agentic rung with a known layout + flag."""
    for ctx in (
        patch(f"{MODULE}.resolve_job_posting_text", new=AsyncMock(side_effect=lambda t, **k: t)),
        patch(
            "plugins.portfolio_plugin.layout.layout_config.use_agentic_layout",
            return_value=True,
        ),
        patch(
            "plugins.portfolio_plugin.layout.evidence_pack.build_evidence_pack",
            new=AsyncMock(return_value={"pack_hash": "h", "inventory": {}}),
        ),
        patch(
            "plugins.portfolio_plugin.agents.layout_agent.run_layout_agent",
            new=AsyncMock(return_value=agent_result),
        ),
        patch(f"{PERSIST_MODULE}.job_layout_short_id_exists", new=AsyncMock(return_value=False)),
        patch(f"{MODULE}.create_job_layout", new=create_mock),
        patch(f"{MODULE}.record_bake_run", new=run_mock),
        patch(
            "plugins.portfolio_plugin.layout.layout_config.get_portfolio_layout_config",
            return_value={"hard_fail_on_quality": hard_fail, "jury_threshold": 7.5},
        ),
    ):
        stack.enter_context(ctx)
    tid = stack.enter_context(patch(f"{MODULE}.current_tenant_id"))
    tid.get.return_value = 1


@pytest.mark.asyncio
async def test_hard_fail_persists_nothing_but_still_records_the_run():
    create_mock, run_mock = AsyncMock(), AsyncMock()
    agent_result = {"status": "ok", "layout": dict(_THIN_LAYOUT), "structure_mode": "free"}
    with ExitStack() as stack:
        _enter_bake_patches(stack, create_mock, run_mock, agent_result, hard_fail=True)
        res = await bake_portfolio_for_job(
            job_description="Platform engineer building agentic systems.",
            company="Acme",
            role="Engineer",
        )

    assert res["status"] == "error"
    assert res["error"] == "quality_gate_failed"
    assert "block_count_lt" in [v["code"] for v in res["quality"]["violations"]]
    # No row, no short_id, no public URL for a page that failed the bar.
    create_mock.assert_not_awaited()
    assert "short_id" not in res
    # But the attempt is still durable.
    run_mock.assert_awaited_once()
    assert run_mock.call_args.kwargs["status"] == "error"
    assert run_mock.call_args.kwargs.get("short_id") is None


@pytest.mark.asyncio
async def test_soak_mode_ships_but_marks_degraded():
    """Flag off is the shipping default: still persist, but never silently."""
    create_mock, run_mock = AsyncMock(return_value={"short_id": "a_b_9"}), AsyncMock()
    agent_result = {"status": "ok", "layout": dict(_THIN_LAYOUT), "structure_mode": "free"}
    with ExitStack() as stack:
        _enter_bake_patches(stack, create_mock, run_mock, agent_result, hard_fail=False)
        res = await bake_portfolio_for_job(
            job_description="Platform engineer building agentic systems.",
            company="Acme",
            role="Engineer",
        )

    assert res["status"] == "ok"
    assert res["degraded"] is True
    assert res["quality"]["passed"] is False
    create_mock.assert_awaited_once()
    assert create_mock.call_args.kwargs["degraded"] is True


@pytest.mark.asyncio
async def test_mcp_and_agent_paths_share_one_verdict():
    """The parity that justifies a shared contract: run_bake_agent must not
    apply a second, different bar on top of the tool's."""
    from plugins.portfolio_plugin.agents.bake import run_bake_agent

    tool_result = {
        "status": "ok",
        "short_id": "a_b_7",
        "layout": {
            "version": 1,
            "meta": {"audience": "recruiter"},
            "blocks": [{"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}}],
        },
        "quality": {"passed": False, "violations": [{"code": "block_count_lt"}]},
        "degraded": True,
    }
    with patch(
        "plugins.portfolio_plugin.MCPTools.bake_tools.bake_portfolio_for_job",
        new=AsyncMock(return_value=tool_result),
    ):
        agent_res = await run_bake_agent(company="Acme", role="Engineer", job_description="x")

    # Verdict passes through untouched — no downgrade to "partial", no second
    # quality_errors key computed from a different rule set.
    assert agent_res["status"] == tool_result["status"]
    assert agent_res["quality"] == tool_result["quality"]
    assert "quality_errors" not in agent_res


@pytest.mark.asyncio
async def test_bake_agent_refuses_to_invent_company_and_role():
    """It used to manufacture 'Whiskers Agent Demo' / 'Engineer', which the MCP tool
    then rejected — a bake for a job nobody applied to, or a confusing error."""
    from plugins.portfolio_plugin.agents.bake import run_bake_agent

    res = await run_bake_agent(job_description="some posting text")
    assert res["status"] == "error"
    assert res["error"] == "missing_job_signals"
    assert set(res["missing_fields"]) == {"company", "role"}
