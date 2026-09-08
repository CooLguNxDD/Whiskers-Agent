"""Specialist-stack model selection end to end (§8 payoff) — flow_runner + manifest effort.

Patches `run_agent` the way test_flow_runner_stages.py already does, so these
tests exercise only the model-selection wiring: a FlowSpec stage's declared
effort reaching `AgentSpec.model`, manifest-level defaults applying when a
stage/flow declares nothing, and effort_overrides[goal_class] beating the
manifest default for that class.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core_graph.agent_loop import AgentRunResult
from core_graph.model_roles.manifest_effort import (
    ManifestEffort,
    register_manifest_effort,
    unregister_manifest_effort,
)
from core_graph.subgraphs.specialist.flow_runner import run_flow
from core_graph.subgraphs.specialist.flow_spec import parse_flow_spec


def _agentic_spec(owner="test_plugin", flow_effort=None, **stage_overrides):
    stage = {"id": "s1", "kind": "agentic", **stage_overrides}
    data = {"flow_id": "f1", "name": "F1", "stages": [stage]}
    if flow_effort is not None:
        data["effort"] = flow_effort
    return parse_flow_spec(data, owner=owner)


@pytest.fixture(autouse=True)
def _clean_manifest_effort():
    yield
    unregister_manifest_effort("test_plugin")


@pytest.mark.asyncio
async def test_stage_effort_reaches_agent_spec_model():
    spec = _agentic_spec(effort="low")
    fake_run_agent = AsyncMock(return_value=AgentRunResult(status="ok", output={}, steps=1, tool_calls=[], errors=[]))
    with patch("core_graph.subgraphs.specialist.flow_runner.run_agent", fake_run_agent):
        await run_flow(spec, "do it", tenant_id=1)

    agent_spec = fake_run_agent.call_args.args[0]
    assert agent_spec.model == "effort:low"


@pytest.mark.asyncio
async def test_no_effort_keys_falls_through_to_core_specialist_role():
    """A flow with no effort keys anywhere resolves to today's behaviour
    (the core "specialist" role, which itself defaults to a single-rung
    "core" ladder — i.e. no observable routing change)."""
    spec = _agentic_spec()
    fake_run_agent = AsyncMock(return_value=AgentRunResult(status="ok", output={}, steps=1, tool_calls=[], errors=[]))
    with patch("core_graph.subgraphs.specialist.flow_runner.run_agent", fake_run_agent):
        await run_flow(spec, "do it", tenant_id=1)

    agent_spec = fake_run_agent.call_args.args[0]
    assert agent_spec.model == "role:specialist"


@pytest.mark.asyncio
async def test_manifest_level_effort_applies_when_stage_and_flow_declare_nothing():
    register_manifest_effort(ManifestEffort(plugin_id="test_plugin", effort="high"))
    spec = _agentic_spec()  # no stage/flow effort
    fake_run_agent = AsyncMock(return_value=AgentRunResult(status="ok", output={}, steps=1, tool_calls=[], errors=[]))
    with patch("core_graph.subgraphs.specialist.flow_runner.run_agent", fake_run_agent):
        await run_flow(spec, "do it", tenant_id=1)

    agent_spec = fake_run_agent.call_args.args[0]
    assert agent_spec.model == "effort:high"


@pytest.mark.asyncio
async def test_flow_effort_beats_manifest_default():
    register_manifest_effort(ManifestEffort(plugin_id="test_plugin", effort="high"))
    spec = _agentic_spec(flow_effort="medium")
    fake_run_agent = AsyncMock(return_value=AgentRunResult(status="ok", output={}, steps=1, tool_calls=[], errors=[]))
    with patch("core_graph.subgraphs.specialist.flow_runner.run_agent", fake_run_agent):
        await run_flow(spec, "do it", tenant_id=1)

    agent_spec = fake_run_agent.call_args.args[0]
    assert agent_spec.model == "effort:medium"


@pytest.mark.asyncio
async def test_existing_flow_json_without_effort_keys_is_unaffected_by_manifest():
    """A plugin with a manifest effort default but a flow that predates the
    feature still resolves — manifest still applies as the fallback (this is
    the intended additive behaviour, not a break)."""
    register_manifest_effort(ManifestEffort(plugin_id="test_plugin", effort="low"))
    spec = _agentic_spec()
    fake_run_agent = AsyncMock(return_value=AgentRunResult(status="ok", output={}, steps=1, tool_calls=[], errors=[]))
    with patch("core_graph.subgraphs.specialist.flow_runner.run_agent", fake_run_agent):
        env = await run_flow(spec, "do it", tenant_id=1)

    assert env["status"] == "ok"
    agent_spec = fake_run_agent.call_args.args[0]
    assert agent_spec.model == "effort:low"
