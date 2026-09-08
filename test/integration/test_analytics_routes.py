import pytest
from unittest.mock import AsyncMock, patch

import api.analytics_routes  # noqa: F401 - triggers route registration


class TestAnalyticsRoutes:
    @pytest.mark.asyncio
    async def test_analytics_summary_empty(self, client):
        # Truncate tool_call_events for a clean state
        from db_layer.connection import get_async_session
        from sqlalchemy import text
        async with get_async_session() as db:
            await db.execute(text("TRUNCATE TABLE tool_call_events RESTART IDENTITY CASCADE"))
            await db.commit()

        res = await client.get("/api/analytics/session_gated/summary?range=24h")
        assert res.status_code == 200
        data = res.json()
        assert "kpi" in data
        assert data["kpi"]["total_calls"] == 0
        assert data["kpi"]["success_rate"] == 100.0

        assert data["kpi"]["active_sessions"] == 0
        assert "series" in data
        assert len(data["series"]["core"]) == 24
        assert len(data["series"]["extensions"]) == 24
        assert len(data["series"]["other"]) == 24
        assert data["top_tools"] == []
        assert data["recent_errors"] == []
        assert data["top_models"] == []

    @pytest.mark.asyncio
    async def test_analytics_ws_ticket(self, client):
        # Minting a ticket when oauth is not enabled raises unless dev auth is active
        with patch("core.telemetry.ws_ticket.mint_analytics_ticket", AsyncMock(return_value="fake-ws-ticket")):
            res = await client.get("/api/analytics/session_gated/ws-ticket")
            assert res.status_code == 200
            data = res.json()
            assert "ws_ticket" in data
            assert data["ws_ticket"] == "fake-ws-ticket"
            assert "ws_url" in data
