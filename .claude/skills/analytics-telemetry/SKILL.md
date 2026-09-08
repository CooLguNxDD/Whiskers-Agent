---
name: analytics-telemetry
description: '**CODE PATTERN SKILL** — Analytics & Telemetry monitoring pattern for Whiskers Agent server. USE FOR: understanding telemetry collector lifecycle, recording new tool calls, measuring latency, recording relay session events, contributing new analytics REST or WebSocket routes, verifying telemetry database schema and migrations. DO NOT USE FOR: general OAuth flow, general SQLAlchemy setup, general React frontend routes.'
argument-hint: 'Optional: specify a file or plugin folder to audit'
---

# Analytics & Telemetry monitoring pattern

## Overview

The Whiskers Agent server telemetry system tracks:
1. **MCP Tool Call Events**: Captures tool name, plugin ID, latency, success status, resolved model, status code, and caller subject.
2. **Terminal Relay Session Events**: Tracks open/close session lifecycle events including active session durations and total byte throughput.
3. **Relay bytes/traffic**: Pumps bytes/frame counts directly into memory minute-buckets for real-time visualization.
4. **Step-Up Elevation events**: Audits TOTP/Password elevation success or failure statuses.

The architecture uses an in-memory `TelemetryCollector` singleton which buffers writes and periodically flushes them to Postgres using an async background task to keep the hot execution paths non-blocking. It also maintains real-time websocket connections to push snapshots of metrics.

---

## Correct Patterns

### 1. Recording MCP Tool Calls
To record telemetry for a tool call (always wrap in try/except and never let it raise):
```python
try:
    from core.telemetry import collector
    collector.record_tool_call(
        tool=tool_name,
        plugin_id=plugin_id,
        model=resolved_model_name,
        latency_ms=latency,
        ok=True,
        subject=user_subject,
        # feature defaults to "mcp" — only pass "graph" logic via record_graph_run
        # below, never here. If this call is a tool dispatched *inside* a graph
        # run, still call record_tool_call (unchanged) but pass parent_run_id so
        # it correlates without being double-counted as a graph KPI.
        parent_run_id=None,
    )
except Exception:
    pass
```

### 1b. Recording a whole graph run (feature="graph" axis)
One row per completed agent run, distinct from the individual tool calls dispatched
inside it (1 above). Real call sites: `core_graph/mcp_tool.py::_stream_graph_impl_inner`
(the MCP `ctx`-streamed path — dominant, since real traffic carries a Context) and
`run_graph_impl`'s headless `graph.ainvoke` fallback. **Not**
`core_graph/runtime/mode_router.py::_run_root`, which the current stack-selection
wiring never actually reaches for a request already classified "root" — it exists
for direct/test callers of `mode_router.run()` only.
```python
try:
    from core.telemetry import collector
    collector.record_graph_run(
        run_id=session_id or "unknown",
        mode="root",
        flow_id=result.get("flow_id"),
        goal_class=result.get("goal_class"),
        latency_ms=latency_ms,
        ok=True,
        step_count=len(result.get("step_results") or []),
        terminal_status=(result.get("response") or {}).get("status"),
    )
except Exception:
    pass
```

### 2. Recording Relay Session Lifecycle
When a session starts:
```python
try:
    from core.telemetry import collector
    collector.record_relay_session(
        event="open",
        subject=subject,
        ide_id=ide_id,
        session_id=session_id
    )
except Exception:
    pass
```

When a session ends:
```python
try:
    from core.telemetry import collector
    collector.record_relay_session(
        event="close",
        subject=session.subject,
        ide_id=session.ide_id,
        session_id=session_id,
        bytes_total=session.bytes_total,
        duration_s=duration
    )
except Exception:
    pass
```

