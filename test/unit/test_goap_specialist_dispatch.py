"""Unit tests for the GOAP substack dispatch path: a registered FlowSpec
appears in the OperationCatalog as ``specialist/<flow_id>`` and is dispatchable
through the ordinary ``execute_operation`` plane (same path a GOAP plan step
uses for any other operation) — see flow_registry._flow_to_operation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.route_registry.execute import execute_operation
from core.route_registry.operation_catalog import get_operation_catalog
from core_graph.subgraphs.specialist.flow_registry import (
    clear_flows,
    register_flow,
    unregister_flow,
)
from core_graph.subgraphs.specialist.flow_spec import parse_flow_spec

FLOW = {
    "flow_id": "goap_dispatch_f1",
    "name": "GOAP Dispatch Flow",
    "requires": ["have:job_signals"],
    "provides": ["did:bake_portfolio", "have:portfolio_short_id"],
    "stages": [{"id": "s1", "kind": "deterministic", "op": "p/o"}],
}


@pytest.fixture(autouse=True)
def _clean():
    clear_flows()
    yield
    clear_flows()
    get_operation_catalog().remove_owner("specialist")


def test_registered_flow_appears_in_catalog():
    register_flow(parse_flow_spec(FLOW, owner="test_plugin"))
    op = get_operation_catalog().get("specialist", "goap_dispatch_f1")
    assert op is not None
    assert op.is_fast_path is True
    assert "goap_requires:have:job_signals" in op.tags
    assert "goap_provides:did:bake_portfolio" in op.tags
    assert "goap_provides:have:portfolio_short_id" in op.tags


def test_unregister_removes_from_catalog():
    register_flow(parse_flow_spec(FLOW, owner="test_plugin"))
    assert get_operation_catalog().get("specialist", "goap_dispatch_f1") is not None
    unregister_flow("goap_dispatch_f1")
    assert get_operation_catalog().get("specialist", "goap_dispatch_f1") is None


@pytest.mark.asyncio
async def test_execute_operation_dispatches_to_run_flow():
    register_flow(parse_flow_spec(FLOW, owner="test_plugin"))
    fake_env = {"status": "ok", "specialist": True, "flow_id": "goap_dispatch_f1"}
    with patch(
        "core_graph.subgraphs.specialist.flow_runner.run_flow",
        new=AsyncMock(return_value=fake_env),
    ):
        # caller_scopes=None == unrestricted local-stdio/CLI principal, the
        # same contract core_graph.node.specialist_entry uses for the GOAP
        # planner/executor's own internal dispatch.
        result = await execute_operation(
            "specialist", "goap_dispatch_f1", {"goal": "bake it"}, caller_scopes=None
        )
    assert result == fake_env


@pytest.mark.asyncio
async def test_execute_operation_anonymous_caller_denied():
    """Fail-closed: an anonymous caller (empty scopes) cannot dispatch a flow."""
    from core.route_registry.execute import ExecuteError

    register_flow(parse_flow_spec(FLOW, owner="test_plugin"))
    with pytest.raises(ExecuteError):
        await execute_operation(
            "specialist", "goap_dispatch_f1", {"goal": "bake it"}, caller_scopes=[]
        )


@pytest.mark.asyncio
async def test_execute_operation_unknown_flow_id_not_found():
    with pytest.raises(Exception):
        await execute_operation("specialist", "does_not_exist", {"goal": "x"}, caller_scopes=None)
