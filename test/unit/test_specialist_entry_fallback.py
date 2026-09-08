"""specialist_entry must not leave triage_mode='specialist' stamped when it falls
through to classic GOAP planning — retriage.should_retriage's mode branches key off
that field, and a stale 'specialist' value makes the classic planner run harder to
retriage than an ordinary classic turn (see core_graph/runtime/retriage.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core_graph.node.specialist_entry import make_specialist_entry_node


def _patched(fake_run_specialist):
    return (
        patch(
            "core_graph.subgraphs.specialist.registry.run_specialist",
            new=AsyncMock(side_effect=fake_run_specialist),
        ),
        patch("core.scope_management.get_request_principal", return_value=None),
        patch("core.context.transport.is_local_stdio", return_value=True),
    )


@pytest.mark.asyncio
async def test_specialist_entry_exception_resets_triage_mode_to_classic():
    async def fake_run_specialist(goal, **kwargs):
        raise RuntimeError("pipeline exploded")

    node = make_specialist_entry_node(MagicMock())
    p1, p2, p3 = _patched(fake_run_specialist)
    with p1, p2, p3:
        out = await node({"user_query": "do something", "session_id": "s1"})

    assert out["triage_mode"] == "classic"
    assert out["response"] is None
    assert out["last_failure"]["error"] == "specialist_pipeline_exception"


@pytest.mark.asyncio
async def test_specialist_entry_failed_envelope_resets_triage_mode_to_classic():
    async def fake_run_specialist(goal, **kwargs):
        return {"status": "error", "message": "no usable output"}

    node = make_specialist_entry_node(MagicMock())
    p1, p2, p3 = _patched(fake_run_specialist)
    with p1, p2, p3:
        out = await node({"user_query": "do something", "session_id": "s1"})

    assert out["triage_mode"] == "classic"
    assert out["response"] is None
    assert out["last_failure"]["error"] == "specialist_pipeline_failed"


@pytest.mark.asyncio
async def test_specialist_entry_success_keeps_triage_mode_specialist():
    async def fake_run_specialist(goal, **kwargs):
        return {
            "status": "ok",
            "summary": "done",
            "specialist_domain": "generic",
            "goal_class": "discover",
        }

    node = make_specialist_entry_node(MagicMock())
    p1, p2, p3 = _patched(fake_run_specialist)
    with p1, p2, p3:
        out = await node({"user_query": "discover things", "session_id": "s1"})

    assert out["triage_mode"] == "specialist"
    assert out["response"] is not None
