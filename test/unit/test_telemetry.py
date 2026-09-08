"""Unit tests for the analytics telemetry collector and WS ticket helpers."""

import pytest
import asyncio
import time
from core.telemetry.ws_ticket import mint_analytics_ticket
from core.telemetry.collector import TelemetryCollector, LatencyReservoir


async def test_analytics_dev_ticket(monkeypatch):
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")
    monkeypatch.setattr("core.context.oauth_provider", None, raising=False)
    ticket = await mint_analytics_ticket("test-subject")
    assert ticket == "dev:test-subject:analytics:read"


async def test_analytics_oauth_disabled_without_dev_raises(monkeypatch):
    monkeypatch.delenv("CAT_TERMINAL_DEV_AUTH", raising=False)
    monkeypatch.setattr("core.context.oauth_provider", None, raising=False)
    with pytest.raises(RuntimeError):
        await mint_analytics_ticket("test-subject")


async def test_analytics_mints_via_oauth(monkeypatch):
    calls = {}

    class _FakeSvc:
        async def ensure_keypair(self):
            return "kid-2"

        async def ensure_internal_client(self, client_id, scopes="whiskers"):
            calls["client_id"] = client_id
            calls["client_scopes"] = scopes

        async def _mint_jwt(self, kid, subject, client_id, scopes, ttl, token_type="access"):
            calls.update(kid=kid, subject=subject, scopes=scopes, ttl=ttl, token_type=token_type)
            return ("jwt-token-2", "jti", None)

    class _FakeProvider:
        _svc = _FakeSvc()

    monkeypatch.setattr("core.context.oauth_provider", _FakeProvider(), raising=False)
    token = await mint_analytics_ticket("test-subject")
    assert token == "jwt-token-2"
    assert calls["subject"] == "test-subject"
    assert calls["scopes"] == ["analytics:read"]


def test_latency_reservoir():
    res = LatencyReservoir(size=5)
    for i in range(10):
        res.add(i)
    # Reservoir size is 5, so it should only have 5 samples
    assert len(res.samples) == 5
    # Percentiles
    assert res.get_percentile(0.5) == 7
    assert res.get_percentile(0.99) == 9


@pytest.mark.asyncio
async def test_collector_recording():

    coll = TelemetryCollector()
    coll.record_tool_call(
        tool="test.tool",
        plugin_id="test_plugin",
        model="test-model",
        latency_ms=120,
        ok=True,
        subject="test-user"
    )

    # Tenant stamped at record time from current_tenant_id ContextVar
    from core.context import current_tenant_id

    assert coll._tool_call_buffer[-1]["tenant_id"] == int(current_tenant_id.get() or 1)

    # Check memory states
    snap = coll.snapshot()
    assert snap["summary"]["total_calls"] == 1
    assert snap["summary"]["success_rate"] == 100.0
    assert snap["summary"]["p50_latency"] == 120
    assert snap["summary"]["p99_latency"] == 120
    assert len(snap["minute_buckets"]) == 1
    assert snap["minute_buckets"][0]["tool_calls"]["test_plugin"] == 1

    # Record error
    coll.record_tool_call(
        tool="test.tool",
        plugin_id="test_plugin",
        model="test-model",
        latency_ms=250,
        ok=False,
        error_type="ValueError",
        status_code=400,
        subject="test-user"
    )
    snap2 = coll.snapshot()
    assert snap2["summary"]["total_calls"] == 2
    assert snap2["summary"]["success_rate"] == 50.0
    assert len(snap2["recent_errors"]) == 1
    assert snap2["recent_errors"][0]["error_type"] == "ValueError"

    # Record relay traffic
    coll.record_relay_traffic("session_1", "console", 500)
    coll.record_relay_traffic("session_1", "extension", 1000)
    snap3 = coll.snapshot()
    assert snap3["relay"]["bytes_console"] == 500
    assert snap3["relay"]["bytes_ext"] == 1000
    assert snap3["relay"]["frames"] == 2


def test_snapshot_reports_registered_gauge():
    """A plugin-registered gauge shows up in the snapshot under its own key."""
    coll = TelemetryCollector()
    coll.register_gauge_provider("active_sessions", lambda: 3)
    coll.register_gauge_provider("custom_gauge", lambda: 7)
    snap = coll.snapshot()
    assert snap["active_sessions"] == 3
    assert snap["custom_gauge"] == 7


def test_snapshot_isolates_failing_gauge():
    """One raising provider must not zero the others (the old fail-open bug)."""
    def _boom():
        raise RuntimeError("provider down")

    coll = TelemetryCollector()
    coll.register_gauge_provider("active_sessions", _boom)
    coll.register_gauge_provider("custom_gauge", lambda: 5)
    snap = coll.snapshot()
    assert snap["active_sessions"] == 0
    assert snap["custom_gauge"] == 5


