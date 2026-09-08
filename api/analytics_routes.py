"""



REST and WebSocket routes for proxy and tool call analytics.

Endpoints
---------
GET    /api/analytics/session_gated/summary     — Retrieve historical aggregated analytics metrics based on range parameter.
GET    /api/analytics/session_gated/ws-ticket   — Mint short-lived WebSocket access ticket for analytics.
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from utils.error_response import safe_error_response
from starlette.websockets import WebSocket, WebSocketDisconnect

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from db_layer import analytics_store

logger = logging.getLogger("whiskers.telemetry")

# plugin_id values recorded by core (non-plugin) code paths — grouped into the
# dashboard's "core" traffic series rather than "extensions".
_CORE_PLUGIN_IDS = ("core", "whiskers_core", "core_plugin", "whiskers")


def _analytics_ws_url() -> str:
    """Build the analytics WS URL from MCP_SERVER_URL."""
    base = os.environ.get("MCP_SERVER_URL", "http://localhost:10000")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base.rstrip('/')}/api/analytics/none/ws"


@http_route_registry.route(
    route="analytics",
    endpoint="summary",
    methods=["GET"],
    name="api_analytics_summary",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.analytics:read",),
    owner="api.analytics",
)
async def get_analytics_summary(request: Request) -> Response:
    """Retrieve historical aggregated analytics metrics based on range parameter."""
    from core.context import current_tenant_id

    range_param = request.query_params.get("range", "24h")
    tenant_id = current_tenant_id.get()
    if tenant_id is None:
        tenant_id = 1

    now = datetime.now(timezone.utc)
    if range_param == "1h":
        start_time = now - timedelta(hours=1)
        bucket_width = 3600 / 24  # 150s
    elif range_param == "7d":
        start_time = now - timedelta(days=7)
        bucket_width = 7 * 86400 / 24
    elif range_param == "30d":
        start_time = now - timedelta(days=30)
        bucket_width = 30 * 86400 / 24
    else:  # "24h"
        start_time = now - timedelta(hours=24)
        bucket_width = 86400 / 24  # 3600s

    try:
        # 1. KPI Cards data
        kpi_res = await analytics_store.get_kpi_metrics(start_time, tenant_id)
        if not kpi_res:
            kpi_res = {"total_calls": 0, "success_calls": 0, "p50": 0, "p99": 0}

        total_calls = kpi_res["total_calls"] or 0
        success_calls = kpi_res["success_calls"] or 0
        p50 = kpi_res["p50"] or 0
        p99 = kpi_res["p99"] or 0
        success_rate = 100.0 if not total_calls else (success_calls / total_calls * 100.0)

        # 2. Time-series chart buckets (exactly 24 buckets)
        series_res = await analytics_store.get_series_buckets(start_time, bucket_width, tenant_id)

        core_series = [0] * 24
        extensions_series = [0] * 24
        other_series = [0] * 24

        for row in series_res:
            b = row["bucket"]
            if 0 <= b < 24:
                pid = row["plugin_id"]
                cnt = row["cnt"]
                if pid in _CORE_PLUGIN_IDS:
                    core_series[b] += cnt
                elif pid.endswith("_plugin"):
                    extensions_series[b] += cnt
                else:
                    other_series[b] += cnt

        # 3. Top Tools
        tools_res = await analytics_store.get_top_tools(start_time, tenant_id, limit=10)

        top_tools = []
        if tools_res:
            top_tool_names = [t["tool_name"] for t in tools_res]

            # Sparkline trend (10 buckets)
            trend_bucket_width = (now - start_time).total_seconds() / 10
            trend_res = await analytics_store.get_tool_trends(
                start_time, trend_bucket_width, list(top_tool_names), tenant_id
            )

            tool_trends = {name: [0] * 10 for name in top_tool_names}
            for row in trend_res:
                b = row["bucket"]
                if 0 <= b < 10:
                    tool_trends[row["tool_name"]][b] = row["cnt"]

            for t in tools_res:
                top_tools.append({
                    "name": t["tool_name"],
                    "calls": t["calls"],
                    "trend": tool_trends[t["tool_name"]],
                    "p99": f"{t['p99']}ms"
                })

        # 4. Recent Errors
        errors_res = await analytics_store.get_recent_errors(start_time, tenant_id, limit=20)
        recent_errors = []
        for row in errors_res:
            time_diff = now - row["created_at"]
            if time_diff.total_seconds() < 60:
                when = "just now"
            elif time_diff.total_seconds() < 3600:
                when = f"{int(time_diff.total_seconds() / 60)}m ago"
            elif time_diff.total_seconds() < 86400:
                when = f"{int(time_diff.total_seconds() / 3600)}h ago"
            else:
                when = f"{int(time_diff.total_seconds() / 86400)}d ago"

            recent_errors.append({
                "code": str(row["status_code"]) if row["status_code"] else (row["error_type"] or "500"),
                "src": row["plugin_id"],
                "msg": f"{row['tool']} · {row['error_type'] or 'error'}",
                "when": when
            })

        # 5. Top Callers (Models)
        models_res = await analytics_store.get_top_models(start_time, tenant_id)
        total_model_calls = sum(m["calls"] for m in models_res)
        top_models = []
        for m in models_res:
            pct = 0 if not total_model_calls else int(m["calls"] / total_model_calls * 100)
            top_models.append({
                "name": m["model"],
                "calls": m["calls"],
                "pct": pct
            })

        # Active sessions: read via the collector's gauge-provider registry, never
        # reach into a plugin's internals directly (core never imports a plugin for
        # a metric — see collector.register_gauge_provider / CLAUDE.md §Telemetry).
        from core.telemetry.collector import collector as _collector
        active_sessions = _collector.snapshot().get("active_sessions", 0)

        # 6. Graph-run KPI + series (feature="graph" axis — whole agent runs,
        # distinct from the MCP tool-call series above; see Phase 1 of the
        # telemetry plan). Tool calls dispatched inside a run stay in the MCP
        # series above via parent_run_id and are not summed here.
        graph_kpi_res = await analytics_store.get_graph_run_kpi(start_time, tenant_id)
        if not graph_kpi_res:
            graph_kpi_res = {"total_calls": 0, "success_calls": 0, "p50": 0, "p99": 0}
        graph_total = graph_kpi_res["total_calls"] or 0
        graph_success = graph_kpi_res["success_calls"] or 0
        graph_success_rate = 100.0 if not graph_total else (graph_success / graph_total * 100.0)

        graph_series_res = await analytics_store.get_graph_series_buckets(start_time, bucket_width, tenant_id)
        graph_series = [0] * 24
        for row in graph_series_res:
            b = row["bucket"]
            if 0 <= b < 24:
                graph_series[b] += row["cnt"]

        return JSONResponse({
            "kpi": {
                "total_calls": total_calls,
                "success_rate": round(success_rate, 2),
                "p50_latency": p50,
                "p99_latency": p99,
                "active_sessions": active_sessions
            },
            "graph_kpi": {
                "total_calls": graph_total,
                "success_rate": round(graph_success_rate, 2),
                "p50_latency": graph_kpi_res["p50"] or 0,
                "p99_latency": graph_kpi_res["p99"] or 0,
            },
            "series": {
                "core": core_series,
                "extensions": extensions_series,
                "other": other_series,
                "mcp": [c + e + o for c, e, o in zip(core_series, extensions_series, other_series)],
                "graph": graph_series,
            },
            "top_tools": top_tools,
            "recent_errors": recent_errors,
            "top_models": top_models
        })
            
    except Exception as exc:
        return safe_error_response(exc, log_ctx="Failed to build analytics summary")


@http_route_registry.route(
    route="analytics",
    endpoint="ws-ticket",
    methods=["GET"],
    name="api_analytics_ws_ticket",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.analytics:read",),
    owner="api.analytics",
)
async def get_analytics_ws_ticket(request: Request) -> Response:
    """Mint short-lived WebSocket access ticket for analytics."""
    subject = "console"
    try:
        session_token = request.cookies.get("session")
        if session_token:
            from core.context import oauth_provider
            if oauth_provider is not None and oauth_provider._svc is not None:
                payload = await oauth_provider._svc.validate_token(session_token)
                subject = payload.get("sub") or "console"
    except Exception:
        logger.debug("analytics_routes.py: swallowed exception", exc_info=True)
        
    try:
        from core.telemetry.ws_ticket import mint_analytics_ticket
        ticket = await mint_analytics_ticket(subject)
        return JSONResponse({
            "ws_url": _analytics_ws_url(),
            "ws_ticket": ticket
        })
    except Exception as exc:
        return safe_error_response(exc, log_ctx="Failed to mint analytics WS ticket")


async def analytics_ws(websocket: WebSocket) -> None:
    """WebSocket endpoint pushing real-time telemetry updates to subscribed consoles."""
    from core.telemetry.auth import authenticate_handshake
    
    subject = await authenticate_handshake(websocket, required_scope="analytics:read")
    if not subject:
        logger.info("analytics WS rejected (no valid analytics:read ticket): %s", websocket.client)
        await websocket.close(code=4001)
        return
        
    await websocket.accept()
    
    from core.telemetry.collector import collector
    collector.subscribe(websocket)
    
    try:
        # Send initial snapshot immediately
        snap = collector.snapshot()
        await websocket.send_json(snap)
        
        while True:
            # We don't expect messages from client, but read to detect disconnect.
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("Analytics WS error: %s", exc)
    finally:
        collector.unsubscribe(websocket)


# Register WS route
http_route_registry.register_ws_route(
    "/api/analytics/none/ws",
    analytics_ws,
    name="analytics_ws",
    auth_policy=AuthPolicy.NONE,
)
