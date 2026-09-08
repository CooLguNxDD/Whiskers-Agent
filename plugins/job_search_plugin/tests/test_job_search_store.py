"""Unit tests for the Job Search plugin store and models."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone

from pathlib import Path

from plugins.job_search_plugin.models import JobApplicantProfile, JobApplication, JobPreferenceEmbedding
import plugins.job_search_plugin.store as store
from db_layer.plugin_schema_migrator import PluginSchemaMigrator


def test_models_structure():
    """Verify that models import cleanly and have expected tablenames and columns."""
    assert JobApplicantProfile.__tablename__ == "job_applicant_profiles"
    assert JobApplication.__tablename__ == "job_applications"
    assert JobPreferenceEmbedding.__tablename__ == "job_preference_embeddings"

    # Check default on JobApplication.status
    status_col = JobApplication.__table__.columns.status
    assert status_col.server_default.arg == "drafted"


def test_plugin_local_migration_steps():
    """Job search DDL lives under plugins/job_search_plugin/migrations/."""
    steps = PluginSchemaMigrator().discover_steps(
        Path("plugins/job_search_plugin/migrations")
    )
    assert [s.revision for s in steps] == [
        "0001_init", "0002_portfolio_job_id", "0003_job_liveness",
        "0004_hybrid_search", "0005_profile_targeting_fields",
    ]
    assert steps[0].kind == "py"
    assert steps[1].kind == "py"


@pytest.fixture
def mock_session():
    with patch("plugins.job_search_plugin.store.get_async_session") as mock_get_session:
        mock_session_instance = AsyncMock()
        mock_session_instance.add = MagicMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session_instance
        yield mock_session_instance


@pytest.mark.asyncio
async def test_create_profile(mock_session):
    def fake_add(obj):
        obj.id = 101
        obj.created_at = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
        obj.updated_at = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    mock_session.add.side_effect = fake_add

    res = await store.create_profile(
        full_name="Alice Smith",
        email="alice@example.com",
        phone="123456"
    )

    assert res["id"] == 101
    assert res["full_name"] == "Alice Smith"
    assert res["email"] == "alice@example.com"
    assert res["phone"] == "123456"
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_profile(mock_session):
    profile_obj = JobApplicantProfile(
        id=101,
        full_name="Alice Smith",
        email="alice@example.com",
        phone="123456",
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = profile_obj
    mock_session.execute.return_value = mock_result

    res = await store.get_profile(101)
    assert res is not None
    assert res["full_name"] == "Alice Smith"
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_profile_new(mock_session):
    def fake_add(obj):
        obj.id = 102
        obj.created_at = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
        obj.updated_at = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    mock_session.add.side_effect = fake_add

    res = await store.upsert_profile(
        profile_id=None,
        full_name="Bob Jones",
        email="bob@example.com"
    )
    assert res["id"] == 102
    assert res["full_name"] == "Bob Jones"


@pytest.mark.asyncio
async def test_upsert_profile_existing(mock_session):
    profile_obj = JobApplicantProfile(
        id=102,
        full_name="Bob Jones Updated",
        email="bob@example.com",
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )

    mock_result_stmt = MagicMock()
    mock_result_fetch = MagicMock()
    mock_result_fetch.scalar_one_or_none.return_value = profile_obj

    mock_session.execute.side_effect = [mock_result_stmt, mock_result_fetch]

    res = await store.upsert_profile(
        profile_id=102,
        full_name="Bob Jones Updated",
        email="bob@example.com"
    )

    assert res["id"] == 102
    assert res["full_name"] == "Bob Jones Updated"
    assert mock_session.execute.call_count == 2


@pytest.mark.asyncio
async def test_create_application(mock_session):
    def fake_add(obj):
        obj.id = 456
        obj.created_at = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
        obj.updated_at = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    mock_session.add.side_effect = fake_add

    res = await store.create_application(
        applicant_profile_id=1,
        job_id="job_123",
        provider="linkedin",
        notes="First application"
    )

    assert res["id"] == 456
    assert res["applicant_profile_id"] == 1
    assert res["job_id"] == "job_123"
    assert res["provider"] == "linkedin"
    assert res["status"] == "drafted"
    assert res["notes"] == "First application"
    assert res["created_at"] == "2026-06-18T12:00:00+00:00"

    mock_session.add.assert_called_once()
    mock_session.flush.assert_called_once()
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_application(mock_session):
    app_obj = JobApplication(
        id=456,
        applicant_profile_id=1,
        job_id="job_123",
        provider="linkedin",
        status="drafted",
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = app_obj
    mock_session.execute.return_value = mock_result

    res = await store.get_application(456)
    assert res is not None
    assert res["id"] == 456
    assert res["job_id"] == "job_123"


@pytest.mark.asyncio
async def test_update_application(mock_session):
    existing_app = JobApplication(
        id=789,
        applicant_profile_id=1,
        job_id="job_123",
        provider="linkedin",
        status="drafted",
        notes="Old notes",
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_app
    mock_session.execute.return_value = mock_result

    res = await store.update_application(
        application_id=789,
        status="submitted"
    )

    assert res is not None
    assert res["id"] == 789
    assert res["status"] == "submitted"
    assert res["notes"] == "Old notes"

    mock_session.execute.assert_called_once()
    mock_session.flush.assert_called_once()
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_list_applications(mock_session):
    app_obj = JobApplication(
        id=202,
        applicant_profile_id=1,
        job_id="job_456",
        provider="indeed",
        status="drafted",
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [app_obj]
    mock_session.execute.return_value = mock_result

    res = await store.list_applications(applicant_profile_id=1, status="drafted")
    assert len(res) == 1
    assert res[0]["id"] == 202
    assert res[0]["provider"] == "indeed"
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_latest_agent_status_found(mock_session):
    app_obj = JobApplication(
        id=303,
        applicant_profile_id=1,
        job_id="job_789",
        provider="greenhouse",
        status="submitted",
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 16, 9, 0, 0, tzinfo=timezone.utc),
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = app_obj
    mock_session.execute.return_value = mock_result

    res = await store.get_latest_agent_status(job_id="job_789")

    assert res == {
        "job_id": "job_789",
        "status": "submitted",
        "updated_at": "2026-07-16T09:00:00+00:00",
    }
    # Privacy: only these 3 keys are ever returned — no applicant PII
    assert set(res.keys()) == {"job_id", "status", "updated_at"}
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_latest_agent_status_not_found(mock_session):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    res = await store.get_latest_agent_status(job_id="unknown_job")
    assert res is None


@pytest.mark.asyncio
async def test_get_latest_agent_status_tenant_scoped(mock_session):
    """Cross-tenant rows must not surface: filter uses resolved tenant_id."""
    from core.context import current_tenant_id

    tenant_a_app = JobApplication(
        id=401,
        applicant_profile_id=1,
        job_id="job_tenant_a",
        provider="greenhouse",
        status="submitted",
        tenant_id=1,
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 16, 10, 0, 0, tzinfo=timezone.utc),
    )
    tenant_b_app = JobApplication(
        id=402,
        applicant_profile_id=2,
        job_id="job_tenant_b",
        provider="lever",
        status="drafted",
        tenant_id=2,
        created_at=datetime(2026, 6, 18, 13, 0, 0, tzinfo=timezone.utc),
        # Newer than tenant A — without tenant filter this would win globally
        updated_at=datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc),
    )

    mock_result = MagicMock()
    # Simulate DB returning only the caller-tenant row after WHERE tenant_id=
    mock_result.scalar_one_or_none.return_value = tenant_a_app
    mock_session.execute.return_value = mock_result

    token = current_tenant_id.set(1)
    try:
        res = await store.get_latest_agent_status()
    finally:
        current_tenant_id.reset(token)

    assert res is not None
    assert res["job_id"] == "job_tenant_a"
    assert res["status"] == "submitted"
    assert set(res.keys()) == {"job_id", "status", "updated_at"}

    # Statement must constrain tenant_id (never unscoped)
    stmt = mock_session.execute.call_args[0][0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "tenant_id" in compiled
    assert "1" in compiled
    # Tenant B's newer job must not appear when scoped to tenant 1
    assert res["job_id"] != tenant_b_app.job_id


@pytest.mark.asyncio
async def test_get_latest_agent_status_explicit_tenant_overrides_contextvar(mock_session):
    """Explicit tenant_id wins over current_tenant_id contextvar."""
    from core.context import current_tenant_id

    app = JobApplication(
        id=403,
        applicant_profile_id=2,
        job_id="job_explicit",
        provider="indeed",
        status="submitted",
        tenant_id=7,
        created_at=datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 16, 9, 0, 0, tzinfo=timezone.utc),
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = app
    mock_session.execute.return_value = mock_result

    token = current_tenant_id.set(1)
    try:
        res = await store.get_latest_agent_status(tenant_id=7)
    finally:
        current_tenant_id.reset(token)

    assert res is not None
    assert res["job_id"] == "job_explicit"
    stmt = mock_session.execute.call_args[0][0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "tenant_id" in compiled
    assert "7" in compiled


@pytest.mark.asyncio
async def test_search_preferences(mock_session):
    mock_row_1 = MagicMock()
    mock_row_1.content = "Match 1"
    mock_row_1.source = "resume"
    mock_row_1.similarity = 0.85

    mock_row_2 = MagicMock()
    mock_row_2.content = "Match 2"
    mock_row_2.source = "preferences"
    mock_row_2.similarity = 0.72

    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row_1, mock_row_2]
    mock_session.execute.return_value = mock_result

    res = await store.search_preferences(
        applicant_profile_id=1,
        query_vector=[0.1, 0.2, 0.3],
        top_k=2
    )

    assert len(res) == 2
    assert res[0]["content"] == "Match 1"
    assert res[0]["source"] == "resume"
    assert res[0]["similarity"] == 0.85
    assert res[1]["content"] == "Match 2"
    assert res[1]["source"] == "preferences"
    assert res[1]["similarity"] == 0.72

    mock_session.execute.assert_called_once()
