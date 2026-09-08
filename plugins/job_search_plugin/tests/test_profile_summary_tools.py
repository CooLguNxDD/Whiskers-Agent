import json
import pytest
from unittest import mock

# ─── get_applicant_profile_summary ────────────────────────────────────────

_FULL_PROFILE = {
    "id": 7,
    "full_name": "Casey Candidate",
    "email": "casey@example.com",
    "phone": "555-0100",
    "base_resume_text": "A" * 2000,
    "cover_letter_template": "Dear Hiring Manager...",
    "resume_object_key": "resume.pdf",
    "preferences_text": "Remote only, Staff+ roles",
    "location": "Remote - US",
    "target_titles": ["Staff Engineer", "Principal Engineer"],
    "top_skills": ["Python", "LangGraph", "Postgres"],
    "constraints_text": "No relocation",
    "notice_period_days": 30,
    "core_technologies": ["Python", "TypeScript"],
    "methodologies": ["TDD", "GOAP"],
    "languages": ["English"],
    "created_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-01-01T00:00:00+00:00",
}


@pytest.mark.asyncio
async def test_get_applicant_profile_summary_compact_tier():
    from plugins.job_search_plugin.MCPTools.profile_tools import get_applicant_profile_summary

    with mock.patch(
        "plugins.job_search_plugin.store.get_profile", new_callable=mock.AsyncMock
    ) as mock_get:
        mock_get.return_value = _FULL_PROFILE
        res = await get_applicant_profile_summary(applicant_profile_id=7, tier="compact")

    assert set(res.keys()) == {
        "id", "full_name", "email", "location", "target_titles",
        "top_skills", "constraints_text", "notice_period_days",
    }
    assert "base_resume_text" not in res
    assert "preferences_text" not in res
    # Character budget, not a token count — no tokenizer is a project dependency.
    assert len(json.dumps(res)) < 1000


@pytest.mark.asyncio
async def test_get_applicant_profile_summary_skills_only_tier():
    from plugins.job_search_plugin.MCPTools.profile_tools import get_applicant_profile_summary

    with mock.patch(
        "plugins.job_search_plugin.store.get_profile", new_callable=mock.AsyncMock
    ) as mock_get:
        mock_get.return_value = _FULL_PROFILE
        res = await get_applicant_profile_summary(applicant_profile_id=7, tier="skills_only")

    assert set(res.keys()) == {"id", "full_name", "core_technologies", "methodologies", "languages"}
    assert "base_resume_text" not in res


@pytest.mark.asyncio
async def test_get_applicant_profile_summary_full_tier():
    from plugins.job_search_plugin.MCPTools.profile_tools import get_applicant_profile_summary

    with mock.patch(
        "plugins.job_search_plugin.store.get_profile", new_callable=mock.AsyncMock
    ) as mock_get:
        mock_get.return_value = _FULL_PROFILE
        res = await get_applicant_profile_summary(applicant_profile_id=7, tier="full")

    assert res == _FULL_PROFILE
    assert "base_resume_text" in res


@pytest.mark.asyncio
async def test_get_applicant_profile_summary_invalid_tier():
    from plugins.job_search_plugin.MCPTools.profile_tools import get_applicant_profile_summary

    res = await get_applicant_profile_summary(applicant_profile_id=7, tier="bogus")
    assert res["status"] == "error"
    assert res["error"] == "invalid_tier"


@pytest.mark.asyncio
async def test_get_applicant_profile_summary_not_found():
    from plugins.job_search_plugin.MCPTools.profile_tools import get_applicant_profile_summary

    with mock.patch(
        "plugins.job_search_plugin.store.get_profile", new_callable=mock.AsyncMock
    ) as mock_get:
        mock_get.return_value = None
        res = await get_applicant_profile_summary(applicant_profile_id=999, tier="compact")

    assert res == {"status": "error", "error": "profile_not_found"}


# ─── get_candidate_proof_points ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_candidate_proof_points_empty_keywords():
    from plugins.job_search_plugin.MCPTools.profile_tools import get_candidate_proof_points

    res = await get_candidate_proof_points(applicant_profile_id=7, keywords=[])
    assert res == {"status": "error", "error": "invalid_keywords"}


@pytest.mark.asyncio
async def test_get_candidate_proof_points_not_found():
    from plugins.job_search_plugin.MCPTools.profile_tools import get_candidate_proof_points

    with mock.patch(
        "plugins.job_search_plugin.store.get_profile", new_callable=mock.AsyncMock
    ) as mock_get:
        mock_get.return_value = None
        res = await get_candidate_proof_points(applicant_profile_id=999, keywords=["python"])

    assert res == {"status": "error", "error": "profile_not_found"}


