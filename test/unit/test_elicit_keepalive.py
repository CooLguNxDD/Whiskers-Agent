"""
Unit tests for the elicitation keepalive mechanism in helpers.
Ensures that long-running human elicitations do not cause MCP host per-request timeouts
by emitting periodic report_progress when a progressToken is present.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_elicit_returns_none_without_progress_token_and_never_calls_elicit():
    """ctx WITHOUT a progressToken: _elicit returns None and ctx.elicit is NEVER awaited."""
    from core_graph.node import helpers
    from fastmcp.server.elicitation import AcceptedElicitation  # for type, not needed

    # Build ctx without progressToken
    ctx = MagicMock()
    ctx.request_context.meta.progressToken = None
    ctx.elicit = AsyncMock()
    ctx.report_progress = AsyncMock()

    result = await helpers._elicit(ctx, "Please confirm?", bool)

    assert result is None
    ctx.elicit.assert_not_awaited()
    ctx.report_progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_elicit_with_progress_token_emits_keepalive_and_returns_accepted_data():
    """ctx WITH progressToken + slow elicit: keepalive fires >=1, returns data, no pending task."""
    from core_graph.node import helpers
    from core_graph.node.helpers import mcp_ctx

    # Small interval so keepalive fires quickly
    original_interval = mcp_ctx.ELICIT_KEEPALIVE_INTERVAL_S
    mcp_ctx.ELICIT_KEEPALIVE_INTERVAL_S = 0.01

    try:
        # AcceptedElicitation instance (isinstance check must pass)
        class _FakeAccepted:
            def __init__(self, data):
                self.data = data

        # Patch the symbol that _elicit imports inside its function
        with patch("fastmcp.server.elicitation.AcceptedElicitation", _FakeAccepted):
            ctx = MagicMock()
            # progressToken present -> channel active
            meta = MagicMock()
            meta.progressToken = "tok-123"
            ctx.request_context.meta = meta

            # Elicit will be slow: wait on event we set after delay
            elicit_event = asyncio.Event()
            async def slow_elicit(*a, **k):
                await asyncio.wait_for(elicit_event.wait(), timeout=2.0)
                return _FakeAccepted("approved-value")

            ctx.elicit = AsyncMock(side_effect=slow_elicit)
            ctx.report_progress = AsyncMock()

            # Run _elicit in background so we can assert keepalives before it finishes
            task = asyncio.create_task(helpers._elicit(ctx, "msg", bool))

            # Wait enough for at least one keepalive tick
            await asyncio.sleep(0.05)

            # Allow the elicit to resolve
            elicit_event.set()

            result = await task

            assert result == "approved-value"
            assert ctx.elicit.await_count >= 1
            # At least one report_progress from keepalive
            assert ctx.report_progress.await_count >= 1
            # Verify a keepalive payload was sent
            calls = [c.args for c in ctx.report_progress.call_args_list]
            assert any(
                len(c) >= 3 and isinstance(c[2], str) and "keepalive" in c[2]
                for c in calls
            )

            # No pending keepalive tasks (they must be awaited in finally)
            current = asyncio.current_task()
            pending_keepalive = [
                t for t in asyncio.all_tasks()
                if not t.done() and t is not current and (
                    "_elicit_keepalive" in getattr(getattr(t, "get_coro", lambda: None)(), "__qualname__", "") or
                    "_elicit_keepalive" in str(t)
                )
            ]
            assert len(pending_keepalive) == 0
    finally:
        mcp_ctx.ELICIT_KEEPALIVE_INTERVAL_S = original_interval


@pytest.mark.asyncio
async def test_elicit_returns_none_on_elicit_exception_even_with_progress_token():
    """ctx.elicit raising Exception (progressToken present) -> _elicit returns None and does not raise."""
    from core_graph.node import helpers
    from core_graph.node.helpers import mcp_ctx

    original_interval = mcp_ctx.ELICIT_KEEPALIVE_INTERVAL_S
    mcp_ctx.ELICIT_KEEPALIVE_INTERVAL_S = 0.01

    try:
        ctx = MagicMock()
        meta = MagicMock()
        meta.progressToken = "tok-xyz"
        ctx.request_context.meta = meta
        ctx.elicit = AsyncMock(side_effect=RuntimeError("client gone"))
        ctx.report_progress = AsyncMock()

        result = await helpers._elicit(ctx, "confirm?", str)

        assert result is None
        ctx.elicit.assert_awaited_once()
        # Keepalive may or may not have fired (race), but must not have leaked tasks
        current = asyncio.current_task()
        pending_keepalive = [
            t for t in asyncio.all_tasks()
            if not t.done() and t is not current and (
                "_elicit_keepalive" in getattr(getattr(t, "get_coro", lambda: None)(), "__qualname__", "") or
                "_elicit_keepalive" in str(t)
            )
        ]
        assert len(pending_keepalive) == 0
    finally:
        mcp_ctx.ELICIT_KEEPALIVE_INTERVAL_S = original_interval
