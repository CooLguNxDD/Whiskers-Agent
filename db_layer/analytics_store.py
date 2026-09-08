"""
analytics_store — data-access helpers for tool_call_events analytics queries.

Route handlers in api/analytics_routes.py own HTTP shaping; this module owns
the parameterized SQL against tool_call_events. Every query is tenant-scoped
(tenant_id is a required param — never allowed to default to None).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import select, func, case, cast, Integer, desc
from db_layer.models.telemetry import ToolCallEvent, GraphRunEvent
from db_layer.connection import get_async_session

logger = logging.getLogger("whiskers")


def _filter_base(stmt, start_time: datetime, tenant_id: int):
    return stmt.where(
        ToolCallEvent.created_at >= start_time,
        ToolCallEvent.tenant_id == tenant_id
    )


def _filter_graph(stmt, start_time: datetime, tenant_id: int):
    return stmt.where(
        GraphRunEvent.created_at >= start_time,
        GraphRunEvent.tenant_id == tenant_id
    )


async def get_graph_run_kpi(start_time: datetime, tenant_id: int) -> dict[str, Any] | None:
    """Return aggregate graph-run KPI row (total/success runs, p50/p99) since start_time.

    Counterpart to get_kpi_metrics() but over graph_run_events — the feature="graph"
    axis (one row per whole agent run, not per tool call inside it).
    """
    stmt = select(
        cast(func.count(), Integer).label("total_calls"),
        cast(func.sum(case((GraphRunEvent.ok, 1), else_=0)), Integer).label("success_calls"),
        cast(func.coalesce(func.percentile_cont(0.50).within_group(GraphRunEvent.latency_ms), 0), Integer).label("p50"),
        cast(func.coalesce(func.percentile_cont(0.99).within_group(GraphRunEvent.latency_ms), 0), Integer).label("p99")
    )
    stmt = _filter_graph(stmt, start_time, tenant_id)

    async with get_async_session() as db:
        result = await db.execute(stmt)
        row = result.mappings().first()
        return dict(row) if row else None


async def get_graph_series_buckets(
    start_time: datetime,
    bucket_width: float,
    tenant_id: int,
) -> Sequence[Any]:
    """Return per-bucket graph-run counts for the time-series chart's 'graph' series."""
    bucket_expr = cast(
        func.floor(
            func.extract('epoch', GraphRunEvent.created_at - start_time) / bucket_width
        ), Integer
    ).label("bucket")

    stmt = select(
        bucket_expr,
        cast(func.count(), Integer).label("cnt")
    )
    stmt = _filter_graph(stmt, start_time, tenant_id)
    stmt = stmt.group_by(bucket_expr).order_by(bucket_expr)

    async with get_async_session() as db:
        result = await db.execute(stmt)
        return result.mappings().all()


async def get_kpi_metrics(start_time: datetime, tenant_id: int) -> dict[str, Any] | None:
    """Return aggregate KPI row (total/success calls, p50/p99) since start_time."""
    stmt = select(
        cast(func.count(), Integer).label("total_calls"),
        cast(func.sum(case((ToolCallEvent.ok, 1), else_=0)), Integer).label("success_calls"),
        cast(func.coalesce(func.percentile_cont(0.50).within_group(ToolCallEvent.latency_ms), 0), Integer).label("p50"),
        cast(func.coalesce(func.percentile_cont(0.99).within_group(ToolCallEvent.latency_ms), 0), Integer).label("p99")
    )
    stmt = _filter_base(stmt, start_time, tenant_id)

    async with get_async_session() as db:
        result = await db.execute(stmt)
        row = result.mappings().first()
        return dict(row) if row else None


async def get_series_buckets(
    start_time: datetime,
    bucket_width: float,
    tenant_id: int,
) -> Sequence[Any]:
    """Return per-bucket plugin call counts for the time-series chart."""
    bucket_expr = cast(
        func.floor(
            func.extract('epoch', ToolCallEvent.created_at - start_time) / bucket_width
        ), Integer
    ).label("bucket")

    stmt = select(
        bucket_expr,
        ToolCallEvent.plugin_id,
        cast(func.count(), Integer).label("cnt")
    )
    stmt = _filter_base(stmt, start_time, tenant_id)
    stmt = stmt.group_by(bucket_expr, ToolCallEvent.plugin_id).order_by(bucket_expr)

    async with get_async_session() as db:
        result = await db.execute(stmt)
        return result.mappings().all()


async def get_top_tools(start_time: datetime, tenant_id: int, limit: int = 10) -> Sequence[Any]:
    """Return top tools by call count with p99 latency since start_time."""
    calls_expr = cast(func.count(), Integer).label("calls")

    stmt = select(
        ToolCallEvent.tool_name,
        calls_expr,
        cast(func.coalesce(func.percentile_cont(0.99).within_group(ToolCallEvent.latency_ms), 0), Integer).label("p99")
    )
    stmt = _filter_base(stmt, start_time, tenant_id)
    stmt = stmt.group_by(ToolCallEvent.tool_name).order_by(desc(calls_expr)).limit(limit)

    async with get_async_session() as db:
        result = await db.execute(stmt)
        return result.mappings().all()


async def get_tool_trends(
    start_time: datetime,
    trend_bucket_width: float,
    tool_names: list[str],
    tenant_id: int,
) -> Sequence[Any]:
    """Return per-tool sparkline bucket counts for the given tool names."""
    bucket_expr = cast(
        func.floor(
            func.extract('epoch', ToolCallEvent.created_at - start_time) / trend_bucket_width
        ), Integer
    ).label("bucket")

    stmt = select(
        ToolCallEvent.tool_name,
        bucket_expr,
        cast(func.count(), Integer).label("cnt")
    )
    stmt = _filter_base(stmt, start_time, tenant_id)
    stmt = stmt.where(ToolCallEvent.tool_name.in_(tool_names))
    stmt = stmt.group_by(ToolCallEvent.tool_name, bucket_expr)

    async with get_async_session() as db:
        result = await db.execute(stmt)
        return result.mappings().all()


async def get_recent_errors(start_time: datetime, tenant_id: int, limit: int = 20) -> Sequence[Any]:
    """Return recent failed tool_call_events rows since start_time."""
    stmt = select(
        ToolCallEvent.tool_name.label("tool"),
        ToolCallEvent.plugin_id,
        ToolCallEvent.error_type,
        ToolCallEvent.status_code,
        ToolCallEvent.created_at
    )
    stmt = _filter_base(stmt, start_time, tenant_id)
    stmt = stmt.where(ToolCallEvent.ok.is_(False)).order_by(desc(ToolCallEvent.created_at)).limit(limit)

    async with get_async_session() as db:
        result = await db.execute(stmt)
        return result.mappings().all()


async def get_top_models(start_time: datetime, tenant_id: int) -> Sequence[Any]:
    """Return model call counts since start_time (non-null model only)."""
    calls_expr = cast(func.count(), Integer).label("calls")

    stmt = select(
        ToolCallEvent.model,
        calls_expr
    )
    stmt = _filter_base(stmt, start_time, tenant_id)
    stmt = stmt.where(ToolCallEvent.model.is_not(None))
    stmt = stmt.group_by(ToolCallEvent.model).order_by(desc(calls_expr))

    async with get_async_session() as db:
        result = await db.execute(stmt)
        return result.mappings().all()
