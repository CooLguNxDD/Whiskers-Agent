"""ProxyManager: an upstream whose tools listing exhausts its reconnect attempts is marked unavailable."""

import contextlib
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.proxy.models import ProxyUnavailableEvent

# ``core.proxy.proxy_manager`` the attribute is the singleton; fetch the module itself.
pm = importlib.import_module("core.proxy.proxy_manager")


@contextlib.asynccontextmanager
async def _fake_session(sink: list):
    """Async session double recording every executed statement."""
    session = MagicMock()
    session.execute = AsyncMock(side_effect=lambda stmt: sink.append(stmt))
    session.commit = AsyncMock()
    yield session


def _manager_with(provider) -> pm.ProxyManager:
    """ProxyManager holding ``provider`` as the live ``notion`` handle."""
    manager = pm.ProxyManager()
    manager._active_handles["notion"] = provider
    manager._remove_proxy_routes = MagicMock(return_value=0)
    return manager


@pytest.mark.asyncio
async def test_mark_unavailable_unmounts_flags_row_and_emits_event():
    """The dead handle is unmounted, the row goes to ``error`` with a reconnect request, an event fires."""
    provider = object()
    manager = _manager_with(provider)
    stmts: list = []
    registry = MagicMock()
    with patch.object(pm, "unmount_proxy") as unmount, \
         patch.object(pm, "_bump_tool_meta_version") as bump, \
         patch.object(pm, "_clear_proxy_scopes") as clear, \
         patch.object(pm, "get_async_session", lambda: _fake_session(stmts)), \
         patch("core.plugin_loader.plugin_registry.get_registry", return_value=registry):
        await manager._mark_unavailable("notion", provider, 6, "405 Method Not Allowed")

    unmount.assert_called_once()
    bump.assert_called_once()
    clear.assert_called_once_with("notion")
    assert "notion" not in manager._active_handles
    params = stmts[0].compile().params
    assert params["status"] == "error"
    assert "reconnect required" in params["error_message"]
    assert "405" in params["error_message"]
    name, payload = registry.events.emit.call_args.args
    assert name == "proxy.unavailable"
    assert payload == ProxyUnavailableEvent(name="notion", failures=6, error="405 Method Not Allowed")


@pytest.mark.asyncio
async def test_mark_unavailable_skips_a_handle_already_replaced():
    """A late hook from an old provider must not tear down a freshly reconnected one."""
    manager = _manager_with(object())
    with patch.object(pm, "unmount_proxy") as unmount, \
         patch.object(pm, "get_async_session") as session:
        await manager._mark_unavailable("notion", object(), 6, "boom")
    unmount.assert_not_called()
    session.assert_not_called()
    assert "notion" in manager._active_handles


def test_watch_upstream_only_tools_exhaustion_marks_unavailable():
    """prompts/resources giving up (upstream doesn't serve them) leaves the proxy mounted."""
    manager = pm.ProxyManager()
    provider = object()
    hooks = []
    with patch.object(pm, "set_exhausted_hook", lambda p, h: hooks.append(h)), \
         patch.object(pm, "spawn_supervised") as spawn:
        manager._watch_upstream("notion", provider)
        hooks[0]("prompts", 6, "405")
        hooks[0]("resources", 6, "405")
        spawn.assert_not_called()
        hooks[0]("tools", 6, "405")
    spawn.assert_called_once()
    spawn.call_args.args[0].close()  # never-awaited coroutine from the patched spawn
