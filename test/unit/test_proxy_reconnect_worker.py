"""proxy_reconnect worker: errored proxies are retried on an exponential schedule and re-mounted."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.proxy.reconnect_worker as rw
from core.proxy.models import ProxyReconnectedEvent


@pytest.fixture(autouse=True)
def _fresh_schedule():
    """Each test starts with an empty retry schedule."""
    rw._schedule.clear()
    yield
    rw._schedule.clear()


def _graph(**graph):
    """Patch the config registry's ``graph`` block."""
    return patch(
        "utils.config_registry.get_config_registry",
        return_value=SimpleNamespace(server={"graph": graph}),
    )


def _manager(names, status="active"):
    """ProxyManager double reporting ``names`` as errored; test_proxy returns ``status``."""
    return SimpleNamespace(
        errored_proxy_names=AsyncMock(return_value=list(names)),
        test_proxy=AsyncMock(return_value={"status": status}),
    )


@pytest.mark.asyncio
async def test_first_sighting_waits_one_interval_then_reconnects():
    """A newly errored proxy is not hit immediately; once due, a reconnect re-mounts and emits."""
    manager = _manager(["notion"])
    registry = MagicMock()
    with _graph(), patch("core.proxy.proxy_manager.proxy_manager", manager), \
         patch("core.plugin_loader.plugin_registry.get_registry", return_value=registry):
        await rw._tick()
        manager.test_proxy.assert_not_called()
        rw._schedule["notion"] = (0, 0.0)  # interval elapsed
        await rw._tick()
    manager.test_proxy.assert_awaited_once_with("notion")
    assert "notion" not in rw._schedule
    name, payload = registry.events.emit.call_args.args
    assert name == "proxy.reconnected"
    assert payload == ProxyReconnectedEvent(name="notion", attempts=1)


@pytest.mark.asyncio
async def test_failed_reconnect_backs_off_exponentially():
    """Each failed attempt doubles the delay, capped at proxy_auto_reconnect_max_s."""
    manager = _manager(["github"], status="error")
    with _graph(proxy_auto_reconnect_interval_s=10, proxy_auto_reconnect_max_s=35), \
         patch("core.proxy.proxy_manager.proxy_manager", manager):
        assert [rw.next_delay(n) for n in range(4)] == [10, 20, 35, 35]
        rw._schedule["github"] = (0, 0.0)
        await rw._tick()
        assert rw._schedule["github"][0] == 1
        await rw._tick()  # not due yet
    assert manager.test_proxy.await_count == 1


@pytest.mark.asyncio
async def test_max_attempts_stops_retrying_and_disabled_does_nothing():
    """The attempt cap parks the proxy; proxy_auto_reconnect=false skips the tick entirely."""
    manager = _manager(["github"], status="error")
    with _graph(proxy_auto_reconnect_max_attempts=2), \
         patch("core.proxy.proxy_manager.proxy_manager", manager):
        rw._schedule["github"] = (2, 0.0)
        await rw._tick()
    manager.test_proxy.assert_not_called()

    with _graph(proxy_auto_reconnect=False), patch("core.proxy.proxy_manager.proxy_manager", manager):
        await rw._tick()
    manager.errored_proxy_names.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovered_proxies_drop_from_schedule_and_errors_never_raise():
    """A proxy no longer errored is forgotten; a raising reconnect counts as a failed attempt."""
    rw._schedule["gone"] = (3, 0.0)
    manager = _manager(["notion"])
    manager.test_proxy = AsyncMock(side_effect=RuntimeError("boom"))
    with _graph(), patch("core.proxy.proxy_manager.proxy_manager", manager):
        rw._schedule["notion"] = (0, 0.0)
        assert await rw._tick() is False
    assert "gone" not in rw._schedule
    assert rw._schedule["notion"][0] == 1
