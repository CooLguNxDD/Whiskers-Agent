"""Unit tests for core_graph.model_roles.ladder.run_role_ladder."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from core_graph.model_roles.ladder import run_role_ladder
from core_graph.model_roles.role_spec import ModelRoleSpec, Rung, StateCondition, Validation
from _llm_stubs import FakeResp

pytestmark = pytest.mark.asyncio


class _CountingClient:
    """Raises for the first ``n_raises`` calls, then succeeds. Used to fake resolver reuse."""

    def __init__(self, n_raises: int, *, model: str = "m", ok_content=None):
        self.n_raises = n_raises
        self.calls = 0
        self.model = model
        self.ok_content = ok_content if ok_content is not None else {"ok": True}

    async def ainvoke(self, msgs):
        self.calls += 1
        if self.calls <= self.n_raises:
            raise RuntimeError("boom")
        return FakeResp(self.ok_content, model=self.model)


async def _attempt(llm):
    return await llm.ainvoke([])


def _extract(raw):
    return raw.content


async def test_ok_on_rung_0_no_escalation():
    spec = ModelRoleSpec(role_id="r", ladder=(Rung(selector="core"),))
    client = _CountingClient(n_raises=0, ok_content={"mode": "chat"})
    res = await run_role_ladder(
        spec, attempt=_attempt, extract=_extract, fallback_llm=client,
    )
    assert res.status == "ok"
    assert res.escalated is False
    assert len(res.attempts) == 1
    assert res.value == {"mode": "chat"}
    assert res.token_usage["model_usage"][client.model]["calls"] == 1


async def test_invalid_rung_0_escalates_and_folds_twice():
    spec = ModelRoleSpec(
        role_id="r",
        ladder=(Rung(selector="core"), Rung(selector="core")),
        validate=Validation(required_keys=("mode",)),
    )

    class _TwoStage:
        def __init__(self):
            self.calls = 0
            self.model = "m"

        async def ainvoke(self, msgs):
            self.calls += 1
            content = {} if self.calls == 1 else {"mode": "chat"}
            return FakeResp(content, model=self.model)

    client = _TwoStage()
    res = await run_role_ladder(spec, attempt=_attempt, extract=_extract, fallback_llm=client)
    assert res.status == "ok"
    assert res.escalated is True
    assert len(res.attempts) == 2
    assert res.attempts[0].status == "invalid"
    assert res.attempts[1].status == "ok"
    assert res.token_usage["model_usage"]["m"]["calls"] == 2


async def test_all_rungs_raise_terminal_fallback_runs_once(monkeypatch):
    """Terminal ctx_llm fallback must be a genuinely distinct client, not a
    redundant repeat of the exact same object the exhausted rungs just used
    (see core_graph/node/planner.py test regression this guards against —
    a "core"-selector rung already *is* fallback_llm, so retrying it would
    silently double-consume any queued/stateful test double or, worse,
    double-bill a real API call for no benefit)."""
    spec = ModelRoleSpec(role_id="r", ladder=(Rung(selector="fast"), Rung(selector="fast")))
    rung_client = _CountingClient(n_raises=99)  # always raises
    fallback_client = _CountingClient(n_raises=0, ok_content={"mode": "chat"})

    async def fake_resolve(selector, *, fallback=None):
        return rung_client, "rung-model"

    monkeypatch.setattr("core_graph.model_roles.resolver.resolve_role_llm", fake_resolve)

    res = await run_role_ladder(spec, attempt=_attempt, extract=_extract, fallback_llm=fallback_client)
    assert res.status == "exhausted"
    assert rung_client.calls == 2  # both rungs attempted against the resolved (non-fallback) client
    assert fallback_client.calls == 1  # terminal ctx_llm fallback runs exactly once
    assert res.attempts[-1].selector == "ctx_llm"
    assert res.attempts[-1].status == "ok"
    assert res.value == {"mode": "chat"}


async def test_terminal_fallback_skipped_when_rung_already_used_fallback_llm():
    """When the exhausted ladder's rungs resolved to fallback_llm itself
    (e.g. the default single-rung "core" spec used when the flag is off),
    the ctx_llm terminal step must NOT re-attempt the identical client."""
    spec = ModelRoleSpec(role_id="r", ladder=(Rung(selector="core"),))
    client = _CountingClient(n_raises=99)
    res = await run_role_ladder(spec, attempt=_attempt, extract=_extract, fallback_llm=client)
    assert res.status == "exhausted"
    assert client.calls == 1  # exactly the one ladder attempt — no redundant terminal retry
    assert res.value is None


async def test_entry_condition_bumps_start_rung():
    spec = ModelRoleSpec(
        role_id="r",
        ladder=(Rung(selector="core"), Rung(selector="core")),
        entry_conditions=(StateCondition(field="repeat_failure_count", op="gt", value=0),),
    )
    client = _CountingClient(n_raises=0, ok_content={"mode": "chat"})
    res = await run_role_ladder(
        spec, attempt=_attempt, extract=_extract, fallback_llm=client,
        state={"repeat_failure_count": 1},
    )
    assert res.rung_index == 1
    assert len(res.attempts) == 1


async def test_min_rung_honored():
    spec = ModelRoleSpec(role_id="r", ladder=(Rung(selector="core"), Rung(selector="core")))
    client = _CountingClient(n_raises=0, ok_content={"mode": "chat"})
    res = await run_role_ladder(spec, attempt=_attempt, extract=_extract, fallback_llm=client, min_rung=1)
    assert res.rung_index == 1
    assert len(res.attempts) == 1


async def test_unregistered_role_falls_back_to_single_attempt(monkeypatch):
    monkeypatch.setattr("utils.server_config.MODEL_ROLES_ENABLED", True)
    client = _CountingClient(n_raises=0, ok_content={"mode": "chat"})
    res = await run_role_ladder("does_not_exist_role_xyz", attempt=_attempt, extract=_extract, fallback_llm=client)
    assert res.status == "ok"
    assert len(res.attempts) == 1
    assert client.calls == 1


async def test_flag_off_degrades_to_single_attempt(monkeypatch):
    monkeypatch.setattr("utils.server_config.MODEL_ROLES_ENABLED", False)
    client = _CountingClient(n_raises=0, ok_content={"mode": "chat"})
    res = await run_role_ladder("triage", attempt=_attempt, extract=_extract, fallback_llm=client)
    assert res.status == "ok"
    assert len(res.attempts) == 1


async def test_exception_no_escalate_goes_straight_to_terminal(monkeypatch):
    spec = ModelRoleSpec(
        role_id="r",
        ladder=(Rung(selector="fast"), Rung(selector="fast")),
        escalate_on_exception=False,
    )
    rung_client = _CountingClient(n_raises=99)
    fallback_client = _CountingClient(n_raises=0, ok_content={"mode": "chat"})

    async def fake_resolve(selector, *, fallback=None):
        return rung_client, "rung-model"

    monkeypatch.setattr("core_graph.model_roles.resolver.resolve_role_llm", fake_resolve)

    res = await run_role_ladder(spec, attempt=_attempt, extract=_extract, fallback_llm=fallback_client)
    # First attempt raises; escalate_on_exception=False means terminal fires immediately
    # rather than trying rung 1 — one failed rung + one distinct terminal fallback call.
    assert rung_client.calls == 1
    assert fallback_client.calls == 1
    assert res.status == "exhausted"
