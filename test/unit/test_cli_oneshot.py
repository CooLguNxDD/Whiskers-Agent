"""Unit tests for the one-shot CLI provider bridge (core_graph/goap_agent/oneshot.py).

When the active core graph LLM provider is a headless CLI provider
(claude-cli / agy-cli), the whole turn should short-circuit to a single
run_cli_agent_dict spawn instead of wiring the CLI as the LLM for every
graph node.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core_graph.goap_agent.oneshot import (
    RECURSION_GUARD_SOURCE,
    active_core_cli_provider,
    oneshot_enabled,
    run_cli_oneshot,
    stream_cli_oneshot,
)


def test_recursion_guard_source_matches_injected_claim():
    """Must match core_graph.goap_agent.cli_inject's ocat_source claim value."""
    assert RECURSION_GUARD_SOURCE == "goap_agent_cli"


# ---------------------------------------------------------------------------
# oneshot_enabled
# ---------------------------------------------------------------------------


def test_oneshot_enabled_default_on(monkeypatch):
    monkeypatch.delenv("CLI_PROVIDER_ONESHOT", raising=False)
    assert oneshot_enabled() is True


def test_oneshot_enabled_explicit_off(monkeypatch):
    monkeypatch.setenv("CLI_PROVIDER_ONESHOT", "0")
    assert oneshot_enabled() is False


def test_oneshot_enabled_explicit_on(monkeypatch):
    monkeypatch.setenv("CLI_PROVIDER_ONESHOT", "true")
    assert oneshot_enabled() is True


# ---------------------------------------------------------------------------
# active_core_cli_provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_core_cli_provider_detects_claude_cli():
    sel = {"provider": "claude-cli", "model": "", "api_key": None, "base_url": None}
    with patch("core.llm_config_service.resolve_core_chat", new_callable=AsyncMock, return_value=sel):
        result = await active_core_cli_provider()
    assert result == "claude-cli"


@pytest.mark.asyncio
async def test_active_core_cli_provider_detects_agy_cli():
    sel = {"provider": "agy-cli", "model": "", "api_key": None, "base_url": None}
    with patch("core.llm_config_service.resolve_core_chat", new_callable=AsyncMock, return_value=sel):
        result = await active_core_cli_provider()
    assert result == "agy-cli"


@pytest.mark.asyncio
async def test_active_core_cli_provider_detects_grok_cli():
    sel = {"provider": "grok-cli", "model": "", "api_key": None, "base_url": None}
    with patch("core.llm_config_service.resolve_core_chat", new_callable=AsyncMock, return_value=sel):
        result = await active_core_cli_provider()
    assert result == "grok-cli"


@pytest.mark.asyncio
async def test_active_core_cli_provider_cloud_provider_returns_none():
    sel = {"provider": "openai", "model": "gpt-4o", "api_key": None, "base_url": None}
    with patch("core.llm_config_service.resolve_core_chat", new_callable=AsyncMock, return_value=sel):
        result = await active_core_cli_provider()
    assert result is None


@pytest.mark.asyncio
async def test_active_core_cli_provider_fails_safe_on_resolution_error():
    with patch(
        "core.llm_config_service.resolve_core_chat",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db down"),
    ):
        result = await active_core_cli_provider()
    assert result is None


# ---------------------------------------------------------------------------
# run_cli_oneshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cli_oneshot_maps_ok_result():
    sel = {"provider": "claude-cli", "model": "sonnet", "api_key": "setup-token", "base_url": None}
    cli_result = {
        "status": "ok",
        "text": "final answer text",
        "meta": {
            "session_id": "cli-sess-1",
            "total_cost_usd": 0.02,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "model": "sonnet",
        },
        "log": {"dir": "logs/goap_agent/run-1"},
    }
    with (
        patch("core.llm_config_service.resolve_core_chat", new_callable=AsyncMock, return_value=sel),
        patch(
            "core_graph.goap_agent.cli.runner.run_cli_agent_dict",
            new_callable=AsyncMock,
            return_value=cli_result,
        ) as run,
    ):
        envelope = await run_cli_oneshot(
            "do the thing",
            provider="claude-cli",
            session_id="sess-1",
            bearer_token="bearer-xyz",
        )

    assert envelope["status"] == "ok"
    assert envelope["message"] == "final answer text"
    assert envelope["meta"]["cli"]["agent"] == "claude"
    assert envelope["meta"]["cli"]["provider"] == "claude-cli"
    assert envelope["meta"]["cli"]["session_id"] == "cli-sess-1"
    assert envelope["meta"]["cli"]["total_cost_usd"] == 0.02

    kwargs = run.await_args.kwargs
    assert kwargs["agent"] == "claude"
    assert kwargs["session_id"] == "sess-1"
    # Outer caller bearer is intentionally dropped so the injector mints an
    # admin token with ocat_source=goap_agent_cli (public CatPortfolio deny-all
    # keys cannot drive GoapAgent_* tools; recursion guard needs the claim).
    assert kwargs["bearer_token"] is None
    assert kwargs["model"] == "sonnet"
    # Pool api_key mapped to CLAUDE_CODE_OAUTH_TOKEN (opaque setup-token).
    assert kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "setup-token"


