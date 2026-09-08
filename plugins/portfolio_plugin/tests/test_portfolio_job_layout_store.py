"""Unit tests for plugins.portfolio_plugin.store job-layout functions (Feature B "bake & send")."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.portfolio_plugin.models import PortfolioJobLayout
from plugins.portfolio_plugin.store import (
    create_job_layout,
    get_job_layout_by_short_id,
    job_layout_short_id_exists,
    update_job_layout,
)


def _session_cm(execute_result=None):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result or MagicMock())
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, session


def _row(**overrides) -> PortfolioJobLayout:
    row = PortfolioJobLayout(
        id=1,
        short_id="whiskers_successor_992",
        job_application_job_id="job-123",
        tenant_id=1,
        audience="recruiter",
        star_query="impact results",
        layout_json={"version": 1, "meta": {"audience": "recruiter"}, "blocks": []},
    )
    row.created_at = None
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


@pytest.mark.asyncio
async def test_job_layout_short_id_exists_true():
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = 1
    cm, _ = _session_cm(exec_result)
    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        assert await job_layout_short_id_exists("whiskers_successor_992") is True


@pytest.mark.asyncio
async def test_job_layout_short_id_exists_false():
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = None
    cm, _ = _session_cm(exec_result)
    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        assert await job_layout_short_id_exists("nonexistent_000") is False


@pytest.mark.asyncio
async def test_create_job_layout_round_trip():
    cm, session = _session_cm()

    def _fake_add(row):
        row.id = 1
        row.created_at = None

    session.add.side_effect = _fake_add

    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        result = await create_job_layout(
            short_id="whiskers_successor_992",
            layout={"version": 1, "meta": {"audience": "recruiter"}, "blocks": []},
            audience="recruiter",
            star_query="impact results",
            job_application_job_id="job-123",
            tenant_id=1,
        )

    assert result["short_id"] == "whiskers_successor_992"
    assert result["audience"] == "recruiter"
    assert result["job_application_job_id"] == "job-123"
    assert result["layout_json"]["blocks"] == []
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_job_layout_persists_plan_and_score():
    """Durable provenance (Phase 3): plan_json/recipe_id/jury_score must reach
    the persisted row and round-trip through the dict serializer."""
    cm, session = _session_cm()

    def _fake_add(row):
        row.id = 1
        row.created_at = None

    session.add.side_effect = _fake_add

    plan = {"version": 1, "recipe_id": "job-bake", "steps": [{"id": "h1", "block_type": "hero"}]}
    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        result = await create_job_layout(
            short_id="whiskers_successor_992",
            layout={"version": 1, "meta": {"audience": "recruiter"}, "blocks": []},
            audience="recruiter",
            tenant_id=1,
            plan_json=plan,
            recipe_id="job-bake",
            jury_score=8.4,
        )

    assert result["plan_json"] == plan
    assert result["recipe_id"] == "job-bake"
    assert result["jury_score"] == 8.4
    added_row = session.add.call_args[0][0]
    assert added_row.plan_json == plan
    assert added_row.recipe_id == "job-bake"
    assert added_row.jury_score == 8.4


@pytest.mark.asyncio
async def test_create_job_layout_provenance_optional():
    """Existing callers that don't pass plan_json/recipe_id/jury_score must
    keep working -- these are additive, nullable columns."""
    cm, session = _session_cm()

    def _fake_add(row):
        row.id = 1
        row.created_at = None

    session.add.side_effect = _fake_add

    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        result = await create_job_layout(
            short_id="whiskers_successor_992",
            layout={"version": 1, "meta": {"audience": "recruiter"}, "blocks": []},
            audience="recruiter",
            tenant_id=1,
        )

    assert result["plan_json"] is None
    assert result["recipe_id"] is None
    assert result["jury_score"] is None


@pytest.mark.asyncio
async def test_get_job_layout_by_short_id_found():
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = _row()
    cm, _ = _session_cm(exec_result)

    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        result = await get_job_layout_by_short_id("whiskers_successor_992")

    assert result is not None
    assert result["short_id"] == "whiskers_successor_992"
    assert result["tenant_id"] == 1


@pytest.mark.asyncio
async def test_get_job_layout_by_short_id_not_found():
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = None
    cm, _ = _session_cm(exec_result)

    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        result = await get_job_layout_by_short_id("missing_000")

    assert result is None


@pytest.mark.asyncio
async def test_create_job_layout_derived_with_parent():
    cm, session = _session_cm()

    def _fake_add(row):
        row.id = 2
        row.created_at = None

    session.add.side_effect = _fake_add

    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        result = await create_job_layout(
            short_id="whiskers_patch_001",
            layout={"version": 1, "meta": {}, "blocks": []},
            audience="recruiter",
            tenant_id=1,
            parent_short_id="whiskers_successor_992",
            is_derived=True,
        )

    assert result["parent_short_id"] == "whiskers_successor_992"
    assert result["is_derived"] is True
    added = session.add.call_args[0][0]
    assert added.parent_short_id == "whiskers_successor_992"
    assert added.is_derived is True


@pytest.mark.asyncio
async def test_update_job_layout_refuses_non_derived():
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = _row(is_derived=False)
    cm, session = _session_cm(exec_result)

    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        ok = await update_job_layout(
            "whiskers_successor_992",
            layout={"version": 1, "blocks": [{"type": "hero", "id": "h1"}]},
            tenant_id=1,
        )
    assert ok is False
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_job_layout_refuses_wrong_tenant():
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = _row(is_derived=True, tenant_id=1)
    cm, session = _session_cm(exec_result)

    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        ok = await update_job_layout(
            "whiskers_successor_992",
            layout={"version": 1, "blocks": []},
            tenant_id=99,
        )
    assert ok is False


@pytest.mark.asyncio
async def test_update_job_layout_refuses_missing():
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = None
    cm, session = _session_cm(exec_result)

    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        ok = await update_job_layout("missing", layout={"version": 1, "blocks": []}, tenant_id=1)
    assert ok is False


@pytest.mark.asyncio
async def test_update_job_layout_accepts_derived():
    row = _row(is_derived=True, parent_short_id="parent_1")
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = row
    cm, session = _session_cm(exec_result)
    new_layout = {"version": 1, "meta": {"mode": "patched"}, "blocks": [{"type": "prose", "id": "p1"}]}

    with patch("plugins.portfolio_plugin.store.get_async_session", return_value=cm):
        ok = await update_job_layout("whiskers_successor_992", layout=new_layout, tenant_id=1)

    assert ok is True
    assert row.layout_json == new_layout
    session.commit.assert_awaited_once()
