"""
Unit tests for cross-process registry sync LISTEN/NOTIFY.
"""

import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from core.clustering.registry_sync import (
    start_registry_sync,
    stop_registry_sync,
    broadcast_tool_visibility_change,
    _apply_remote_change,
    _bare_dsn,
)
from core.proxy_tools.tool_visibility import ToolVisibility


@pytest.fixture(autouse=True)
def cleanup_registry_sync_globals():
    from core.clustering import registry_sync
    registry_sync._listener_task = None
    registry_sync._listener_conn = None
    yield
    registry_sync._listener_task = None
    registry_sync._listener_conn = None


def test_bare_dsn():
    assert _bare_dsn("postgresql+psycopg2://user:pass@host/db") == "postgresql://user:pass@host/db"
    assert _bare_dsn("postgresql+psycopg://user:pass@host/db") == "postgresql://user:pass@host/db"
    assert _bare_dsn("postgresql://user:pass@host/db") == "postgresql://user:pass@host/db"
    assert _bare_dsn("sqlite://") == "sqlite://"


@pytest.mark.asyncio
async def test_start_registry_sync_inert_without_db():
    with patch("core.context._env._DB_AVAILABLE", False), \
         patch("psycopg.AsyncConnection.connect", new_callable=AsyncMock) as mock_connect:
        res = await start_registry_sync()
        assert res is None
        mock_connect.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_inert_without_db():
    with patch("core.context._env._DB_AVAILABLE", False), \
         patch("db_layer.connection.get_async_session") as mock_get_session:
        await broadcast_tool_visibility_change("hide", "foo")
        mock_get_session.assert_not_called()


def test_apply_remote_change_hide_and_show():
    from core.context import tool_visibility
    mock_mcp = MagicMock()
    original_mcp = tool_visibility._mcp
    tool_visibility._mcp = mock_mcp
    try:
        # Test hide
        _apply_remote_change(json.dumps({"action": "hide", "tool_name": "foo"}))
        mock_mcp.disable.assert_called_once_with(names={"foo"}, components={"tool"})
        
        mock_mcp.reset_mock()
        
        # Test show
        _apply_remote_change(json.dumps({"action": "show", "tool_name": "bar"}))
        mock_mcp.enable.assert_called_once_with(names={"bar"}, components={"tool"})
    finally:
        tool_visibility._mcp = original_mcp


def test_apply_remote_change_malformed_payload_does_not_raise():
    # This should not raise an error
    _apply_remote_change("garbage JSON payload")


@pytest.mark.asyncio
async def test_hide_schedules_broadcast():
    mock_mcp = MagicMock()
    
    with patch("core.clustering.registry_sync.broadcast_tool_visibility_change", new_callable=AsyncMock) as mock_broadcast:
        tv = ToolVisibility(mock_mcp)
        assert tv.hide("foo") is True
        
        # Give the event loop a chance to schedule the background task
        await asyncio.sleep(0.01)
        mock_broadcast.assert_awaited_once_with("hide", "foo")


@pytest.mark.asyncio
async def test_show_schedules_broadcast():
    mock_mcp = MagicMock()
    
    with patch("core.clustering.registry_sync.broadcast_tool_visibility_change", new_callable=AsyncMock) as mock_broadcast:
        tv = ToolVisibility(mock_mcp)
        assert tv.show("bar") is True
        
        # Give the event loop a chance to schedule the background task
        await asyncio.sleep(0.01)
        mock_broadcast.assert_awaited_once_with("show", "bar")


@pytest.mark.asyncio
async def test_stop_registry_sync_safe_when_never_started():
    # This should run cleanly without exceptions
    await stop_registry_sync()


@pytest.mark.asyncio
async def test_start_registry_sync_idempotent():
    mock_conn = AsyncMock()
    mock_task = MagicMock()
    
    with patch("core.context._env._DB_AVAILABLE", True), \
         patch("psycopg.AsyncConnection.connect", AsyncMock(return_value=mock_conn)) as mock_connect, \
         patch("asyncio.create_task", return_value=mock_task) as mock_create_task, \
         patch("db_layer.connection.get_database_url", return_value="postgresql://url"):
         
        task1 = await start_registry_sync()
        assert task1 is mock_task
        mock_connect.assert_called_once()
        mock_create_task.assert_called_once()
        
        # Second call should return same task and not call connect or create_task again
        task2 = await start_registry_sync()
        assert task2 is task1
        mock_connect.assert_called_once()
        mock_create_task.assert_called_once()


