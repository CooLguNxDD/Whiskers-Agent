"""Unit tests for CliAgentChatModel and messages_to_prompt."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGenerationChunk

from core_graph.goap_agent.cli.base import CliResult, CliRunOptions
from core_graph.goap_agent.llm.cli_chat_model import (
    CliAgentChatModel,
    cli_auth_env_overrides,
    make_cli_chat_model,
    messages_to_prompt,
    normalize_cli_model_name,
)


def test_messages_to_prompt_roles():
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="hi"),
        AIMessage(content="yo"),
    ]
    text = messages_to_prompt(msgs)
    assert "[system]" in text
    assert "sys" in text
    assert "[user]" in text
    assert "hi" in text
    assert "[assistant]" in text


def test_normalize_cli_model_name_strips_provider_aliases():
    assert normalize_cli_model_name("claude-cli") == ""
    assert normalize_cli_model_name("agy-cli") == ""
    assert normalize_cli_model_name("CLAUDE-CLI") == ""
    assert normalize_cli_model_name("sonnet") == "sonnet"
    assert normalize_cli_model_name("claude-sonnet-4-5") == "claude-sonnet-4-5"
    assert normalize_cli_model_name("") == ""
    assert normalize_cli_model_name(None) == ""


def test_cli_auth_env_overrides_oauth_default(monkeypatch):
    monkeypatch.delenv("CLAUDE_CLI_AUTH_MODE", raising=False)
    env = cli_auth_env_overrides("claude", "setup-token-opaque-value")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "setup-token-opaque-value"
    assert env["ANTHROPIC_API_KEY"] == ""  # clear parent API key in child


def test_cli_auth_env_overrides_sk_ant_api_key(monkeypatch):
    monkeypatch.delenv("CLAUDE_CLI_AUTH_MODE", raising=False)
    env = cli_auth_env_overrides("claude", "sk-ant-api03-testkey")
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-api03-testkey"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


def test_cli_auth_env_overrides_mode_force_oauth(monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_AUTH_MODE", "oauth")
    env = cli_auth_env_overrides("claude", "sk-ant-api03-testkey")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-api03-testkey"


def test_cli_auth_env_overrides_agy():
    env = cli_auth_env_overrides("agy", "secret")
    assert env["AGY_API_KEY"] == "secret"
    assert env["ANTHROPIC_API_KEY"] == "secret"


def test_cli_auth_env_overrides_grok():
    env = cli_auth_env_overrides("grok", "secret-json")
    assert env["GROK_AUTH_JSON"] == "secret-json"


def test_make_cli_chat_model_maps_provider():
    m = make_cli_chat_model("claude-cli", "sonnet")
    assert m.agent == "claude"
    assert m.model_name == "sonnet"
    m2 = make_cli_chat_model("agy-cli", "")
    assert m2.agent == "agy"
    m3 = make_cli_chat_model("grok-cli", "")
    assert m3.agent == "grok"


def test_make_cli_chat_model_drops_provider_as_model():
    """Pool rows that stored model=claude-cli must not pass --model claude-cli."""
    m = make_cli_chat_model("claude-cli", "claude-cli")
    assert m.agent == "claude"
    assert m.model_name == ""
    assert m._resolved_model() is None
    m2 = make_cli_chat_model("agy-cli", "agy-cli")
    assert m2.model_name == ""
    m3 = make_cli_chat_model("grok-cli", "grok-cli")
    assert m3.model_name == ""


@pytest.mark.asyncio
async def test_cli_chat_model_agenerate_ok():
    model = CliAgentChatModel(agent="claude", model_name="")
    fake = CliResult(status="ok", text='{"mode":"task"}', agent="claude", returncode=0)
    with patch(
        "core_graph.goap_agent.llm.cli_chat_model.run_cli_agent",
        new_callable=AsyncMock,
        return_value=fake,
    ) as run:
        result = await model._agenerate([HumanMessage(content="hello")])
    assert len(result.generations) == 1
    assert result.generations[0].message.content == '{"mode":"task"}'
    opts: CliRunOptions = run.await_args.kwargs["options"]
    assert opts.model is None


@pytest.mark.asyncio
async def test_cli_chat_model_skips_alias_model_flag():
    model = CliAgentChatModel(agent="claude", model_name="claude-cli")
    fake = CliResult(status="ok", text="ok", agent="claude", returncode=0)
    with patch(
        "core_graph.goap_agent.llm.cli_chat_model.run_cli_agent",
        new_callable=AsyncMock,
        return_value=fake,
    ) as run:
        await model._agenerate([HumanMessage(content="hello")])
    opts: CliRunOptions = run.await_args.kwargs["options"]
    assert opts.model is None


@pytest.mark.asyncio
async def test_cli_chat_model_passes_real_model_flag():
    model = CliAgentChatModel(agent="claude", model_name="sonnet")
    fake = CliResult(status="ok", text="ok", agent="claude", returncode=0)
    with patch(
        "core_graph.goap_agent.llm.cli_chat_model.run_cli_agent",
        new_callable=AsyncMock,
        return_value=fake,
    ) as run:
        await model._agenerate([HumanMessage(content="hello")])
    opts: CliRunOptions = run.await_args.kwargs["options"]
    assert opts.model == "sonnet"


@pytest.mark.asyncio
async def test_cli_chat_model_injects_oauth_env():
    model = CliAgentChatModel(
        agent="claude",
        model_name="",
        api_key="oauth-token-from-pool",
    )
    fake = CliResult(status="ok", text="ok", agent="claude", returncode=0)
    with patch(
        "core_graph.goap_agent.llm.cli_chat_model.run_cli_agent",
        new_callable=AsyncMock,
        return_value=fake,
    ) as run:
        await model._agenerate([HumanMessage(content="hello")])
    opts: CliRunOptions = run.await_args.kwargs["options"]
    assert opts.env is not None
    assert opts.env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token-from-pool"
    assert opts.env.get("ANTHROPIC_API_KEY", None) == ""


def test_make_cli_chat_model_stores_api_key():
    m = make_cli_chat_model("claude-cli", "sonnet", api_key="  tok  ")
    assert m.api_key == "tok"
    assert m._identifying_params.get("has_api_key") is True
    assert "tok" not in str(m._identifying_params)


@pytest.mark.asyncio
async def test_cli_chat_model_agenerate_error_envelope():
    model = CliAgentChatModel(agent="claude")
    fake = CliResult(status="binary_not_found", error="missing", agent="claude")
    with patch(
        "core_graph.goap_agent.llm.cli_chat_model.run_cli_agent",
        new_callable=AsyncMock,
        return_value=fake,
    ):
        result = await model._agenerate([HumanMessage(content="hello")])
    content = result.generations[0].message.content
    assert "cli_agent_failed" in content


@pytest.mark.asyncio
async def test_cli_chat_model_astream_yields_chunk():
    model = CliAgentChatModel(agent="claude", model_name="")
    fake = CliResult(status="ok", text="hello-out", agent="claude", returncode=0)
    with patch(
        "core_graph.goap_agent.llm.cli_chat_model.run_cli_agent",
        new_callable=AsyncMock,
        return_value=fake,
    ):
        chunks = [c async for c in model._astream([HumanMessage(content="hi")])]
    assert len(chunks) == 1
    assert isinstance(chunks[0], ChatGenerationChunk)
    assert isinstance(chunks[0].message, AIMessageChunk)
    assert chunks[0].message.content == "hello-out"
