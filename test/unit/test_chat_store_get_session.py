"""Unit tests for chat_store.get_session outerjoin optimization."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from db_layer.chat_store import get_session


@pytest.mark.asyncio
async def test_get_session_single_execute_with_messages():
    """get_session issues one outerjoin query and maps message rows."""
    session_id = uuid4()
    msg_id = uuid4()
    now = datetime.now(timezone.utc)

    chat_session = SimpleNamespace(
        id=session_id,
        title="t",
        created_at=now,
        updated_at=now,
    )
    message = SimpleNamespace(
        id=msg_id,
        role="user",
        content="hi",
        engine=None,
        goap_state=None,
        raw=None,
        created_at=now,
    )

    mock_result = MagicMock()
    mock_result.all.return_value = [(chat_session, message)]

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def _fake_session():
        yield mock_db

    with patch("db_layer.chat_store.get_async_session", _fake_session):
        out = await get_session(str(session_id))

    assert mock_db.execute.await_count == 1
    assert out is not None
    assert out["id"] == str(session_id)
    assert out["title"] == "t"
    assert len(out["messages"]) == 1
    assert out["messages"][0]["content"] == "hi"


@pytest.mark.asyncio
async def test_get_session_empty_session_filters_null_message():
    """Outer join null child must yield messages=[] not a null entry."""
    session_id = uuid4()
    now = datetime.now(timezone.utc)
    chat_session = SimpleNamespace(
        id=session_id,
        title="empty",
        created_at=now,
        updated_at=now,
    )

    mock_result = MagicMock()
    mock_result.all.return_value = [(chat_session, None)]

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def _fake_session():
        yield mock_db

    with patch("db_layer.chat_store.get_async_session", _fake_session):
        out = await get_session(str(session_id))

    assert mock_db.execute.await_count == 1
    assert out is not None
    assert out["messages"] == []


@pytest.mark.asyncio
async def test_get_session_missing_returns_none():
    mock_result = MagicMock()
    mock_result.all.return_value = []

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def _fake_session():
        yield mock_db

    with patch("db_layer.chat_store.get_async_session", _fake_session):
        out = await get_session(str(uuid4()))

    assert out is None
