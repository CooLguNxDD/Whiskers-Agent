"""AgentSpec.model / llm_kind resolution (core_graph/agent_loop/runner.py::_get_llm) — S8a.

Previously `AgentSpec.llm_kind` was a dead field: any value other than "core"
routed through `resolve_step_llm(None)`, which is identical to
`get_graph_core_llm()`. This proves the fix: `model` (and the deprecated
`llm_kind` back-compat path) actually resolve through the model-role layer.
"""

from __future__ import annotations

import pytest

from core_graph.agent_loop.runner import _get_llm
from core_graph.agent_loop.spec import AgentSpec

pytestmark = pytest.mark.asyncio


class _FakeLLM:
    def __init__(self, name):
        self.name = name


async def test_model_none_and_llm_kind_core_use_graph_core_llm(monkeypatch):
    core = _FakeLLM("core")
    monkeypatch.setattr("core.llm_config_service.get_graph_core_llm", lambda: _awaitable(core))
    spec = AgentSpec(name="a", system_prompt="p")
    assert await _get_llm(spec) is core


async def test_explicit_model_selector_resolves_through_role_layer(monkeypatch):
    core = _FakeLLM("core")
    resolved = _FakeLLM("resolved")
    monkeypatch.setattr("core.llm_config_service.get_graph_core_llm", lambda: _awaitable(core))

    async def fake_resolve_role_llm(selector, *, fallback=None):
        assert selector == "strongest"
        return resolved, "resolved-model"

    monkeypatch.setattr("core_graph.model_roles.resolver.resolve_role_llm", fake_resolve_role_llm)

    spec = AgentSpec(name="a", system_prompt="p", model="strongest")
    assert await _get_llm(spec) is resolved


async def test_role_prefix_selector_resolves_first_rung(monkeypatch):
    core = _FakeLLM("core")
    resolved = _FakeLLM("role-resolved")
    monkeypatch.setattr("core.llm_config_service.get_graph_core_llm", lambda: _awaitable(core))

    async def fake_first_rung(role_id, *, fallback=None):
        assert role_id == "planner_goal"
        return resolved, "role-model"

    monkeypatch.setattr("core_graph.model_roles.ladder.resolve_role_first_rung", fake_first_rung)

    spec = AgentSpec(name="a", system_prompt="p", model="role:planner_goal")
    assert await _get_llm(spec) is resolved


async def test_deprecated_llm_kind_treated_as_selector_when_model_unset(monkeypatch):
    core = _FakeLLM("core")
    resolved = _FakeLLM("resolved")
    monkeypatch.setattr("core.llm_config_service.get_graph_core_llm", lambda: _awaitable(core))

    async def fake_resolve_role_llm(selector, *, fallback=None):
        assert selector == "fast"
        return resolved, "resolved-model"

    monkeypatch.setattr("core_graph.model_roles.resolver.resolve_role_llm", fake_resolve_role_llm)

    spec = AgentSpec(name="a", system_prompt="p", llm_kind="fast")
    assert await _get_llm(spec) is resolved


async def test_explicit_model_wins_over_llm_kind(monkeypatch):
    """model= takes precedence when both are set (llm_kind is simply ignored once model is non-None)."""
    core = _FakeLLM("core")
    monkeypatch.setattr("core.llm_config_service.get_graph_core_llm", lambda: _awaitable(core))
    calls = []

    async def fake_resolve_role_llm(selector, *, fallback=None):
        calls.append(selector)
        return _FakeLLM(selector), selector

    monkeypatch.setattr("core_graph.model_roles.resolver.resolve_role_llm", fake_resolve_role_llm)

    spec = AgentSpec(name="a", system_prompt="p", model="balanced", llm_kind="fast")
    llm = await _get_llm(spec)

    assert calls == ["balanced"]
    assert llm.name == "balanced"


def _awaitable(value):
    async def _inner():
        return value
    return _inner()
