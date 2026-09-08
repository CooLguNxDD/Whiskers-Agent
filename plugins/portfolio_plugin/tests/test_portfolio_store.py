"""Unit tests for the Portfolio plugin store and models."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone

from pathlib import Path

from plugins.portfolio_plugin.models import PortfolioProject
import plugins.portfolio_plugin.store as store
from db_layer.plugin_schema_migrator import PluginSchemaMigrator


def test_models_structure():
    """Verify that models import cleanly and have expected tablenames and columns."""
    assert PortfolioProject.__tablename__ == "portfolio_projects"

    # Check defaults on PortfolioProject
    sort_order_col = PortfolioProject.__table__.columns.sort_order
    assert sort_order_col.server_default.arg == "0"

    is_active_col = PortfolioProject.__table__.columns.is_active
    assert is_active_col.server_default.arg == "true"


def test_plugin_local_migration_steps():
    """Portfolio DDL lives under plugins/portfolio_plugin/migrations/ as Python steps."""
    steps = PluginSchemaMigrator().discover_steps(
        Path("plugins/portfolio_plugin/migrations")
    )
    assert [s.revision for s in steps] == [
        "0001_init",
        "0002_job_layouts",
        "0003_job_layout_provenance",
        "0004_job_layout_derived",
        "0005_bake_observability",
        "0006_project_timeline",
        "0007_ask_turns",
        "0008_ask_turn_column_widths",
    ]
    assert all(s.kind == "py" for s in steps)


@pytest.fixture
def mock_session():
    with patch("plugins.portfolio_plugin.store.get_async_session") as mock_get_session:
        mock_session_instance = AsyncMock()
        mock_session_instance.add = MagicMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session_instance
        yield mock_session_instance


@pytest.mark.asyncio
async def test_list_projects_audience_filter(mock_session):
    p1 = PortfolioProject(
        id=1, slug="p1", name="Proj 1", summary="Sum 1", audiences=[],
        tags=[], metrics=[], links=[], sort_order=1, is_active=True, tenant_id=1,
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )
    p2 = PortfolioProject(
        id=2, slug="p2", name="Proj 2", summary="Sum 2", audiences=["recruiter"],
        tags=[], metrics=[], links=[], sort_order=2, is_active=True, tenant_id=1,
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )
    p3 = PortfolioProject(
        id=3, slug="p3", name="Proj 3", summary="Sum 3", audiences=["peer"],
        tags=[], metrics=[], links=[], sort_order=3, is_active=True, tenant_id=1,
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [p1, p2, p3]
    mock_session.execute.return_value = mock_result

    res = await store.list_projects(audience="recruiter", tenant_id=1)
    assert len(res) == 2
    assert res[0]["id"] == 1
    assert res[1]["id"] == 2


@pytest.mark.asyncio
async def test_list_projects_include_inactive(mock_session):
    p1 = PortfolioProject(
        id=1, slug="p1", name="Proj 1", summary="Sum 1", audiences=[],
        tags=[], metrics=[], links=[], sort_order=1, is_active=True, tenant_id=1,
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )
    p2 = PortfolioProject(
        id=2, slug="p2", name="Proj 2", summary="Sum 2", audiences=[],
        tags=[], metrics=[], links=[], sort_order=2, is_active=False, tenant_id=1,
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [p1, p2]
    mock_session.execute.return_value = mock_result

    res = await store.list_projects(audience=None, include_inactive=True, tenant_id=1)
    assert len(res) == 2
    assert res[0]["id"] == 1
    assert res[1]["id"] == 2


@pytest.mark.asyncio
async def test_get_project_found(mock_session):
    p = PortfolioProject(
        id=1, slug="my-project", name="Proj 1", summary="Sum 1", audiences=[],
        tags=[], metrics=[], links=[], sort_order=1, is_active=True, tenant_id=1,
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = p
    mock_session.execute.return_value = mock_result

    res = await store.get_project("my-project", tenant_id=1)
    assert res is not None
    assert res["slug"] == "my-project"
    assert res["name"] == "Proj 1"


@pytest.mark.asyncio
async def test_get_project_missing(mock_session):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    res = await store.get_project("missing", tenant_id=1)
    assert res is None


@pytest.mark.asyncio
async def test_upsert_project_updates_existing(mock_session):
    p = PortfolioProject(
        id=1, slug="my-project", name="Proj 1", summary="Sum 1", audiences=[],
        tags=[], metrics=[], links=[], sort_order=1, is_active=True, tenant_id=1,
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = p
    mock_session.execute.return_value = mock_result

    res = await store.upsert_project("my-project", tenant_id=1, summary="new")

    assert res["slug"] == "my-project"
    assert p.summary == "new"
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_project_inserts_new(mock_session):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    added_obj = None

    def fake_add(obj):
        nonlocal added_obj
        added_obj = obj
        obj.id = 42
        obj.created_at = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
        obj.updated_at = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)

    mock_session.add.side_effect = fake_add

    res = await store.upsert_project("new-project", tenant_id=1, name="New Proj", summary="A new project")

    assert res["id"] == 42
    assert res["slug"] == "new-project"
    assert res["name"] == "New Proj"
    assert res["summary"] == "A new project"

    assert added_obj is not None
    assert added_obj.slug == "new-project"
    assert added_obj.tenant_id == 1
    assert added_obj.name == "New Proj"
    assert added_obj.summary == "A new project"
    mock_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_project_malformed_date_drops_to_none(mock_session):
    """A malformed started_on/ended_on must not raise — date.fromisoformat on a
    non-ISO string (e.g. 'present') is caught and the field falls back to None
    instead of crashing the async handler with an unhandled ValueError."""
    p = PortfolioProject(
        id=1, slug="my-project", name="Proj 1", summary="Sum 1", audiences=[],
        tags=[], metrics=[], links=[], sort_order=1, is_active=True, tenant_id=1,
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = p
    mock_session.execute.return_value = mock_result

    res = await store.upsert_project("my-project", tenant_id=1, started_on="present")

    assert res["started_on"] is None
    assert p.started_on is None
    mock_session.commit.assert_called_once()
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_delete_project(mock_session):
    # Case 1: Row deleted (rowcount 1)
    mock_result_1 = MagicMock()
    mock_result_1.rowcount = 1
    mock_session.execute.return_value = mock_result_1

    res_1 = await store.delete_project("my-project", tenant_id=1)
    assert res_1 is True

    # Case 2: No row deleted (rowcount 0)
    mock_result_2 = MagicMock()
    mock_result_2.rowcount = 0
    mock_session.execute.return_value = mock_result_2

    res_2 = await store.delete_project("missing-project", tenant_id=1)
    assert res_2 is False
