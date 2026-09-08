"""Unit tests for core_graph.runtime mode registry and router selection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core_graph.runtime.mode_router import (
    RunRequest,
    graph_mode,
    oneshot_can_handle,
    reset_default_stacks_for_tests,
    select_kind,
    select_stack,
)
from core_graph.runtime.registry import clear_stacks, get_stack, list_stacks


@pytest.fixture(autouse=True)
def _reset_stacks(monkeypatch):
    """Isolate stack registry and GRAPH_MODE per test."""
    monkeypatch.delenv("GRAPH_MODE", raising=False)
    monkeypatch.delenv("CLI_PROVIDER_ONESHOT", raising=False)
    reset_default_stacks_for_tests()
    yield
    clear_stacks()
    reset_default_stacks_for_tests()


def test_default_stacks_registered():
    ids = {s.id for s in list_stacks()}
    assert "oneshot_cli" in ids
    assert "root" in ids
    assert get_stack("root") is not None
    assert get_stack("oneshot_cli").priority < get_stack("root").priority


def test_graph_mode_default_auto(monkeypatch):
    monkeypatch.delenv("GRAPH_MODE", raising=False)
    assert graph_mode() == "auto"


def test_graph_mode_root_aliases(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "classic")
    assert graph_mode() == "root"
    monkeypatch.setenv("GRAPH_MODE", "specialist")
    assert graph_mode() == "root"


@pytest.mark.asyncio
async def test_recursion_guard_skips_oneshot():
    req = RunRequest(
        user_message="hello",
        caller_source="goap_agent_cli",
    )
    with patch(
        "core_graph.goap_agent.oneshot.active_core_cli_provider",
        new_callable=AsyncMock,
        return_value="claude-cli",
    ):
        assert await oneshot_can_handle(req) is False
        assert await select_kind(req) == "root"


@pytest.mark.asyncio
async def test_graph_mode_root_forces_root_even_with_cli(monkeypatch):
    monkeypatch.setenv("GRAPH_MODE", "root")
    req = RunRequest(user_message="plan something")
    with patch(
        "core_graph.goap_agent.oneshot.active_core_cli_provider",
        new_callable=AsyncMock,
        return_value="claude-cli",
    ), patch(
        "core_graph.goap_agent.oneshot.oneshot_enabled",
        return_value=True,
    ):
        assert await oneshot_can_handle(req) is False
        stack = await select_stack(req)
        assert stack.id == "root"


@pytest.mark.asyncio
async def test_auto_selects_oneshot_when_cli_core():
    req = RunRequest(user_message="do work")
    with patch(
        "core_graph.goap_agent.oneshot.active_core_cli_provider",
        new_callable=AsyncMock,
        return_value="claude-cli",
    ), patch(
        "core_graph.goap_agent.oneshot.oneshot_enabled",
        return_value=True,
    ):
        assert await oneshot_can_handle(req) is True
        assert await select_kind(req) == "oneshot"


@pytest.mark.asyncio
async def test_oneshot_disabled_falls_to_root():
    req = RunRequest(user_message="do work")
    with patch(
        "core_graph.goap_agent.oneshot.active_core_cli_provider",
        new_callable=AsyncMock,
        return_value="claude-cli",
    ), patch(
        "core_graph.goap_agent.oneshot.oneshot_enabled",
        return_value=False,
    ):
        assert await oneshot_can_handle(req) is False
        assert await select_kind(req) == "root"


@pytest.mark.asyncio
async def test_mode_hint_root_overrides_cli():
    req = RunRequest(user_message="x", mode_hint="classic")
    with patch(
        "core_graph.goap_agent.oneshot.active_core_cli_provider",
        new_callable=AsyncMock,
        return_value="grok-cli",
    ), patch(
        "core_graph.goap_agent.oneshot.oneshot_enabled",
        return_value=True,
    ):
        assert await select_kind(req) == "root"
