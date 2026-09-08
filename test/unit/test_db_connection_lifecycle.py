import pytest
import asyncio
from db_layer.connection import (
    _get_async_session_factory,
    dispose_async_engine,
    _async_engine,
    _async_session_factory,
)

@pytest.mark.asyncio
async def test_async_engine_lifecycle():
    """Verify initialization, factory retrieval, and clean disposal under _async_locks_guard."""
    # Ensure starting clean
    await dispose_async_engine()

    factory = await _get_async_session_factory()
    assert factory is not None

    # Calling again returns same factory (fast path)
    factory2 = await _get_async_session_factory()
    assert factory2 is factory

    # Dispose detaches references cleanly
    await dispose_async_engine()
    from db_layer import connection
    assert connection._async_engine is None
    assert connection._async_session_factory is None
