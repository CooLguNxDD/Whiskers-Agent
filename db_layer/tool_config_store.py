"""
tool_config_store — CRUD for the per-tool MCP-exposure toggle (core_017).

Distinct from ``route_embeddings.is_enabled`` (semantic-search routing): this
table decides whether an individual ``@mcp.tool()`` is registered with FastMCP
and therefore visible/callable by MCP clients. Independent of RAG — these reads
work even when DATABASE_URL has no embeddings populated.

Sparse storage: only disabled tools need a row. A missing row means enabled,
so ``set_tool_enabled(..., True)`` upserts ``is_enabled=TRUE`` rather than
deleting, keeping the toggle idempotent and auditable via ``updated_at``.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from db_layer.connection import get_async_session
from db_layer.models import ToolConfig

logger = logging.getLogger("whiskers")


async def get_disabled_tools() -> set[tuple[str, str]]:
    """Return ``(plugin_id, tool_name)`` pairs marked ``is_enabled=FALSE``.

    Mirrors ``job_producer._disabled_keys()`` so the startup gate and the
    toggle endpoints share one notion of "what is off".
    """
    async with get_async_session() as session:
        rows = await session.execute(
            select(ToolConfig.plugin_id, ToolConfig.tool_name).where(
                ToolConfig.is_enabled.is_(False)
            )
        )
        return {(r.plugin_id, r.tool_name) for r in rows.all()}


async def get_hidden_tools() -> set[tuple[str, str]]:
    """Return ``(plugin_id, tool_name)`` pairs marked ``is_hidden=TRUE``.

    Gateway (run_graph unified) mode: hidden tools are removed from MCP
    exposure but stay reachable internally by ``run_graph``. Distinct from
    :func:`get_disabled_tools` (which gates internal reachability too).
    """
    async with get_async_session() as session:
        rows = await session.execute(
            select(ToolConfig.plugin_id, ToolConfig.tool_name).where(
                ToolConfig.is_hidden.is_(True)
            )
        )
        return {(r.plugin_id, r.tool_name) for r in rows.all()}


async def get_tool_states(plugin_id: str) -> dict[str, bool]:
    """Map ``tool_name -> is_enabled`` for one plugin.

    Only tools with an explicit row appear; callers treat a missing tool as
    enabled (the table is sparse).
    """
    async with get_async_session() as session:
        rows = await session.execute(
            select(ToolConfig.tool_name, ToolConfig.is_enabled).where(
                ToolConfig.plugin_id == plugin_id
            )
        )
        return {r.tool_name: r.is_enabled for r in rows.all()}


async def get_tool_hidden_states(plugin_id: str) -> dict[str, bool]:
    """Map ``tool_name -> is_hidden`` for one plugin (sparse; absence = visible)."""
    async with get_async_session() as session:
        rows = await session.execute(
            select(ToolConfig.tool_name, ToolConfig.is_hidden).where(
                ToolConfig.plugin_id == plugin_id
            )
        )
        return {r.tool_name: r.is_hidden for r in rows.all()}


async def get_tool_embedding_models(plugin_id: str) -> dict[str, str | None]:
    """Map tool_name -> embedding_model ID for one plugin."""
    async with get_async_session() as session:
        rows = await session.execute(
            select(ToolConfig.tool_name, ToolConfig.embedding_model).where(
                ToolConfig.plugin_id == plugin_id,
                ToolConfig.embedding_model.is_not(None)
            )
        )
        return {r.tool_name: r.embedding_model for r in rows.all()}


async def get_tool_embedding_model(plugin_id: str, tool_name: str) -> str | None:
    """Get the configured embedding model pool entry ID for a tool, or None."""
    async with get_async_session() as session:
        row = await session.execute(
            select(ToolConfig.embedding_model).where(
                ToolConfig.plugin_id == plugin_id,
                ToolConfig.tool_name == tool_name
            )
        )
        res = row.fetchone()
        return res[0] if res else None


async def set_tool_embedding_model(plugin_id: str, tool_name: str, model_id: str | None) -> None:
    """Set the configured embedding model pool entry ID for a tool."""
    async with get_async_session() as session:
        stmt = (
            insert(ToolConfig)
            .values(
                plugin_id=plugin_id,
                tool_name=tool_name,
                embedding_model=model_id,
                updated_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=["plugin_id", "tool_name"],
                set_=dict(embedding_model=model_id, updated_at=func.now()),
            )
        )
        await session.execute(stmt)
        await session.commit()
    logger.info(
        "tool_config: %s.%s -> embedding_model=%s",
        plugin_id, tool_name, model_id,
    )


async def set_tool_enabled(plugin_id: str, tool_name: str, enabled: bool) -> None:
    """Upsert the enabled state for a single tool."""
    async with get_async_session() as session:
        stmt = (
            insert(ToolConfig)
            .values(
                plugin_id=plugin_id,
                tool_name=tool_name,
                is_enabled=enabled,
                updated_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=["plugin_id", "tool_name"],
                set_=dict(is_enabled=enabled, updated_at=func.now()),
            )
        )
        await session.execute(stmt)
        await session.commit()
    logger.info(
        "tool_config: %s.%s -> is_enabled=%s",
        plugin_id, tool_name, enabled,
    )


async def set_tool_hidden(plugin_id: str, tool_name: str, hidden: bool) -> None:
    """Upsert the gateway hidden state for a single tool."""
    async with get_async_session() as session:
        stmt = (
            insert(ToolConfig)
            .values(
                plugin_id=plugin_id,
                tool_name=tool_name,
                is_hidden=hidden,
                updated_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=["plugin_id", "tool_name"],
                set_=dict(is_hidden=hidden, updated_at=func.now()),
            )
        )
        await session.execute(stmt)
        await session.commit()
    logger.info(
        "tool_config: %s.%s -> is_hidden=%s",
        plugin_id, tool_name, hidden,
    )
