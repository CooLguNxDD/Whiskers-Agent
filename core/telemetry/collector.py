"""Telemetry collector for real-time monitoring and historical DB persistence of MCP tool calls and relay sessions."""

import asyncio
import logging
import time
import math
import json
from collections import deque
from typing import Callable
from starlette.websockets import WebSocket
from db_layer.connection import get_async_session
from db_layer.models import ToolCallEvent, RelaySessionEvent, GraphRunEvent

logger = logging.getLogger("whiskers.telemetry")

# Cap on in-memory flush buffers so a stalled/unreachable DB degrades to
# dropping the oldest unflushed events instead of growing without bound.
_MAX_BUFFER_SIZE = 5000


def _requeue_failed_flush(buf: deque, old_events: list) -> None:
    """Put a failed flush batch back in front of events that arrived mid-await.

    ``extend(old)`` appends on the right, so ``maxlen`` then drops the *newer*
    mid-flush events from the left. ``extendleft`` on a full deque evicts from
    the right (newest). Rebuild oldest→newest and ``extend`` so overflow
    trims the left.
    """
    restored = list(old_events) + list(buf)
    buf.clear()
    buf.extend(restored)


class LatencyReservoir:
    """Bounded reservoir for p50/p99 latency sampling."""

    def __init__(self, size: int = 100) -> None:
        """Initialize the latency reservoir with a target size and empty sample list."""
        self.size = size
        self.samples: list[int] = []
        self._index = 0

    def add(self, sample: int) -> None:
        """Add a latency sample to the reservoir."""
        if len(self.samples) < self.size:
            self.samples.append(sample)
        else:
            self.samples[self._index] = sample
            self._index = (self._index + 1) % self.size

    def get_percentile(self, p: float) -> int:
        """Calculate the p-th percentile of latency samples."""
        if not self.samples:
            return 0
        sorted_samples = sorted(self.samples)
        idx = max(0, min(len(sorted_samples) - 1, math.floor(p * len(sorted_samples))))
        return sorted_samples[idx]


