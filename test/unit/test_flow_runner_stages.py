"""Unit tests for core_graph.subgraphs.specialist.flow_runner.run_flow.

Mocks the two reused execution primitives (run_agent, execute_operation) so
these tests exercise only the flow_runner's own stage-sequencing logic:
required_outputs enforcement, on_fail policies, repeat_until jump-back.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core_graph.agent_loop import AgentRunResult
from core_graph.subgraphs.specialist.flow_runner import _envelope, run_flow
from core_graph.subgraphs.specialist.blackboard import Blackboard
from core_graph.subgraphs.specialist.flow_spec import parse_flow_spec


def _det_spec(op="p/o", **stage_overrides):
    stage = {"id": "s1", "kind": "deterministic", "op": op, **stage_overrides}
    return parse_flow_spec(
        {"flow_id": "f1", "name": "F1", "stages": [stage]}, owner="test_plugin"
    )


@pytest.mark.asyncio
async def test_deterministic_stage_ok():
    spec = _det_spec(required_outputs=["result"])
    with patch(
        "core.route_registry.execute.execute_operation",
        new=AsyncMock(return_value={"result": "ok"}),
    ):
        env = await run_flow(spec, "do it", tenant_id=1)
    assert env["status"] == "ok"


@pytest.mark.asyncio
async def test_missing_required_output_fails_closed():
    spec = _det_spec(required_outputs=["result"])
    with patch(
        "core.route_registry.execute.execute_operation",
        new=AsyncMock(return_value={"other": "value"}),
    ):
        env = await run_flow(spec, "do it", tenant_id=1)
    assert env["status"] == "error"
    assert env["error"] == "required_outputs_missing"
    assert env["missing"] == ["result"]


@pytest.mark.asyncio
async def test_deterministic_op_exception_fails_closed_by_default():
    from core.route_registry.execute import ExecuteError

    spec = _det_spec()
    with patch(
        "core.route_registry.execute.execute_operation",
        new=AsyncMock(side_effect=ExecuteError("boom", "op failed")),
    ):
        env = await run_flow(spec, "do it", tenant_id=1)
    assert env["status"] == "error"
    assert env["error"] == "stage_failed"


@pytest.mark.asyncio
async def test_on_fail_continue_swallows_stage_error():
    from core.route_registry.execute import ExecuteError

    spec = _det_spec(on_fail="continue")
    with patch(
        "core.route_registry.execute.execute_operation",
        new=AsyncMock(side_effect=ExecuteError("boom", "op failed")),
    ):
        env = await run_flow(spec, "do it", tenant_id=1)
    # on_fail=continue with no required_outputs: stage produces {} and the
    # flow completes rather than short-circuiting.
    assert env["status"] == "ok"


@pytest.mark.asyncio
async def test_deterministic_stage_forwards_caller_scopes_verbatim():
    """execute_operation must receive exactly the caller_scopes run_flow was
    given — the deterministic-stage half of the scope-threading fix. Every
    other test in this file calls run_flow with caller_scopes defaulting to
    None (unrestricted), so this is the only coverage of a restricted grant
    reaching the op boundary."""
    spec = _det_spec(required_outputs=["result"])
    captured = {}

    async def fake_execute(plugin_id, operation_id, args, *, caller_scopes, **kwargs):
        captured["caller_scopes"] = caller_scopes
        return {"result": "ok"}

    with patch(
        "core.route_registry.execute.execute_operation",
        new=AsyncMock(side_effect=fake_execute),
    ):
        env = await run_flow(
            spec, "do it", tenant_id=1, caller_scopes=["group:portfolio_plugin:ask"]
        )

    assert env["status"] == "ok"
    assert captured["caller_scopes"] == ["group:portfolio_plugin:ask"]


@pytest.mark.asyncio
async def test_agentic_stage_error_status_fails_closed():
    spec = parse_flow_spec(
        {
            "flow_id": "f2",
            "name": "F2",
            "stages": [{"id": "a1", "kind": "agentic", "tool_globs": ["p/*"]}],
        },
        owner="test_plugin",
    )
    bad_result = AgentRunResult(status="error", output=None, steps=1, errors=["llm_failed"])
    with patch(
        "core_graph.subgraphs.specialist.flow_runner.run_agent",
        new=AsyncMock(return_value=bad_result),
    ):
        env = await run_flow(spec, "do it", tenant_id=1)
    assert env["status"] == "error"
    assert env["stage"] == "a1"


@pytest.mark.asyncio
async def test_repeat_until_jumps_back_and_respects_max_rounds():
    spec = parse_flow_spec(
        {
            "flow_id": "f3",
            "name": "F3",
            "stages": [
                {"id": "compose", "kind": "deterministic", "op": "p/compose"},
                {
                    "id": "validate",
                    "kind": "deterministic",
                    "op": "p/validate",
                    "required_outputs": ["passes"],
                    "repeat_until": {
                        "field": "passes",
                        "equals": True,
                        "max_rounds": 2,
                        "on_retry_stage": "compose",
                    },
                },
            ],
        },
        owner="test_plugin",
    )
    calls: list[str] = []

    async def fake_execute(plugin_id, operation_id, args, **kwargs):
        calls.append(operation_id)
        if operation_id == "compose":
            return {"layout": "x"}
        # Always fails validation -> exhausts max_rounds, then flow completes.
        return {"passes": False}

    with patch("core.route_registry.execute.execute_operation", new=AsyncMock(side_effect=fake_execute)):
        env = await run_flow(spec, "do it", tenant_id=1)

    # compose ran 1 (initial) + 2 (retries) = 3 times; validate ran 3 times too.
    assert calls.count("compose") == 3
    assert calls.count("validate") == 3
    # required_outputs("passes") is present (False, not None) so it's not
    # "missing" — the flow completes ok once max_rounds is exhausted.
    assert env["status"] == "ok"


@pytest.mark.asyncio
async def test_repeat_until_stops_early_when_condition_met():
    spec = parse_flow_spec(
        {
            "flow_id": "f4",
            "name": "F4",
            "stages": [
                {"id": "compose", "kind": "deterministic", "op": "p/compose"},
                {
                    "id": "validate",
                    "kind": "deterministic",
                    "op": "p/validate",
                    "repeat_until": {
                        "field": "passes",
                        "equals": True,
                        "max_rounds": 5,
                        "on_retry_stage": "compose",
                    },
                },
            ],
        },
        owner="test_plugin",
    )
    calls: list[str] = []

    async def fake_execute(plugin_id, operation_id, args, **kwargs):
        calls.append(operation_id)
        if operation_id == "compose":
            return {}
        return {"passes": True}

    with patch("core.route_registry.execute.execute_operation", new=AsyncMock(side_effect=fake_execute)):
        env = await run_flow(spec, "do it", tenant_id=1)

    assert calls == ["compose", "validate"]
    assert env["status"] == "ok"


@pytest.mark.asyncio
async def test_carry_exposes_blackboard_slots():
    spec = _det_spec(writes=["result"])
    with patch(
        "core.route_registry.execute.execute_operation",
        new=AsyncMock(return_value={"result": "ok"}),
    ):
        env = await run_flow(spec, "do it", tenant_id=1)
    assert env["carry"]["result"] == "ok"


def test_envelope_hoists_ask_overlay_and_answer():
    spec = _det_spec()
    board = Blackboard(session_id="s", goal="game")
    board.write(
        blocks=[{"type": "fishTank", "id": "fish-tank-1"}],
        focus_slug="fisoul",
        pending_job={"job_id": "j1", "status": "ready"},
        answer_markdown="Fisoul is a Unity multiplayer game.",
        recommendations=[{"slug": "fisoul", "name": "Fisoul", "in_tank": False}],
    )
    env = _envelope(status="ok", spec=spec, phases=[], session_id="s", extra={"carry": dict(board.slots)}, board=board)
    assert env["blocks"][0]["id"] == "fish-tank-1"
    assert env["focus_slug"] == "fisoul"
    assert env["pending_job"]["job_id"] == "j1"
    assert env["message"] == "Fisoul is a Unity multiplayer game."
    assert env["summary"] == env["message"]
    assert env["recommendations"][0]["slug"] == "fisoul"
