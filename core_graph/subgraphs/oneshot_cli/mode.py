"""Oneshot CLI stack: single Mode-B subprocess for the whole turn."""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from core_graph.subgraphs.oneshot_cli.policy import agent_for_provider

logger = logging.getLogger("whiskers.core_graph.oneshot_cli")


async def run_cli_oneshot(
    user_message: str,
    *,
    provider: str,
    session_id: str | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    """Spawn the active CLI provider once for the whole turn.

    Returns a graph-style response envelope: ``{"status", "message", "meta"}``.
    ``bearer_token`` is accepted for call-site symmetry but not forwarded — the
    injector mints an admin token with ``ocat_source=goap_agent_cli``.
    """
    from core.llm_config_service import resolve_core_chat
    from core_graph.goap_agent.cli.runner import run_cli_agent_dict
    from core_graph.goap_agent.llm.cli_chat_model import (
        cli_auth_env_overrides,
        normalize_cli_model_name,
    )

    _ = bearer_token

    sel = await resolve_core_chat()
    agent = agent_for_provider(provider)
    model = normalize_cli_model_name(sel.get("model"))
    auth_env = cli_auth_env_overrides(agent, sel.get("api_key"))

    result = await run_cli_agent_dict(
        user_message,
        agent=agent,
        model=model or None,
        session_id=session_id,
        bearer_token=None,
        env=auth_env or None,
    )

    cli_meta = result.get("meta") or {}
    log_info = result.get("log")

    if result.get("status") != "ok":
        message = result.get("error") or result.get("text") or f"cli_agent_{result.get('status')}"
        return {
            "status": "error",
            "message": message,
            "meta": {
                "cli": {
                    "agent": agent,
                    "provider": provider,
                    "log": log_info,
                }
            },
        }

    return {
        "status": "ok",
        "message": result.get("text") or "",
        "meta": {
            "cli": {
                "agent": agent,
                "provider": provider,
                "session_id": cli_meta.get("session_id"),
                "total_cost_usd": cli_meta.get("total_cost_usd"),
                "usage": cli_meta.get("usage"),
                "model": cli_meta.get("model"),
                "log": log_info,
            }
        },
    }


async def stream_cli_oneshot(
    user_message: str,
    *,
    provider: str,
    session_id: str | None = None,
    bearer_token: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """SSE-shaped wrapper over ``run_cli_oneshot`` for streaming callers."""
    yield {"type": "values", "state": {"active_node": "cli_agent", "response": None}}
    try:
        envelope = await run_cli_oneshot(
            user_message,
            provider=provider,
            session_id=session_id,
            bearer_token=bearer_token,
        )
    except Exception as exc:
        logger.exception("stream_cli_oneshot failed")
        yield {"type": "error", "message": str(exc)}
        return

    if envelope.get("status") == "error":
        yield {"type": "values", "state": {"active_node": "cli_agent", "response": envelope}}
        yield {"type": "error", "message": envelope.get("message") or "cli_agent_failed"}
        return

    message = envelope.get("message") or ""
    if message:
        yield {"type": "token", "node": "cli_agent", "text": message}
    yield {"type": "values", "state": {"active_node": "cli_agent", "response": envelope}}
    yield {"type": "end"}
