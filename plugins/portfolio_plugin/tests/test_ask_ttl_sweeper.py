"""portfolio_ask_turns_ttl_sweeper: plugin-owned retention sweep (Phase 4)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from plugins.portfolio_plugin.ask import ttl_sweeper


class _Result:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcounts=(0,)):
        self.executed = []
        self.commits = 0
        self._rowcounts = list(rowcounts)
        self._i = 0

    async def execute(self, stmt):
        self.executed.append(stmt)
        n = self._rowcounts[self._i] if self._i < len(self._rowcounts) else 0
        self._i += 1
        return _Result(n)

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def committed(self):
        return self.commits > 0


@pytest.mark.asyncio
async def test_tick_deletes_and_commits():
    fake = _FakeSession(rowcounts=(0,))
    with patch("db_layer.connection.get_async_session", return_value=fake):
        result = await ttl_sweeper._tick()

    assert result is False
    assert len(fake.executed) == 1
    assert fake.commits == 1


@pytest.mark.asyncio
async def test_tick_returns_true_when_a_batch_deleted():
    fake = _FakeSession(rowcounts=(ttl_sweeper._BATCH,))
    with patch("db_layer.connection.get_async_session", return_value=fake):
        result = await ttl_sweeper._tick()

    assert result is True
    assert len(fake.executed) == 1


@pytest.mark.asyncio
async def test_tick_swallows_db_errors():
    class _BoomSession:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *exc):
            return False

    with patch("db_layer.connection.get_async_session", return_value=_BoomSession()):
        result = await ttl_sweeper._tick()
    assert result is False


def test_config_defaults(monkeypatch):
    monkeypatch.setattr("utils.server_config.SCHEDULED_JOBS_CONFIG", {}, raising=False)
    assert ttl_sweeper._interval_seconds() == ttl_sweeper._DEFAULT_INTERVAL_SECONDS
    assert ttl_sweeper._enabled() is True


def test_config_overrides(monkeypatch):
    monkeypatch.setattr(
        "utils.server_config.SCHEDULED_JOBS_CONFIG",
        {"portfolio_ask_turns_sweep": {"enabled": False, "interval_seconds": 60}},
        raising=False,
    )
    assert ttl_sweeper._interval_seconds() == 60
    assert ttl_sweeper._enabled() is False


def test_register_adds_worker_spec():
    class _FakeRegistry:
        def __init__(self):
            self.specs = []

        def register(self, spec):
            self.specs.append(spec)

    registry = _FakeRegistry()
    ttl_sweeper.register(registry)
    assert len(registry.specs) == 1
    assert registry.specs[0].name == "portfolio_ask_turns_ttl_sweeper"
