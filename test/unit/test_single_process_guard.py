"""Tests for utils.single_process_guard (stdlib-only; no core_graph import)."""
from __future__ import annotations

import logging

import pytest

from utils.single_process_guard import (
    WORKER_ENV_VARS,
    detect_multi_process_config,
    warn_if_multi_process,
)


@pytest.fixture(autouse=True)
def _reset_model_roles_state():
    # Override the conftest autouse fixture: it imports core_graph, which this test does not need.
    yield


def test_no_signals_returns_empty():
    assert detect_multi_process_config({}, {}) == []
    assert detect_multi_process_config(None, None) == []


@pytest.mark.parametrize("name", WORKER_ENV_VARS)
def test_each_env_var_above_one_gives_reason(name):
    reasons = detect_multi_process_config({name: "4"}, {})
    assert len(reasons) == 1
    assert name in reasons[0]


@pytest.mark.parametrize("value", ["1", "", "abc", "-3", "0", " ", "2.5"])
def test_env_garbage_and_low_values_ignored(value):
    env = {name: value for name in WORKER_ENV_VARS}
    assert detect_multi_process_config(env, {}) == []


@pytest.mark.parametrize("key", ["replicas", "workers"])
def test_config_key_above_one_gives_reason(key):
    reasons = detect_multi_process_config({}, {"deployment": {key: 3}})
    assert len(reasons) == 1
    assert f"deployment.{key}" in reasons[0]


@pytest.mark.parametrize(
    "deployment",
    [{"replicas": 1, "workers": 1}, {"replicas": True}, {"workers": "x"}, {"replicas": None}, {"workers": -2}, "bad", None],
)
def test_config_garbage_ignored(deployment):
    assert detect_multi_process_config({}, {"deployment": deployment}) == []


def test_config_string_number_counts():
    assert len(detect_multi_process_config({}, {"deployment": {"workers": "2"}})) == 1


def test_signals_combine():
    reasons = detect_multi_process_config({"WEB_CONCURRENCY": "2"}, {"deployment": {"replicas": 2, "workers": 5}})
    assert len(reasons) == 3


def test_startup_hook_warns_once_per_reason(caplog):
    with caplog.at_level(logging.WARNING, logger="whiskers"):
        reasons = warn_if_multi_process({"WEB_CONCURRENCY": "2", "UVICORN_WORKERS": "3"}, {})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(reasons) == 2
    assert len(warnings) == 2
    text = warnings[0].getMessage()
    assert "subscription bus" in text and "rate limiter" in text and "session locks" in text
    assert "SetupGuide.md" in text


def test_startup_hook_silent_without_reasons(caplog):
    with caplog.at_level(logging.DEBUG, logger="whiskers"):
        assert warn_if_multi_process({"WEB_CONCURRENCY": "1"}, {"deployment": {"replicas": 1}}) == []
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_startup_hook_never_raises(caplog):
    class Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    assert warn_if_multi_process(Boom(), {}) == []
