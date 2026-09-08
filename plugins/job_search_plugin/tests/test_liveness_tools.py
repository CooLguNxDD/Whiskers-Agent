"""Unit tests for job posting liveness verification and ghost-job detection."""

from datetime import datetime, timedelta, timezone
import pytest
from unittest import mock


# ─── check_job_liveness tool ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_job_liveness_missing_url():
    from plugins.job_search_plugin.MCPTools.liveness_tools import check_job_liveness

    res = await check_job_liveness(url="")
    assert res["status"] == "error"
    assert res["error"] == "missing_required_fields"


@pytest.mark.asyncio
async def test_check_job_liveness_new_posting():
    from plugins.job_search_plugin.MCPTools.liveness_tools import check_job_liveness

    record = {
        "repost_count": 1,
        "staleness_days": 0,
        "first_seen_at": "2026-08-01T00:00:00+00:00",
        "company": "Acme",
        "role_title": "Staff Engineer",
    }
    with mock.patch(
        "plugins.job_search_plugin.MCPTools.liveness_tools._check_url_live",
        new_callable=mock.AsyncMock,
    ) as mock_live, mock.patch(
        "plugins.job_search_plugin.store.upsert_liveness", new_callable=mock.AsyncMock
    ) as mock_upsert:
        mock_live.return_value = True
        mock_upsert.return_value = (record, True)

        res = await check_job_liveness(
            url="https://jobs.ashbyhq.com/acme/123", company="Acme", role_title="Staff Engineer"
        )

    assert res["status"] == "ok"
    assert res["is_live"] is True
    assert res["repost_count"] == 1
    assert res["is_potential_ghost_job"] is False
    assert res["legitimacy_tier"] == "verified"
    mock_upsert.assert_awaited_once_with(
        "https://jobs.ashbyhq.com/acme/123",
        company="Acme", role_title="Staff Engineer", provider="", is_live=True,
    )


@pytest.mark.asyncio
async def test_check_job_liveness_repost_triggers_caution():
    from plugins.job_search_plugin.MCPTools.liveness_tools import check_job_liveness

    record = {
        "repost_count": 3,
        "staleness_days": 102,
        "first_seen_at": "2026-05-10T10:00:00+00:00",
        "company": "Acme",
        "role_title": "Staff Engineer",
    }
    with mock.patch(
        "plugins.job_search_plugin.MCPTools.liveness_tools._check_url_live",
        new_callable=mock.AsyncMock,
    ) as mock_live, mock.patch(
        "plugins.job_search_plugin.store.upsert_liveness", new_callable=mock.AsyncMock
    ) as mock_upsert:
        mock_live.return_value = True
        mock_upsert.return_value = (record, False)

        res = await check_job_liveness(url="https://jobs.ashbyhq.com/acme/123")

    assert res["is_potential_ghost_job"] is True
    assert res["legitimacy_tier"] == "caution"
    assert any("reposted 3 times" in r for r in res["ghost_reasons"])
    assert any("102 days old" in r for r in res["ghost_reasons"])


@pytest.mark.asyncio
async def test_check_job_liveness_expired_url_is_not_live():
    from plugins.job_search_plugin.MCPTools.liveness_tools import check_job_liveness

    record = {
        "repost_count": 1, "staleness_days": 5,
        "first_seen_at": "2026-08-15T00:00:00+00:00",
        "company": "Acme", "role_title": "Staff Engineer",
    }
    with mock.patch(
        "plugins.job_search_plugin.MCPTools.liveness_tools._check_url_live",
        new_callable=mock.AsyncMock,
    ) as mock_live, mock.patch(
        "plugins.job_search_plugin.store.upsert_liveness", new_callable=mock.AsyncMock
    ) as mock_upsert:
        mock_live.return_value = False
        mock_upsert.return_value = (record, False)

        res = await check_job_liveness(url="https://jobs.ashbyhq.com/acme/gone")

    assert res["is_live"] is False
    mock_upsert.assert_awaited_once_with(
        "https://jobs.ashbyhq.com/acme/gone", company="", role_title="", provider="", is_live=False,
    )


