"""
TelemetryStore — DB-backed reads of ``tool_call_events`` scoped to a single plugin.

Backs the plugin detail Logs tab (paginated tool-call history + status filter).
Distinct from ``api/analytics_routes.py`` which aggregates across all plugins
for the dashboard-wide analytics summary.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select, text

from db_layer.connection import get_async_session
from db_layer.models.telemetry import ToolCallEvent

logger = logging.getLogger("whiskers")


class TelemetryStore:
    """DB-backed store for querying tool-call telemetry."""

    async def ping(self) -> bool:
        """Lightweight DB connectivity probe (SELECT 1)."""
        try:
            async with get_async_session() as db:
                await db.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            logger.debug("telemetry_store.ping failed: %s", exc)
            return False

    async def kpi_last_24h(self, tenant_id: int) -> dict[str, float | int]:
        """Aggregate last-24h success rate and latency percentiles for a tenant."""
        start_time = datetime.now(timezone.utc) - timedelta(hours=24)
        async with get_async_session() as db:
            kpi_query = text("""
                SELECT
                    count(*)::int as total_calls,
                    sum(case when ok = true then 1 else 0 end)::int as success_calls,
                    coalesce(percentile_cont(0.50) within group (order by latency_ms), 0)::int as p50,
                    coalesce(percentile_cont(0.99) within group (order by latency_ms), 0)::int as p99
                FROM tool_call_events
                WHERE created_at >= :start_time AND tenant_id = :tenant_id
            """)
            row = (
                await db.execute(
                    kpi_query, {"start_time": start_time, "tenant_id": tenant_id}
                )
            ).mappings().first()
            if not row:
                return {
                    "success_rate": 0.0,
                    "p50_latency_ms": 0,
                    "p99_latency_ms": 0,
                }
            total_calls = row["total_calls"] or 0
            success_calls = row["success_calls"] or 0
            success_rate = 100.0 if not total_calls else (success_calls / total_calls * 100.0)
            return {
                "success_rate": round(success_rate, 2),
                "p50_latency_ms": int(row["p50"] or 0),
                "p99_latency_ms": int(row["p99"] or 0),
            }

    async def get_plugin_logs(
        self,
        plugin_id: str,
        page: int = 1,
        per_page: int = 25,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (items, total) of tool-call events for *plugin_id*, newest first.

        status: "ok" | "error" | None (no filter)
        """
        from core.context import current_tenant_id

        tenant_id = current_tenant_id.get()
        if tenant_id is None:
            tenant_id = 1  # fail closed to default tenant — never unscoped
        filters = [
            ToolCallEvent.plugin_id == plugin_id,
            ToolCallEvent.tenant_id == tenant_id,
        ]
        if status == "ok":
            filters.append(ToolCallEvent.ok.is_(True))
        elif status == "error":
            filters.append(ToolCallEvent.ok.is_(False))

        offset = max(page - 1, 0) * per_page

        async with get_async_session() as session:
            total = await session.scalar(
                select(func.count()).select_from(ToolCallEvent).where(and_(*filters))
            )
            rows = await session.execute(
                select(ToolCallEvent)
                .where(and_(*filters))
                .order_by(ToolCallEvent.created_at.desc())
                .limit(per_page)
                .offset(offset)
            )
            events = rows.scalars().all()

        items = [
            {
                "id": e.id,
                "tool_name": e.tool_name,
                "model": e.model,
                "latency_ms": e.latency_ms,
                "ok": e.ok,
                "error_type": e.error_type,
                "status_code": e.status_code,
                "subject": e.subject,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
        return items, int(total or 0)


telemetry_store = TelemetryStore()