class TelemetryCollector:
    """TelemetryCollector singleton tracking metrics in memory and flushing to DB in background."""

    def __init__(self) -> None:
        """Initialize TelemetryCollector with locks, stats collections, and event buffers."""
        self._lock = asyncio.Lock()
        
        # Live counters (in-memory only)
        self._tool_calls: dict[str, int] = {}
        self._tool_errors: dict[str, int] = {}
        self._tool_latencies: dict[str, LatencyReservoir] = {}
        self._model_calls: dict[str, int] = {}
        self._recent_errors: deque = deque(maxlen=50)

        # Graph-run live counters (feature="graph" KPI axis, separate from MCP tool calls)
        self._graph_run_count = 0
        self._graph_run_errors = 0
        self._graph_run_latencies = LatencyReservoir()
        
        # Relay live totals
        self._relay_bytes_console = 0
        self._relay_bytes_ext = 0
        self._relay_frames = 0
        self._relay_session_open_count = 0
        self._relay_session_close_count = 0
        
        # Per-minute time buckets (ring of last 120 mins)
        self._minute_buckets: dict[int, dict] = {}
        
        # Subscriptions & Buffers (bounded — see _MAX_BUFFER_SIZE)
        self._subscribers: set[WebSocket] = set()
        self._tool_call_buffer: deque = deque(maxlen=_MAX_BUFFER_SIZE)
        self._relay_session_buffer: deque = deque(maxlen=_MAX_BUFFER_SIZE)
        self._graph_run_buffer: deque = deque(maxlen=_MAX_BUFFER_SIZE)
        
        # Gauge providers registered by plugins (core never imports plugins for metrics)
        self._gauge_providers: dict[str, Callable[[], int | float]] = {}
        
        # Tasks
        self._flush_task: asyncio.Task | None = None
        self._broadcast_task: asyncio.Task | None = None
        self._started = False

    def record_tool_call(
        self,
        tool: str,
        plugin_id: str,
        model: str | None,
        latency_ms: int,
        ok: bool,
        error_type: str | None = None,
        status_code: int | None = None,
        subject: str | None = None,
        feature: str = "mcp",
        parent_run_id: str | None = None,
    ) -> None:
        """Record an MCP tool call telemetry event.

        ``feature`` distinguishes standalone MCP calls ("mcp", the default) from
        tool dispatch happening *inside* a graph run — those still record here
        (so per-tool latency/error stats stay accurate) but are tagged with
        ``parent_run_id`` and must not be double-counted as a graph-run KPI;
        the run itself is recorded once via :meth:`record_graph_run`.
        """
        # Increment memory metrics
        self._tool_calls[tool] = self._tool_calls.get(tool, 0) + 1
        if not ok:
            self._tool_errors[tool] = self._tool_errors.get(tool, 0) + 1
            self._recent_errors.append({
                "tool": tool,
                "plugin_id": plugin_id,
                "error_type": error_type or "unknown",
                "status_code": status_code,
                "timestamp": time.time(),
            })
            
        if tool not in self._tool_latencies:
            self._tool_latencies[tool] = LatencyReservoir()
        self._tool_latencies[tool].add(latency_ms)
        
        if model:
            self._model_calls[model] = self._model_calls.get(model, 0) + 1
            
        # Update minute bucket
        now_sec = time.time()
        minute_epoch = int(now_sec // 60)
        self._ensure_minute_bucket(minute_epoch)
        self._minute_buckets[minute_epoch]["tool_calls"][plugin_id] = (
            self._minute_buckets[minute_epoch]["tool_calls"].get(plugin_id, 0) + 1
        )
        
        # Stamp tenant at record time (flush runs in a background task).
        try:
            from core.context import current_tenant_id
            tenant_id = int(current_tenant_id.get() or 1)
        except Exception:
            tenant_id = 1

        # Append to flush buffer
        self._tool_call_buffer.append({
            "tool_name": tool,
            "plugin_id": plugin_id,
            "model": model,
            "latency_ms": latency_ms,
            "ok": ok,
            "error_type": error_type,
            "status_code": status_code,
            "subject": subject,
            "tenant_id": tenant_id,
            "feature": feature,
            "parent_run_id": parent_run_id,
        })

        self.ensure_started()

    def record_graph_run(
        self,
        *,
        run_id: str,
        mode: str | None,
        flow_id: str | None,
        goal_class: str | None,
        latency_ms: int,
        ok: bool,
        step_count: int | None = None,
        failed_steps: int | None = None,
        terminal_status: str | None = None,
        error_type: str | None = None,
        subject: str | None = None,
    ) -> None:
        """Record one completed graph run (feature="graph" KPI, one row per run).

        Tool calls dispatched inside the run are recorded separately via
        ``record_tool_call(..., feature="mcp", parent_run_id=run_id)`` and must
        not be summed into this counter (see record_tool_call docstring).
        """
        self._graph_run_count += 1
        if not ok:
            self._graph_run_errors += 1
        self._graph_run_latencies.add(latency_ms)

        now_sec = time.time()
        minute_epoch = int(now_sec // 60)
        self._ensure_minute_bucket(minute_epoch)
        self._minute_buckets[minute_epoch]["graph_runs"] = (
            self._minute_buckets[minute_epoch].get("graph_runs", 0) + 1
        )

        try:
            from core.context import current_tenant_id
            tenant_id = int(current_tenant_id.get() or 1)
        except Exception:
            tenant_id = 1

        self._graph_run_buffer.append({
            "run_id": run_id,
            "tenant_id": tenant_id,
            "subject": subject,
            "mode": mode,
            "flow_id": flow_id,
            "goal_class": goal_class,
            "ok": ok,
            "error_type": error_type,
            "terminal_status": terminal_status,
            "latency_ms": latency_ms,
            "step_count": step_count,
            "failed_steps": failed_steps,
        })

        self.ensure_started()

    def record_relay_traffic(self, session_id: str, leg: str, nbytes: int) -> None:
        """Record bytes and frames pumped through terminal relay."""
        if leg == "console":
            self._relay_bytes_console += nbytes
        elif leg == "extension":
            self._relay_bytes_ext += nbytes
        self._relay_frames += 1
        
        # Update minute bucket
        now_sec = time.time()
        minute_epoch = int(now_sec // 60)
        self._ensure_minute_bucket(minute_epoch)
        self._minute_buckets[minute_epoch]["relay_bytes"] += nbytes
        
        self.ensure_started()

    def record_relay_session(
        self,
        event: str,
        subject: str,
        ide_id: str | None,
        session_id: str,
        bytes_total: int | None = None,
        duration_s: float | None = None,
    ) -> None:
        """Record session open/close event."""
        if event == "open":
            self._relay_session_open_count += 1
        elif event == "close":
            self._relay_session_close_count += 1
            
        self._relay_session_buffer.append({
            "event": event,
            "session_id": session_id,
            "subject": subject,
            "ide_id": ide_id,
            "bytes_total": bytes_total,
            "duration_s": duration_s,
        })
        self.ensure_started()

    def record_elevation(self, event: str, subject: str) -> None:
        """Record elevation success/failure events."""
        self._relay_session_buffer.append({
            "event": event,
            "session_id": "elevation",
            "subject": subject,
            "ide_id": None,
            "bytes_total": None,
            "duration_s": None,
        })
        self.ensure_started()

    def _ensure_minute_bucket(self, minute_epoch: int) -> None:
        if minute_epoch not in self._minute_buckets:
            self._minute_buckets[minute_epoch] = {
                "timestamp": minute_epoch * 60,
                "tool_calls": {},
                "relay_bytes": 0,
                "graph_runs": 0,
            }
            # Prune old buckets beyond 120 mins
            cutoff = minute_epoch - 120
            self._minute_buckets = {k: v for k, v in self._minute_buckets.items() if k >= cutoff}

    def subscribe(self, ws: WebSocket) -> None:
        """Subscribe a WebSocket client to live snapshots."""
        self._subscribers.add(ws)
        self.ensure_started()

    def register_gauge_provider(self, key: str, provider: Callable[[], int | float]) -> None:
        """Register a live gauge readable by ``snapshot()``.

        Inverts the dependency: a plugin pushes its own metric in from its
        lifecycle hook instead of core reaching into plugin internals.
        """
        self._gauge_providers[key] = provider

    def unregister_gauge_provider(self, key: str) -> None:
        """Drop a gauge provider (plugin unload). Unknown keys are ignored."""
        self._gauge_providers.pop(key, None)

    def unsubscribe(self, ws: WebSocket) -> None:
        """Unsubscribe a WebSocket client."""
        self._subscribers.discard(ws)

    def snapshot(self) -> dict:
        """Generate a live state snapshot for WS broadcast."""
        # Evaluate each registered gauge in isolation so one bad provider
        # cannot zero out the rest of the snapshot.
        gauges: dict[str, int | float] = {}
        for key, provider in self._gauge_providers.items():
            try:
                gauges[key] = provider()
            except Exception:
                logger.warning("gauge provider %s failed", key, exc_info=True)

        active_sessions = gauges.pop("active_sessions", 0)
            
        # Get sorted last 120 min buckets (copy first — cheap insurance if
        # a concurrent task mutates the dict during sorted()).
        sorted_buckets = sorted(list(self._minute_buckets.values()), key=lambda b: b["timestamp"])
        
        # Calculate summary tool call latency stats (overall recent)
        all_samples = []
        for reservoir in self._tool_latencies.values():
            all_samples.extend(reservoir.samples)
            
        p50, p99 = 0, 0
        if all_samples:
            sorted_all = sorted(all_samples)
            p50 = sorted_all[int(len(sorted_all) * 0.5)]
            p99 = sorted_all[int(len(sorted_all) * 0.99)]

        total_calls = sum(self._tool_calls.values())
        total_errors = sum(self._tool_errors.values())
        success_rate = 100.0 if not total_calls else ((total_calls - total_errors) / total_calls * 100.0)

        graph_success_rate = (
            100.0 if not self._graph_run_count
            else ((self._graph_run_count - self._graph_run_errors) / self._graph_run_count * 100.0)
        )

        return {
            **gauges,
            "active_sessions": active_sessions,
            "relay": {
                "bytes_console": self._relay_bytes_console,
                "bytes_ext": self._relay_bytes_ext,
                "frames": self._relay_frames,
                "session_open": self._relay_session_open_count,
                "session_close": self._relay_session_close_count,
            },
            "summary": {
                "total_calls": total_calls,
                "success_rate": round(success_rate, 2),
                "p50_latency": p50,
                "p99_latency": p99,
                "mcp": {
                    "total_calls": total_calls,
                    "success_rate": round(success_rate, 2),
                    "p50_latency": p50,
                    "p99_latency": p99,
                },
                "graph": {
                    "total_calls": self._graph_run_count,
                    "success_rate": round(graph_success_rate, 2),
                    "p50_latency": self._graph_run_latencies.get_percentile(0.5),
                    "p99_latency": self._graph_run_latencies.get_percentile(0.99),
                },
            },
            "recent_errors": list(self._recent_errors),
            "minute_buckets": sorted_buckets,
        }

    def ensure_started(self) -> None:
        """Idempotently start background loop tasks."""
        if not self._started:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                self._started = True
                self._flush_task = asyncio.create_task(self._flush_loop())
                self._broadcast_task = asyncio.create_task(self._broadcast_loop())
                logger.info("TelemetryCollector background loops started")


    async def stop(self) -> None:
        """Cancel background tasks and perform final flush."""
        self._started = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None
            
        # Final flush of remaining buffer
        await self._flush_once()
        logger.info("TelemetryCollector stopped and flushed")

    async def _flush_once(self) -> None:
        if not self._tool_call_buffer and not self._relay_session_buffer and not self._graph_run_buffer:
            return

        # Swap deque buffers quickly under local scope
        tool_events = list(self._tool_call_buffer)
        self._tool_call_buffer.clear()
        relay_events = list(self._relay_session_buffer)
        self._relay_session_buffer.clear()
        graph_events = list(self._graph_run_buffer)
        self._graph_run_buffer.clear()

        try:
            async with get_async_session() as session:
                for ev in tool_events:
                    obj = ToolCallEvent(
                        tool_name=ev["tool_name"],
                        plugin_id=ev["plugin_id"],
                        model=ev["model"],
                        latency_ms=ev["latency_ms"],
                        ok=ev["ok"],
                        error_type=ev["error_type"],
                        status_code=ev["status_code"],
                        subject=ev["subject"],
                        tenant_id=ev.get("tenant_id") or 1,
                        feature=ev.get("feature") or "mcp",
                        parent_run_id=ev.get("parent_run_id"),
                    )
                    session.add(obj)
                for ev in relay_events:
                    obj = RelaySessionEvent(
                        event=ev["event"],
                        session_id=ev["session_id"],
                        subject=ev["subject"],
                        ide_id=ev["ide_id"],
                        bytes_total=ev["bytes_total"],
                        duration_s=ev["duration_s"],
                    )
                    session.add(obj)
                for ev in graph_events:
                    obj = GraphRunEvent(
                        run_id=ev["run_id"],
                        tenant_id=ev.get("tenant_id") or 1,
                        subject=ev["subject"],
                        mode=ev["mode"],
                        flow_id=ev["flow_id"],
                        goal_class=ev["goal_class"],
                        ok=ev["ok"],
                        error_type=ev["error_type"],
                        terminal_status=ev["terminal_status"],
                        latency_ms=ev["latency_ms"],
                        step_count=ev["step_count"],
                        failed_steps=ev["failed_steps"],
                    )
                    session.add(obj)
                await session.commit()
        except Exception as exc:
            logger.exception("Failed to flush telemetry events to DB: %s", exc)
            _requeue_failed_flush(self._tool_call_buffer, tool_events)
            _requeue_failed_flush(self._relay_session_buffer, relay_events)
            _requeue_failed_flush(self._graph_run_buffer, graph_events)

    async def _flush_loop(self) -> None:
        while self._started:
            try:
                await asyncio.sleep(5)
                await self._flush_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Telemetry flush loop error: %s", exc)

    async def _broadcast_loop(self) -> None:
        while self._started:
            try:
                await asyncio.sleep(2)
                if not self._subscribers:
                    continue
                snap = self.snapshot()
                payload = json.dumps(snap)
                
                # Send to all subscribers
                disconnected = set()
                for ws in list(self._subscribers):
                    try:
                        await ws.send_text(payload)
                    except Exception:
                        disconnected.add(ws)
                for ws in disconnected:
                    self._subscribers.discard(ws)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Telemetry broadcast loop error: %s", exc)


collector = TelemetryCollector()
