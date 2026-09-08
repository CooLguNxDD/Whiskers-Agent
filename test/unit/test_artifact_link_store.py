"""Unit tests for db_layer/artifact_link_store.py (mocked session)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_session_cm(execute_result=None):
    """Build an async context manager for get_async_session."""
    session = AsyncMock()
    if execute_result is not None:
        session.execute = AsyncMock(return_value=execute_result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm, session


@pytest.mark.asyncio
async def test_create_requires_tenant_id():
    from db_layer.artifact_link_store import create_artifact_link

    with pytest.raises(ValueError, match="tenant_id"):
        await create_artifact_link(
            short_id="art_x_001",
            bucket="b",
            object_key="k",
            tenant_id=None,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_get_requires_tenant_id():
    from db_layer.artifact_link_store import get_artifact_link_by_short_id

    with pytest.raises(ValueError, match="tenant_id"):
        await get_artifact_link_by_short_id("art_x_001", tenant_id=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_artifact_link_persists():
    from db_layer.artifact_link_store import create_artifact_link

    cm, session = _mock_session_cm()

    # Simulate flush assigning id
    def _add(row):
        row.id = 42
        row.created_at = None

    session.add = _add

    with patch("db_layer.artifact_link_store.get_async_session", return_value=cm):
        out = await create_artifact_link(
            short_id="art_diff_001",
            bucket="whiskers-artifacts",
            object_key="1/sess/diff-abc.md",
            content_type="text/markdown",
            kind="diff",
            bytes=100,
            source_path="step_results[0].data",
            tenant_id=1,
            session_id="sess",
        )

    assert out["short_id"] == "art_diff_001"
    assert out["tenant_id"] == 1
    assert out["kind"] == "diff"
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_get_artifact_wrong_tenant_returns_none():
    from db_layer.artifact_link_store import get_artifact_link_by_short_id

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    cm, session = _mock_session_cm(execute_result=result)

    with patch("db_layer.artifact_link_store.get_async_session", return_value=cm):
        out = await get_artifact_link_by_short_id("art_diff_001", tenant_id=99)

    assert out is None
    session.execute.assert_awaited()
