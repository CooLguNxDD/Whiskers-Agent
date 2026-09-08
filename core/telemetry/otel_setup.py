"""Optional OpenTelemetry tracing lane — a debugging aid, never product truth.

Postgres + core.telemetry.collector stay the source of truth for product KPIs
(tool_call_events / graph_run_events / portfolio_ask_turns). This module only
feeds an optional Arize Phoenix container for LLM/graph span waterfalls — see
docker-compose.observability.yml and .claude/skills/analytics-telemetry/SKILL.md.

Kill switch: init_telemetry() is a no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is
set. No default endpoint is guessed — an absent Phoenix container must never
spin up an exporter thread pointed at nothing. All opentelemetry imports are
lazy (inside init_telemetry()) so the server boots clean without
requirements-observability.txt installed.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("whiskers_agent.telemetry")

_initialized = False


def telemetry_enabled() -> bool:
    """Whether the OTel lane should initialize at all (kill switch)."""
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def init_telemetry() -> None:
    """Initialize the OTel TracerProvider + OTLP export, or do nothing.

    Safe to call unconditionally at boot: returns immediately when the kill
    switch is off, and never raises — a broken/missing opentelemetry install
    must not prevent the server from starting.
    """
    global _initialized
    if _initialized or not telemetry_enabled():
        return

    endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        ratio = _sample_ratio()
        provider = TracerProvider(
            sampler=TraceIdRatioBased(ratio),
            resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "whiskers-agent-mcp")}),
        )
        # BatchSpanProcessor only — export runs off a background worker thread
        # and drops on failure rather than blocking the request path. No
        # SimpleSpanProcessor / in-memory ring buffer: there is no in-app
        # flamegraph consuming one (Phoenix's own UI covers waterfalls).
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        _initialized = True
        logger.info(
            "OpenTelemetry initialized: endpoint=%s sample_ratio=%.3f", endpoint, ratio
        )
    except Exception:
        logger.warning("OpenTelemetry init failed — tracing disabled, server unaffected", exc_info=True)


def instrument_app(app) -> None:
    """Instrument the Starlette app for HTTP spans. No-op if telemetry is off."""
    if not telemetry_enabled():
        return
    try:
        from opentelemetry.instrumentation.starlette import StarletteInstrumentor

        StarletteInstrumentor().instrument_app(app)
        logger.info("OpenTelemetry: Starlette app instrumented")
    except Exception:
        logger.warning("OpenTelemetry Starlette instrumentation failed", exc_info=True)


def _sample_ratio() -> float:
    raw = os.environ.get("OTEL_TRACES_SAMPLER_ARG", "0.1")
    try:
        ratio = float(raw)
    except ValueError:
        return 0.1
    return min(1.0, max(0.0, ratio))