### 3. Live Analytics WS handshake & reconnect
The analytics live-stream WS (`api/analytics_routes.py::analytics_ws`) authenticates
**pre-accept** via `core/telemetry/auth.py::authenticate_handshake` (60s `analytics:read`
ticket from `/api/analytics/ws-ticket`). On failure it returns `None` and the endpoint
`websocket.close(code=4001)` **before** `accept()` — uvicorn surfaces this as an opaque
`connection rejected (403 Forbidden)`. Each rejection now logs its cause on the
`whiskers.telemetry` logger: `authenticate_handshake` logs the no-token / token-invalid /
missing-scope branch, and `analytics_ws` logs `analytics WS rejected (...)` with the
client before closing. So a 403 in the logs always has a preceding cause line. The
frontend (`hooks/useAnalytics.ts::useTunnelMetrics`) reconnects with capped
exponential backoff + jitter (`nextReconnectDelay`, reset on `onopen`) instead of a fixed
5s loop, so a server-restart reconnect race produces one quiet retry, not a 403 storm.

### 4. Admin console tab map
`frontend/cat-admin-frontend` `/analytics` is URL-driven (`?tab=` + `?range=`):

| Tab | Component | Data |
|---|---|---|
| `overview` | `OverviewTab` | overlay KPIs + `McpGraphChart` (MCP vs graph, dual Y-axis) + top-4 tools + health |
| `tools` | `ToolsTrafficTab` | `PluginStackedChart` + searchable tools `Table` + CSV + top callers |
| `ask_turns` | `AskTurnsTab` | `useAskTurns(20, intent)` intent chips + client search `Table` |
| `errors` | `ErrorsHealthTab` | overlay error stream, client-side filter |

Live WS snapshots merge in `components/analytics/overlay.ts`. `useInvalidateSummaryOnLiveChange(range, liveMetrics)` shares `analyticsSummaryQueryKey(range)` with the summary query (10s throttle).

---

## DB Tables

- `tool_call_events`: Raw history of tool call metrics (id, tool_name, plugin_id, model, latency_ms, ok, error_type, status_code, subject, tenant_id, **feature** — `'mcp'` default, **parent_run_id** — set when the call happened inside a graph run, created_at)
- `graph_run_events`: One row per completed graph run — the `feature="graph"` KPI axis, distinct from the tool calls dispatched inside it (run_id, tenant_id, subject, mode, flow_id, goal_class, ok, error_type, terminal_status, latency_ms, step_count, failed_steps, created_at). `api/analytics_routes.py`'s summary response exposes both series (`series.mcp` / `series.graph`, plus `kpi` / `graph_kpi`) alongside the legacy `core`/`extensions`/`other` breakdown, which stays for backward compat. `telemetry_ttl_sweeper` deletes stale `graph_run_events` in `_BATCH=5000` chunks (tick returns `True` while a batch landed). A failed `_flush_once` requeues the failed batch in front of mid-flush arrivals so the bounded deque drops oldest, not newest.
- `relay_session_events`: Raw history of session events (id, event, session_id, subject, ide_id, bytes_total, duration_s, created_at)

## Plugin-owned domain logs (third tier)

Core telemetry above answers "is the platform healthy"; it cannot answer "who asked
what" for a specific plugin surface. The pattern for that is a **plugin-owned dual
write**, not a core table — see `plugins/portfolio_plugin/bake/telemetry.py::record_bake_run`
(the original) and `plugins/portfolio_plugin/ask/telemetry.py::record_ask_turn` (mirrors
it for fish-tank ask turns → `portfolio_ask_turns`). Both:
- write the in-memory aggregate via `collector.record_tool_call` (swallowed at `logger.debug`)
- **and** a durable per-attempt row via a plugin `store.py` function (swallowed at `logger.warning` — losing the audit row is worse than losing the aggregate)
- stamp `tenant_id` explicitly at the call site (bake: from `BakeContext`; ask: from
  `current_tenant_id.get()`, unauthenticated-safe since it's a public visitor path)
- clip visitor-controlled strings to VARCHAR widths (`ASK_TURN_*_MAX` in `models.py`,
  migration `0008_ask_turn_column_widths`) so a public ask cannot drop the audit row
- never persist the actual content being produced (bake: no layout; ask: no overlay
  blocks/answer text) — only the request and outcome

Also not yet documented above: `record_elevation` (`core/telemetry/collector.py`) audits
TOTP/password step-up success/failure the same way as tool calls.