@pytest.mark.asyncio
async def test_run_cli_oneshot_maps_agy_provider():
    sel = {"provider": "agy-cli", "model": "", "api_key": None, "base_url": None}
    cli_result = {"status": "ok", "text": "ok", "meta": {}, "log": None}
    with (
        patch("core.llm_config_service.resolve_core_chat", new_callable=AsyncMock, return_value=sel),
        patch(
            "core_graph.goap_agent.cli.runner.run_cli_agent_dict",
            new_callable=AsyncMock,
            return_value=cli_result,
        ) as run,
    ):
        envelope = await run_cli_oneshot("hi", provider="agy-cli", session_id=None)

    assert envelope["status"] == "ok"
    assert envelope["meta"]["cli"]["agent"] == "agy"
    assert run.await_args.kwargs["agent"] == "agy"


@pytest.mark.asyncio
async def test_run_cli_oneshot_maps_grok_provider():
    sel = {"provider": "grok-cli", "model": "", "api_key": "some-grok-auth", "base_url": None}
    cli_result = {"status": "ok", "text": "ok", "meta": {}, "log": None}
    with (
        patch("core.llm_config_service.resolve_core_chat", new_callable=AsyncMock, return_value=sel),
        patch(
            "core_graph.goap_agent.cli.runner.run_cli_agent_dict",
            new_callable=AsyncMock,
            return_value=cli_result,
        ) as run,
    ):
        envelope = await run_cli_oneshot("hi", provider="grok-cli", session_id=None)

    assert envelope["status"] == "ok"
    assert envelope["meta"]["cli"]["agent"] == "grok"
    assert run.await_args.kwargs["agent"] == "grok"
    assert run.await_args.kwargs["env"]["GROK_AUTH_JSON"] == "some-grok-auth"


@pytest.mark.asyncio
async def test_run_cli_oneshot_maps_error_result():
    sel = {"provider": "claude-cli", "model": "", "api_key": None, "base_url": None}
    cli_result = {"status": "error", "error": "binary_not_found: claude missing", "text": "", "meta": {}, "log": None}
    with (
        patch("core.llm_config_service.resolve_core_chat", new_callable=AsyncMock, return_value=sel),
        patch(
            "core_graph.goap_agent.cli.runner.run_cli_agent_dict",
            new_callable=AsyncMock,
            return_value=cli_result,
        ),
    ):
        envelope = await run_cli_oneshot("hi", provider="claude-cli")

    assert envelope["status"] == "error"
    assert "binary_not_found" in envelope["message"]


@pytest.mark.asyncio
async def test_run_cli_oneshot_drops_provider_alias_as_model():
    """Pool rows that stored model=claude-cli must not pass --model claude-cli."""
    sel = {"provider": "claude-cli", "model": "claude-cli", "api_key": None, "base_url": None}
    cli_result = {"status": "ok", "text": "ok", "meta": {}, "log": None}
    with (
        patch("core.llm_config_service.resolve_core_chat", new_callable=AsyncMock, return_value=sel),
        patch(
            "core_graph.goap_agent.cli.runner.run_cli_agent_dict",
            new_callable=AsyncMock,
            return_value=cli_result,
        ) as run,
    ):
        await run_cli_oneshot("hi", provider="claude-cli")
    assert run.await_args.kwargs["model"] is None


# ---------------------------------------------------------------------------
# stream_cli_oneshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_cli_oneshot_yields_values_token_values_end():
    ok_envelope = {"status": "ok", "message": "the answer", "meta": {"cli": {"agent": "claude"}}}
    with patch(
        # stream_cli_oneshot lives in oneshot_cli.mode and calls sibling run_cli_oneshot
        "core_graph.subgraphs.oneshot_cli.mode.run_cli_oneshot",
        new_callable=AsyncMock,
        return_value=ok_envelope,
    ):
        events = [
            ev
            async for ev in stream_cli_oneshot("hi", provider="claude-cli", session_id="s1")
        ]

    types = [ev["type"] for ev in events]
    assert types == ["values", "token", "values", "end"]
    assert events[0]["state"]["active_node"] == "cli_agent"
    assert events[0]["state"]["response"] is None
    assert events[1]["text"] == "the answer"
    assert events[2]["state"]["response"] == ok_envelope


@pytest.mark.asyncio
async def test_stream_cli_oneshot_error_envelope_yields_values_then_error():
    err_envelope = {"status": "error", "message": "cli_agent_failed", "meta": {}}
    with patch(
        "core_graph.subgraphs.oneshot_cli.mode.run_cli_oneshot",
        new_callable=AsyncMock,
        return_value=err_envelope,
    ):
        events = [
            ev
            async for ev in stream_cli_oneshot("hi", provider="claude-cli")
        ]

    types = [ev["type"] for ev in events]
    assert types == ["values", "values", "error"]
    assert events[-1]["message"] == "cli_agent_failed"