def test_snapshot_defaults_without_providers():
    """No relay plugin loaded → active_sessions stays 0, contract unchanged."""
    coll = TelemetryCollector()
    assert coll.snapshot()["active_sessions"] == 0


def test_unregister_gauge_provider():
    """Unload drops the gauge; unknown keys are a no-op."""
    coll = TelemetryCollector()
    coll.register_gauge_provider("active_sessions", lambda: 2)
    coll.unregister_gauge_provider("active_sessions")
    coll.unregister_gauge_provider("never_registered")
    assert coll.snapshot()["active_sessions"] == 0


def test_record_tool_call_defaults_feature_mcp():
    """feature defaults to 'mcp' so every pre-existing call site keeps working unmodified."""
    coll = TelemetryCollector()
    coll.record_tool_call(tool="t", plugin_id="p", model=None, latency_ms=10, ok=True)
    assert coll._tool_call_buffer[-1]["feature"] == "mcp"
    assert coll._tool_call_buffer[-1]["parent_run_id"] is None


def test_record_tool_call_tags_graph_dispatch():
    """A tool call made inside a graph run carries feature='mcp' + parent_run_id, not a graph row."""
    coll = TelemetryCollector()
    coll.record_tool_call(
        tool="t", plugin_id="p", model=None, latency_ms=10, ok=True,
        feature="mcp", parent_run_id="run-123",
    )
    assert coll._tool_call_buffer[-1]["feature"] == "mcp"
    assert coll._tool_call_buffer[-1]["parent_run_id"] == "run-123"
    assert coll._graph_run_count == 0


def test_record_graph_run():
    """One record_graph_run call is one row in the graph buffer and one graph KPI, distinct from MCP."""
    coll = TelemetryCollector()
    coll.record_graph_run(
        run_id="run-abc", mode="root", flow_id="portfolio_ask_v1", goal_class="scoped_ask",
        latency_ms=500, ok=True, step_count=4, failed_steps=0, terminal_status="done",
    )
    assert coll._graph_run_count == 1
    assert coll._graph_run_buffer[-1]["run_id"] == "run-abc"

    snap = coll.snapshot()
    assert snap["summary"]["graph"]["total_calls"] == 1
    assert snap["summary"]["graph"]["success_rate"] == 100.0
    assert snap["summary"]["mcp"]["total_calls"] == 0

    coll.record_graph_run(
        run_id="run-def", mode="root", flow_id=None, goal_class=None,
        latency_ms=200, ok=False, error_type="TimeoutError",
    )
    snap2 = coll.snapshot()
    assert snap2["summary"]["graph"]["total_calls"] == 2
    assert snap2["summary"]["graph"]["success_rate"] == 50.0
    # MCP series unaffected by graph-run recording (D4: no double-count)
    assert snap2["summary"]["mcp"]["total_calls"] == 0


def test_buffers_are_bounded():
    """Flush buffers drop oldest entries past _MAX_BUFFER_SIZE instead of growing unboundedly."""
    from core.telemetry.collector import _MAX_BUFFER_SIZE

    coll = TelemetryCollector()
    for i in range(_MAX_BUFFER_SIZE + 50):
        coll.record_tool_call(tool=f"t{i}", plugin_id="p", model=None, latency_ms=1, ok=True)
    assert len(coll._tool_call_buffer) == _MAX_BUFFER_SIZE
    # Oldest entries were dropped; the buffer holds the most recent ones.
    assert coll._tool_call_buffer[-1]["tool_name"] == f"t{_MAX_BUFFER_SIZE + 49}"


@pytest.mark.asyncio
async def test_flush_failure_requeues_oldest_first():
    """Failed flush restores chronology; overflow drops the oldest, not mid-flush events."""
    from collections import deque
    from unittest.mock import patch

    from core.telemetry.collector import TelemetryCollector

    coll = TelemetryCollector()
    coll._tool_call_buffer = deque(maxlen=3)
    for i in range(3):
        coll.record_tool_call(tool=f"old{i}", plugin_id="p", model=None, latency_ms=1, ok=True)

    class _FailingSession:
        def add(self, _obj):
            return None

        async def commit(self):
            coll.record_tool_call(tool="new", plugin_id="p", model=None, latency_ms=1, ok=True)
            raise RuntimeError("db down")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    with patch("core.telemetry.collector.get_async_session", return_value=_FailingSession()):
        await coll._flush_once()

    names = [ev["tool_name"] for ev in coll._tool_call_buffer]
    assert names == ["old1", "old2", "new"]