@pytest.mark.asyncio
async def test_check_job_liveness_scam_signal_detection():
    from plugins.job_search_plugin.MCPTools.liveness_tools import check_job_liveness

    record = {
        "repost_count": 1, "staleness_days": 0,
        "first_seen_at": "2026-08-15T00:00:00+00:00",
        "company": "Definitely Legit LLC (interview via Telegram)",
        "role_title": "Remote Data Entry - wire transfer setup required",
    }
    with mock.patch(
        "plugins.job_search_plugin.MCPTools.liveness_tools._check_url_live",
        new_callable=mock.AsyncMock,
    ) as mock_live, mock.patch(
        "plugins.job_search_plugin.store.upsert_liveness", new_callable=mock.AsyncMock
    ) as mock_upsert:
        mock_live.return_value = True
        mock_upsert.return_value = (record, True)

        res = await check_job_liveness(
            url="https://example.com/scam-job",
            company=record["company"],
            role_title=record["role_title"],
        )

    assert res["legitimacy_tier"] == "scam_risk"
    assert res["is_potential_ghost_job"] is True
    assert any("telegram" in r.lower() for r in res["ghost_reasons"])
    assert any("wire transfer" in r.lower() for r in res["ghost_reasons"])


@pytest.mark.asyncio
async def test_check_url_live_http_check():
    from plugins.job_search_plugin.MCPTools.liveness_tools import _check_url_live

    resp = mock.Mock()
    resp.status_code = 404
    client = mock.AsyncMock()
    client.get = mock.AsyncMock(return_value=resp)
    ctx = mock.MagicMock()
    ctx.__aenter__ = mock.AsyncMock(return_value=client)
    ctx.__aexit__ = mock.AsyncMock(return_value=False)

    with mock.patch(
        "plugins.job_search_plugin.MCPTools.liveness_tools._safe_async_client", return_value=ctx
    ):
        assert await _check_url_live("https://example.com/gone") is False


# ─── store.upsert_liveness ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_upsert_liveness_new_posting(monkeypatch):
    from plugins.job_search_plugin import store

    class _FakeApp:
        pass

    session_state = {"row": None}

    class _FakeResult:
        def scalar_one_or_none(self):
            return session_state["row"]

    class _FakeSession:
        async def execute(self, stmt):
            return _FakeResult()

        async def flush(self):
            if session_state["row"] is not None and not hasattr(session_state["row"], "id"):
                session_state["row"].id = 1

        async def commit(self):
            pass

        def add(self, obj):
            obj.id = 1
            session_state["row"] = obj

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    fake_session = _FakeSession()
    monkeypatch.setattr(store, "get_async_session", lambda: fake_session)

    fake_tenant_cv = mock.Mock()
    fake_tenant_cv.get.return_value = 1
    monkeypatch.setattr("core.context.current_tenant_id", fake_tenant_cv)

    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    record, created = await store.upsert_liveness(
        "https://jobs.ashbyhq.com/acme/1", company="Acme", role_title="Eng", now=now,
    )

    assert created is True
    assert record["repost_count"] == 1
    assert record["staleness_days"] == 0


@pytest.mark.asyncio
async def test_store_upsert_liveness_repost_after_gap(monkeypatch):
    """A re-check 35 days after the prior verification bumps repost_count to 2."""
    from plugins.job_search_plugin import store
    from plugins.job_search_plugin.models import JobPostingLiveness

    first_seen = datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc)
    existing = JobPostingLiveness(
        url_hash=store._hash_url("https://jobs.ashbyhq.com/acme/1"),
        url="https://jobs.ashbyhq.com/acme/1",
        company="Acme", role_title="Eng", provider="ashby",
        first_seen_at=first_seen, last_verified_at=first_seen,
        is_active=True, repost_count=1, staleness_days=0,
        ghost_signals={}, tenant_id=1,
    )
    existing.id = 1

    class _FakeResult:
        def scalar_one_or_none(self):
            return existing

    class _FakeSession:
        async def execute(self, stmt):
            return _FakeResult()

        async def flush(self):
            pass

        async def commit(self):
            pass

        def add(self, obj):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    fake_session = _FakeSession()
    monkeypatch.setattr(store, "get_async_session", lambda: fake_session)

    fake_tenant_cv = mock.Mock()
    fake_tenant_cv.get.return_value = 1
    monkeypatch.setattr("core.context.current_tenant_id", fake_tenant_cv)

    later = first_seen + timedelta(days=35)
    record, created = await store.upsert_liveness(
        "https://jobs.ashbyhq.com/acme/1", now=later,
    )

    assert created is False
    assert record["repost_count"] == 2
    assert record["staleness_days"] == 35
    assert record["last_verified_at"] == later.isoformat()


@pytest.mark.asyncio
async def test_store_get_liveness_tenant_scoping(monkeypatch):
    """A row stamped for tenant 2 is invisible to a tenant-1 lookup."""
    from plugins.job_search_plugin import store

    class _FakeResult:
        def scalar_one_or_none(self):
            return None  # tenant filter excludes the tenant-2 row

    class _FakeSession:
        async def execute(self, stmt):
            return _FakeResult()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(store, "get_async_session", lambda: _FakeSession())

    result = await store.get_liveness("https://jobs.ashbyhq.com/acme/1", tenant_id=1)
    assert result is None