@pytest.mark.asyncio
async def test_stream_cli_oneshot_exception_yields_error():
    with patch(
        "core_graph.subgraphs.oneshot_cli.mode.run_cli_oneshot",
        new_callable=AsyncMock,
        side_effect=RuntimeError("spawn failed"),
    ):
        events = [
            ev
            async for ev in stream_cli_oneshot("hi", provider="claude-cli")
        ]

    types = [ev["type"] for ev in events]
    assert types == ["values", "error"]
    assert "spawn failed" in events[-1]["message"]


# ---------------------------------------------------------------------------
# mcp_tool.py branch wiring: short-circuit + recursion guard + rollback switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_graph_impl_short_circuits_to_oneshot(monkeypatch):
    """CLI provider active + no recursion guard -> native graph never runs."""
    import core_graph.mcp_tool as mcp_tool

    monkeypatch.setattr(mcp_tool, "_LLM_USABLE", True)
    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", True)
    monkeypatch.setattr(
        mcp_tool,
        "_get_graph",
        AsyncMock(side_effect=AssertionError("native graph must not run when one-shot fires")),
    )

    async def _fake_stream_cli_oneshot(user_message, *, provider, session_id=None, bearer_token=None):
        yield {"type": "values", "state": {"active_node": "cli_agent", "response": None}}
        yield {"type": "token", "node": "cli_agent", "text": "hi back"}
        yield {"type": "end"}

    with (
        patch(
            "core_graph.goap_agent.oneshot.active_core_cli_provider",
            new_callable=AsyncMock,
            return_value="claude-cli",
        ),
        patch("core_graph.goap_agent.oneshot.stream_cli_oneshot", _fake_stream_cli_oneshot),
    ):
        events = [ev async for ev in mcp_tool.stream_graph_impl("hi", session_id="s-oneshot")]

    types = [ev["type"] for ev in events]
    assert types == ["values", "token", "end"]


@pytest.mark.asyncio
async def test_stream_graph_impl_recursion_guard_skips_oneshot(monkeypatch):
    """caller_source == RECURSION_GUARD_SOURCE (the CLI agent's own injected
    child call) must never re-spawn a nested CLI agent — native graph runs."""
    import core_graph.mcp_tool as mcp_tool

    monkeypatch.setattr(mcp_tool, "_LLM_USABLE", True)
    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", True)
    monkeypatch.setattr(
        mcp_tool, "_get_graph", AsyncMock(side_effect=RuntimeError("reached native graph"))
    )

    with patch(
        "core_graph.goap_agent.oneshot.active_core_cli_provider",
        new_callable=AsyncMock,
        return_value="claude-cli",
    ) as cli_check:
        with pytest.raises(RuntimeError, match="reached native graph"):
            async for _ in mcp_tool.stream_graph_impl(
                "hi", session_id="s-guard", caller_source=RECURSION_GUARD_SOURCE
            ):
                pass

    cli_check.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_graph_impl_oneshot_disabled_via_env(monkeypatch):
    """CLI_PROVIDER_ONESHOT=0 forces the native graph even with a CLI provider active."""
    import core_graph.mcp_tool as mcp_tool

    monkeypatch.setenv("CLI_PROVIDER_ONESHOT", "0")
    monkeypatch.setattr(mcp_tool, "_LLM_USABLE", True)
    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", True)
    monkeypatch.setattr(
        mcp_tool, "_get_graph", AsyncMock(side_effect=RuntimeError("native graph ran"))
    )

    with patch(
        "core_graph.goap_agent.oneshot.active_core_cli_provider",
        new_callable=AsyncMock,
        return_value="claude-cli",
    ) as cli_check:
        with pytest.raises(RuntimeError, match="native graph ran"):
            async for _ in mcp_tool.stream_graph_impl("hi"):
                pass

    cli_check.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_graph_impl_headless_short_circuits_to_oneshot(monkeypatch):
    """Headless (ctx=None) run_graph_impl path also short-circuits to one-shot."""
    import core_graph.mcp_tool as mcp_tool

    monkeypatch.setattr(mcp_tool, "_LLM_USABLE", True)
    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", True)
    monkeypatch.setattr(
        mcp_tool,
        "_get_graph",
        AsyncMock(side_effect=AssertionError("native graph must not run when one-shot fires")),
    )

    ok_envelope = {"status": "ok", "message": "done", "meta": {"cli": {"agent": "claude"}}}
    with (
        patch(
            "core_graph.goap_agent.oneshot.active_core_cli_provider",
            new_callable=AsyncMock,
            return_value="claude-cli",
        ),
        patch(
            "core_graph.goap_agent.oneshot.run_cli_oneshot",
            new_callable=AsyncMock,
            return_value=ok_envelope,
        ),
    ):
        out = await mcp_tool.run_graph_impl("hi", session_id="s-headless")

    assert out == ok_envelope