@pytest.mark.asyncio
async def test_get_candidate_proof_points_success():
    from plugins.job_search_plugin.MCPTools import profile_tools

    mock_chunks = [
        {"content": "Built a multi-agent LangGraph pipeline", "source": "resume", "similarity": 0.91},
        {"content": "Staff-level distributed systems work", "source": "resume", "similarity": 0.85},
    ]

    with mock.patch(
        "plugins.job_search_plugin.store.get_profile", new_callable=mock.AsyncMock
    ) as mock_get, mock.patch(
        "plugins.job_search_plugin.store.search_preferences_hybrid", new_callable=mock.AsyncMock
    ) as mock_search:
        mock_get.return_value = _FULL_PROFILE
        mock_search.return_value = mock_chunks

        res = await profile_tools.get_candidate_proof_points(
            applicant_profile_id=7, keywords=["LangGraph", "multi-agent"], max_bullets=2
        )

    assert res["status"] == "ok"
    assert res["hybrid_used"] is True
    assert len(res["proof_points"]) == 2
    assert res["proof_points"][0]["content"] == mock_chunks[0]["content"]
    mock_search.assert_awaited_once()
    call_args = mock_search.await_args
    assert call_args.args[0] == 7
    assert call_args.kwargs["top_k"] == 2


# ─── sync_application_status ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_application_status_invalid_status():
    from plugins.job_search_plugin.MCPTools.application_tools import sync_application_status

    res = await sync_application_status(applicant_profile_id=1, job_id="job_1", status="bogus")
    assert res["status"] == "error"
    assert res["error"] == "invalid_status"


@pytest.mark.asyncio
async def test_sync_application_status_missing_job_id():
    from plugins.job_search_plugin.MCPTools.application_tools import sync_application_status

    res = await sync_application_status(applicant_profile_id=1, job_id="", status="applied")
    assert res["status"] == "error"
    assert res["error"] == "missing_required_fields"


@pytest.mark.asyncio
async def test_sync_application_status_creates_then_updates():
    from plugins.job_search_plugin.MCPTools.application_tools import sync_application_status

    with mock.patch(
        "plugins.job_search_plugin.store.upsert_application_status", new_callable=mock.AsyncMock
    ) as mock_upsert:
        mock_upsert.return_value = ({"id": 1, "job_id": "job_1", "status": "drafted"}, True)
        res_created = await sync_application_status(
            applicant_profile_id=1, job_id="job_1", status="drafted"
        )
        assert res_created["status"] == "ok"
        assert res_created["created"] is True

        mock_upsert.return_value = ({"id": 1, "job_id": "job_1", "status": "applied"}, False)
        res_updated = await sync_application_status(
            applicant_profile_id=1, job_id="job_1", status="applied", notes="submitted via portal"
        )
        assert res_updated["status"] == "ok"
        assert res_updated["created"] is False
        assert res_updated["application"]["status"] == "applied"


# ─── store.upsert_application_status idempotency + tenant scoping ─────────

@pytest.mark.asyncio
async def test_store_upsert_application_status_idempotent(monkeypatch):
    """Second sync call on the same (profile, job_id) updates the existing row, no duplicate."""
    from plugins.job_search_plugin import store

    class _FakeResult:
        def __init__(self, row):
            self._row = row

        def scalar_one_or_none(self):
            return self._row

    class _FakeApp:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)
            self.id = 1
            self.created_at = None
            self.updated_at = None

    existing = _FakeApp(
        applicant_profile_id=1, job_id="job_1", provider="greenhouse",
        provider_application_id=None,
        status="drafted", notes=None, resume_object_key=None,
        cover_letter_object_key=None, portfolio_job_id=None, tenant_id=1,
    )

    class _FakeSession:
        def __init__(self):
            self.executed = []

        async def execute(self, stmt):
            self.executed.append(stmt)
            return _FakeResult(existing)

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

    class _CtxMgr:
        def __call__(self):
            return fake_session

    monkeypatch.setattr(store, "get_async_session", lambda: fake_session)

    fake_tenant_cv = mock.Mock()
    fake_tenant_cv.get.return_value = 1
    monkeypatch.setattr("core.context.current_tenant_id", fake_tenant_cv)

    app, created = await store.upsert_application_status(1, "job_1", "applied")

    assert created is False
    assert existing.status == "applied"
