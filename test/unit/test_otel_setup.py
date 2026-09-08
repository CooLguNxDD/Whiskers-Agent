"""otel_setup: kill-switched OTel init (Phase 3). Never a hard dependency."""

from __future__ import annotations

import core.telemetry.otel_setup as otel_setup


def test_telemetry_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert otel_setup.telemetry_enabled() is False


def test_telemetry_enabled_when_endpoint_set(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://phoenix:6006/v1/traces")
    assert otel_setup.telemetry_enabled() is True


def test_init_telemetry_noop_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(otel_setup, "_initialized", False)
    otel_setup.init_telemetry()
    assert otel_setup._initialized is False


def test_instrument_app_noop_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    # Must not raise even though `app` is not a real Starlette app.
    otel_setup.instrument_app(object())


def test_sample_ratio_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv("OTEL_TRACES_SAMPLER_ARG", raising=False)
    assert otel_setup._sample_ratio() == 0.1

    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "5.0")
    assert otel_setup._sample_ratio() == 1.0

    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "-1.0")
    assert otel_setup._sample_ratio() == 0.0

    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "not-a-number")
    assert otel_setup._sample_ratio() == 0.1
