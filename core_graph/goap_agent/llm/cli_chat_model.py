"""LangChain BaseChatModel that shells out to a headless CLI agent."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncIterator, Iterator, List, Optional

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from core_graph.goap_agent.cli.base import CliRunOptions
from core_graph.goap_agent.cli.runner import run_cli_agent

logger = logging.getLogger("whiskers.goap_agent.llm")

# Whiskers Agent provider ids / driver aliases are NOT valid Claude/agy ``--model`` values.
# The admin UI used to persist ``model=claude-cli`` when the optional model field
# was left blank; Claude Code then rejects ``--model claude-cli`` with 404.
_PROVIDER_ALIAS_MODELS = frozenset(
    {
        "claude-cli",
        "agy-cli",
        "grok-cli",
        "claude",
        "agy",
        "grok",
        "cli-agent",
        "generic",
        "antigravity",
    }
)


def normalize_cli_model_name(model: str | None) -> str:
    """Return a CLI ``--model`` flag value, or empty to use the binary default.

    Strips provider ids mistakenly stored as model names (e.g. ``claude-cli``).
    """
    m = (model or "").strip()
    if not m:
        return ""
    if m.lower() in _PROVIDER_ALIAS_MODELS:
        return ""
    return m


def cli_auth_env_overrides(agent: str, api_key: str | None) -> dict[str, str]:
    """Map a pool ``api_key`` into env vars for the headless CLI subprocess.

    * Claude default: opaque / setup-token → ``CLAUDE_CODE_OAUTH_TOKEN``;
      ``sk-ant-…`` → ``ANTHROPIC_API_KEY``. Override with ``CLAUDE_CLI_AUTH_MODE``
      (``oauth`` | ``api_key``).
    * Agy: best-effort ``AGY_API_KEY`` + ``ANTHROPIC_API_KEY`` (same secret).
    * Grok: maps pool ``api_key`` to ``GROK_AUTH_JSON``.
    """
    k = (api_key or "").strip()
    if not k:
        return {}
    agent_l = (agent or "claude").strip().lower()
    if agent_l in ("agy", "agy-cli", "antigravity"):
        return {"AGY_API_KEY": k, "ANTHROPIC_API_KEY": k}
    if agent_l in ("grok", "grok-cli"):
        return {"GROK_AUTH_JSON": k}

    mode = (os.environ.get("CLAUDE_CLI_AUTH_MODE") or "").strip().lower()
    if mode in ("oauth", "token", "oauth_token"):
        # Empty string clears a parent process API key in the child env (runner).
        return {"CLAUDE_CODE_OAUTH_TOKEN": k, "ANTHROPIC_API_KEY": ""}
    if mode in ("api_key", "api", "key"):
        return {"ANTHROPIC_API_KEY": k, "CLAUDE_CODE_OAUTH_TOKEN": ""}
    # Anthropic console API keys are sk-ant-*; claude setup-token is opaque.
    if k.startswith("sk-ant-"):
        return {"ANTHROPIC_API_KEY": k, "CLAUDE_CODE_OAUTH_TOKEN": ""}
    return {"CLAUDE_CODE_OAUTH_TOKEN": k, "ANTHROPIC_API_KEY": ""}


def _max_prompt_chars() -> int:
    try:
        return int(os.environ.get("CLI_AGENT_MAX_PROMPT_CHARS", "120000"))
    except ValueError:
        return 120000


def messages_to_prompt(messages: List[BaseMessage]) -> str:
    """Flatten a LangChain message list into a single CLI prompt string."""
    parts: list[str] = []
    for msg in messages:
        role = getattr(msg, "type", None) or msg.__class__.__name__
        content = msg.content
        if isinstance(content, list):
            # multimodal blocks — keep text only
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    texts.append(block)
            content = "\n".join(texts)
        content = str(content or "")
        if isinstance(msg, SystemMessage) or role == "system":
            parts.append(f"[system]\n{content}")
        elif isinstance(msg, HumanMessage) or role == "human":
            parts.append(f"[user]\n{content}")
        elif isinstance(msg, AIMessage) or role == "ai":
            parts.append(f"[assistant]\n{content}")
        else:
            parts.append(f"[{role}]\n{content}")
    prompt = "\n\n".join(parts)
    max_chars = _max_prompt_chars()
    if len(prompt) > max_chars:
        prompt = prompt[-max_chars:]
        prompt = "...[truncated]\n" + prompt
    return prompt


class CliAgentChatModel(BaseChatModel):
    """Chat model that runs ``claude`` / ``agy`` / generic headless agents as the LLM."""

    agent: str = Field(default="claude", description="Driver name: claude | agy | generic")
    model_name: str = Field(default="", description="Optional model flag for the CLI")
    workdir: Optional[str] = Field(default=None)
    timeout_s: Optional[float] = Field(default=None)
    # Pool override (encrypted at rest). Never log / identify by value.
    api_key: Optional[str] = Field(default=None, exclude=True, repr=False)

    @property
    def _llm_type(self) -> str:
        return f"cli-agent-{self.agent}"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "model_name": self.model_name,
            "workdir": self.workdir,
            "timeout_s": self.timeout_s,
            "has_api_key": bool(self.api_key),
        }

    def _resolved_model(self) -> str | None:
        """CLI ``--model`` value, or None to let the binary pick its default."""
        m = normalize_cli_model_name(self.model_name)
        return m or None

    def _run_options(self) -> CliRunOptions:
        """Build CliRunOptions including pool auth env overrides."""
        auth_env = cli_auth_env_overrides(self.agent, self.api_key)
        return CliRunOptions(
            workdir=self.workdir,
            timeout_s=self.timeout_s,
            model=self._resolved_model(),
            env=auth_env or None,
        )
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            )
        # Sync path called from a running loop — bridge via a new thread.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(
                asyncio.run,
                self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs),
            )
            return fut.result()

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt = messages_to_prompt(messages)
        if stop:
            prompt += "\n\n[instruction]\nDo not emit any of these stop sequences: " + ", ".join(stop)

        opts = self._run_options()
        result = await run_cli_agent(prompt, agent=self.agent, options=opts)
        if result.status != "ok":
            err = result.error or result.status
            # Prefer extracted CLI result text when the driver only got "exit N"
            if result.text and (
                not err
                or err.startswith("exit ")
                or err == result.status
            ):
                err = result.text.strip() or err
            logger.warning("CliAgentChatModel agent=%s failed: %s", self.agent, err)
            # Surface a usable AIMessage so graph nodes can parse JSON failures
            text = f'{{"error": "cli_agent_failed", "message": {_json_str(err)}}}'
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

        text = result.text or result.raw_stdout or ""
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        # Headless CLI has no token stream — yield one chunk after full generation
        # so LangGraph astream_events / messages projections always see a message.
        result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        for gen in result.generations:
            content = getattr(gen.message, "content", "") or ""
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=content))
            if run_manager:
                run_manager.on_llm_new_token(content)
            yield chunk

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        result = await self._agenerate(messages, stop=stop, run_manager=None, **kwargs)
        for gen in result.generations:
            content = getattr(gen.message, "content", "") or ""
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=content))
            if run_manager:
                await run_manager.on_llm_new_token(content)
            yield chunk


def _json_str(s: str) -> str:
    import json
    return json.dumps(str(s))


def make_cli_chat_model(
    provider: str,
    model: str = "",
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> CliAgentChatModel:
    """Map LLM provider id to a CliAgentChatModel instance.

    ``api_key`` is a pool override: OAuth setup-token or Anthropic API key,
    injected into the CLI subprocess env (see ``cli_auth_env_overrides``).
    """
    p = (provider or "").strip().lower()
    agent = "claude"
    if p in ("agy-cli", "agy", "antigravity"):
        agent = "agy"
    elif p in ("grok-cli", "grok"):
        agent = "grok"
    elif p in ("claude-cli", "claude"):
        agent = "claude"
    elif p in ("cli-agent", "generic"):
        agent = (os.environ.get("CLI_AGENT_PROVIDER") or "claude").strip().lower()
        if agent in ("claude-cli",):
            agent = "claude"
        if agent in ("agy-cli", "antigravity"):
            agent = "agy"
        if agent in ("grok-cli",):
            agent = "grok"
    key = (api_key or "").strip() or None
    # Never pass provider ids as --model (Claude Code 404s on "claude-cli").
    return CliAgentChatModel(
        agent=agent,
        model_name=normalize_cli_model_name(model),
        workdir=base_url or None,
        api_key=key,
    )