@pytest.mark.asyncio
async def test_listen_loop_dispatches_payload():
    mock_conn = MagicMock()
    
    # An async generator yielding notifications
    mock_notify = MagicMock()
    mock_notify.payload = '{"action": "hide", "tool_name": "foo"}'
    
    async def mock_notifies():
        yield mock_notify
        # Yield once and then sleep/hang to simulate waiting for next notifies
        await asyncio.sleep(1)
        
    mock_conn.notifies = mock_notifies
    
    with patch("core.clustering.registry_sync._apply_remote_change") as mock_apply:
        from core.clustering.registry_sync import _listen_loop
        task = asyncio.create_task(_listen_loop(mock_conn))
        
        # Give the event loop a tick to run the generator and call _apply_remote_change
        await asyncio.sleep(0.02)
        
        mock_apply.assert_called_once_with('{"action": "hide", "tool_name": "foo"}')
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_listen_loop_self_heals_after_giving_up_instead_of_exiting():
    """After _MAX_CONSECUTIVE_FAILURES, the loop must degrade to a long-interval
    retry rather than returning (task ending) — a transient extended outage should
    self-heal without requiring a container restart."""
    from core.clustering import registry_sync
    from core.clustering.registry_sync import _listen_loop

    real_sleep = asyncio.sleep

    async def always_fails_connect(*args, **kwargs):
        raise ConnectionError("db down")

    sleep_calls = []

    async def instant_sleep(delay, *args, **kwargs):
        sleep_calls.append(delay)
        await real_sleep(0)  # genuine yield so the loop doesn't hog the event loop

    with patch("psycopg.AsyncConnection.connect", side_effect=always_fails_connect), \
         patch("asyncio.sleep", side_effect=instant_sleep), \
         patch("db_layer.connection.get_database_url", return_value="postgresql://url"):

        task = asyncio.create_task(_listen_loop(None))

        # Enough ticks for consecutive_failures to exceed the cap and hit the
        # degraded branch at least once — sleep is instant so this settles fast.
        for _ in range(200):
            if registry_sync._DEGRADED_RETRY_INTERVAL_S in sleep_calls:
                break
            await real_sleep(0)

        assert registry_sync._DEGRADED_RETRY_INTERVAL_S in sleep_calls
        assert not task.done(), "loop must not exit after giving up — it should self-heal"

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_stop_registry_sync_cancels_and_closes():
    mock_conn = AsyncMock()
    mock_task = MagicMock()
    
    with patch("core.context._env._DB_AVAILABLE", True), \
         patch("psycopg.AsyncConnection.connect", AsyncMock(return_value=mock_conn)), \
         patch("asyncio.create_task", return_value=mock_task), \
         patch("db_layer.connection.get_database_url", return_value="postgresql://url"):
         
        await start_registry_sync()
        
        await stop_registry_sync()
        
        mock_task.cancel.assert_called_once()
        mock_conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_listen_loop_reconnects_on_exception():
    from core.clustering.registry_sync import _listen_loop
    
    real_asyncio_sleep = asyncio.sleep

    mock_conn1 = AsyncMock()
    # first call to notifies raises an exception (connection lost)
    async def mock_notifies1():
        if False:
            yield None
        raise Exception("Connection lost")
    mock_conn1.notifies = mock_notifies1
    
    mock_conn2 = AsyncMock()
    # second connection notifies hangs/sleeps
    async def mock_notifies2():
        yield MagicMock(payload='{"action": "show", "tool_name": "bar"}')
        await real_asyncio_sleep(10)
    mock_conn2.notifies = mock_notifies2
    
    # We patch connect to return mock_conn2 when called (reconnect)
    # And we patch asyncio.sleep to not actually sleep
    connect_calls = []
    async def mock_connect(*args, **kwargs):
        connect_calls.append(args)
        return mock_conn2

    async def mock_sleep_side_effect(delay, *args, **kwargs):
        if delay >= 1.0:
            await real_asyncio_sleep(0.001)
        else:
            await real_asyncio_sleep(delay)

    with patch("psycopg.AsyncConnection.connect", side_effect=mock_connect) as mock_conn_func, \
         patch("asyncio.sleep", side_effect=mock_sleep_side_effect) as mock_sleep, \
         patch("core.clustering.registry_sync._apply_remote_change") as mock_apply, \
         patch("db_layer.connection.get_database_url", return_value="postgresql://url"):
         
        # Start the listen loop with the first failing conn
        task = asyncio.create_task(_listen_loop(mock_conn1))
        
        # Give the event loop a few ticks to handle the exception, sleep, reconnect and read notify
        await asyncio.sleep(0.05)
        
        # Check that we reconnected
        mock_conn_func.assert_called_once()  # connect was called once (for reconnect, since mock_conn1 was passed directly)
        from psycopg import sql
        mock_conn2.execute.assert_called_once_with(
            sql.SQL("LISTEN {}").format(sql.Identifier("tool_visibility_changed"))
        )
        mock_sleep.assert_any_call(1.0)  # slept with backoff 1.0
        
        # Check notify from the reconnected conn was processed
        mock_apply.assert_called_once_with('{"action": "show", "tool_name": "bar"}')
        
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


