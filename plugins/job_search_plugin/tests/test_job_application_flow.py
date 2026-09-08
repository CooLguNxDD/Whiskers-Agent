import pytest
from unittest import mock

# ─── Profile Tools Tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_applicant_profile():
    from plugins.job_search_plugin.MCPTools.profile_tools import get_applicant_profile
    
    with mock.patch("plugins.job_search_plugin.store.get_profile", new_callable=mock.AsyncMock) as mock_get:
        # 1. Profile not found
        mock_get.return_value = None
        res_none = await get_applicant_profile(applicant_profile_id=999)
        assert res_none["status"] == "error"
        assert res_none["error"] == "profile_not_found"
        
        # 2. Profile found
        mock_profile = {"id": 999, "full_name": "Alice Developer"}
        mock_get.return_value = mock_profile
        res_found = await get_applicant_profile(applicant_profile_id=999)
        assert res_found == mock_profile
        assert mock_get.await_count == 2



@pytest.mark.asyncio
async def test_upsert_applicant_profile():
    from plugins.job_search_plugin.MCPTools.profile_tools import upsert_applicant_profile
    
    # 1. Validation check on create (applicant_profile_id = None)
    res_val = await upsert_applicant_profile(applicant_profile_id=None, full_name="", email="")
    assert res_val["status"] == "error"
    assert res_val["error"] == "missing_required_fields"
    assert "full_name" in res_val["missing_fields"]
    assert "email" in res_val["missing_fields"]
    
    # 2. Success check on create
    mock_profile = {
        "id": 500,
        "full_name": "Bob Smith",
        "email": "bob@example.com",
        "phone": "123-456",
        "base_resume_text": "Bob's Resume",
    }
    with mock.patch("plugins.job_search_plugin.store.upsert_profile", new_callable=mock.AsyncMock) as mock_upsert:
        mock_upsert.return_value = mock_profile
        res = await upsert_applicant_profile(
            applicant_profile_id=None,
            full_name="Bob Smith",
            email="bob@example.com",
            phone="123-456",
            base_resume_text="Bob's Resume",
        )
        assert res == mock_profile
        mock_upsert.assert_awaited_once_with(
            None,
            full_name="Bob Smith",
            email="bob@example.com",
            phone="123-456",
            base_resume_text="Bob's Resume",
            cover_letter_template="",
            preferences_text="",
            location="",
            target_titles=[],
            top_skills=[],
            constraints_text="",
            notice_period_days=None,
            core_technologies=[],
            methodologies=[],
            languages=[],
        )


# ─── Application Tools Tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_application():
    from plugins.job_search_plugin.MCPTools.application_tools import create_application
    
    # 1. Validation error check
    val_res = await create_application(job_id="", provider="", applicant_profile_id=None)
    assert val_res["status"] == "error"
    assert val_res["error"] == "missing_required_fields"
    assert "job_id" in val_res["missing_fields"]
    assert "provider" in val_res["missing_fields"]
    assert "applicant_profile_id" in val_res["missing_fields"]

    # 2. Success flow with mock
    mock_app = {
        "id": 42,
        "job_id": "job_123",
        "provider": "greenhouse",
        "applicant_profile_id": 1,
        "status": "drafted",
        "resume_object_key": "res.pdf",
        "cover_letter_object_key": "cl.pdf",
        "notes": "my notes",
    }
    with mock.patch("plugins.job_search_plugin.store.create_application", new_callable=mock.AsyncMock) as mock_create:
        mock_create.return_value = mock_app
        res = await create_application(
            job_id="job_123",
            provider="greenhouse",
            applicant_profile_id=1,
            resume_object_key="res.pdf",
            cover_letter_object_key="cl.pdf",
            notes="my notes"
        )
        assert res["id"] == 42
        assert res["status"] == "drafted"
        mock_create.assert_awaited_once_with(
            1, "job_123", "greenhouse",
            resume_object_key="res.pdf",
            cover_letter_object_key="cl.pdf",
            notes="my notes",
            portfolio_job_id=None,
        )


@pytest.mark.asyncio
async def test_create_application_with_portfolio_job_id():
    from plugins.job_search_plugin.MCPTools.application_tools import create_application

    mock_app = {
        "id": 43,
        "job_id": "job_456",
        "provider": "greenhouse",
        "applicant_profile_id": 1,
        "status": "drafted",
        "portfolio_job_id": "whiskers_successor_992",
    }
    with mock.patch("plugins.job_search_plugin.store.create_application", new_callable=mock.AsyncMock) as mock_create:
        mock_create.return_value = mock_app
        res = await create_application(
            job_id="job_456",
            provider="greenhouse",
            applicant_profile_id=1,
            portfolio_job_id="whiskers_successor_992",
        )
        assert res["portfolio_job_id"] == "whiskers_successor_992"
        mock_create.assert_awaited_once_with(
            1, "job_456", "greenhouse",
            resume_object_key="",
            cover_letter_object_key="",
            notes="",
            portfolio_job_id="whiskers_successor_992",
        )


@pytest.mark.asyncio
async def test_submit_application_rest_provider():
    from plugins.job_search_plugin.MCPTools.application_tools import submit_application
    
    app_data = {
        "id": 101,
        "job_id": "job_greenhouse",
        "provider": "greenhouse",
        "applicant_profile_id": 9,
        "status": "drafted",
        "resume_object_key": "res.pdf",
        "cover_letter_object_key": "cl.pdf",
        "notes": "some notes",
    }
    profile_data = {
        "id": 9,
        "full_name": "Alice Developer",
        "email": "alice@example.com",
        "phone": "555-0199",
        "base_resume_text": "Alice's Resume",
    }
    updated_app_data = {
        **app_data,
        "status": "submitted",
        "provider_application_id": "greenhouse_app_123",
    }

    with mock.patch("plugins.job_search_plugin.store.get_application", new_callable=mock.AsyncMock) as mock_get_app, \
         mock.patch("plugins.job_search_plugin.store.get_profile", new_callable=mock.AsyncMock) as mock_get_profile, \
         mock.patch("plugins.job_search_plugin.store.update_application", new_callable=mock.AsyncMock) as mock_update_app, \
         mock.patch("requests.post") as mock_post:
        
        mock_get_app.return_value = app_data
        mock_get_profile.return_value = profile_data
        mock_update_app.return_value = updated_app_data
        
        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "greenhouse_app_123"}
        mock_resp.text = '{"id": "greenhouse_app_123"}'
        mock_post.return_value = mock_resp
        
        res = await submit_application(application_id=101)
        
        assert res["status"] == "ok"
        assert res["submitted"] is True
        assert res["application"]["status"] == "submitted"
        assert res["application"]["provider_application_id"] == "greenhouse_app_123"
        
        mock_get_app.assert_awaited_once_with(101)
        mock_get_profile.assert_awaited_once_with(9)
        mock_update_app.assert_awaited_once_with(
            101, status="submitted", provider_application_id="greenhouse_app_123"
        )
        mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_submit_application_no_api_provider():
    from plugins.job_search_plugin.MCPTools.application_tools import submit_application
    
    app_data = {
        "id": 102,
        "job_id": "job_linkedin",
        "provider": "linkedin",
        "applicant_profile_id": 9,
        "status": "drafted",
        "resume_object_key": "res_key_abc",
        "cover_letter_object_key": "cl_key_abc",
        "notes": "",
    }
    profile_data = {
        "id": 9,
        "full_name": "Alice Developer",
        "email": "alice@example.com",
    }
    
    with mock.patch("plugins.job_search_plugin.store.get_application", new_callable=mock.AsyncMock) as mock_get_app, \
         mock.patch("plugins.job_search_plugin.store.get_profile", new_callable=mock.AsyncMock) as mock_get_profile, \
         mock.patch("plugins.job_search_plugin.store.update_application", new_callable=mock.AsyncMock) as mock_update_app, \
         mock.patch("requests.post") as mock_post:
         
        mock_get_app.return_value = app_data
        mock_get_profile.return_value = profile_data
        
        res = await submit_application(application_id=102)
        
        assert res["status"] == "needs_browser_apply"
        assert res["application_id"] == 102
        assert res["profile"] == profile_data
        assert res["resume_object_key"] == "res_key_abc"
        
        mock_get_app.assert_awaited_once_with(102)
        mock_get_profile.assert_awaited_once_with(9)
        mock_update_app.assert_not_called()
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_mark_application_submitted():
    from plugins.job_search_plugin.MCPTools.application_tools import mark_application_submitted
    
    updated_app = {
        "id": 103,
        "status": "submitted",
        "provider_application_id": "prov_abc_123",
    }
    with mock.patch("plugins.job_search_plugin.store.update_application", new_callable=mock.AsyncMock) as mock_update:
        mock_update.return_value = updated_app
        res = await mark_application_submitted(application_id=103, provider_application_id="prov_abc_123")
        assert res["status"] == "submitted"
        assert res["provider_application_id"] == "prov_abc_123"
        mock_update.assert_awaited_once_with(103, status="submitted", provider_application_id="prov_abc_123")


@pytest.mark.asyncio
async def test_update_application():
    from plugins.job_search_plugin.MCPTools.application_tools import update_application
    
    # Invalid status check
    res_invalid = await update_application(application_id=104, status="invalid_status_value")
    assert res_invalid["status"] == "error"
    assert res_invalid["error"] == "invalid_status"
    
    # Valid status check (delegates to store with only provided fields)
    mock_app = {"id": 104, "status": "interview", "notes": "some notes"}
    with mock.patch("plugins.job_search_plugin.store.update_application", new_callable=mock.AsyncMock) as mock_update:
        mock_update.return_value = mock_app
        # only pass status
        res_status = await update_application(application_id=104, status="interview")
        assert res_status == mock_app
        mock_update.assert_awaited_once_with(104, status="interview")
        
        mock_update.reset_mock()
        # only pass notes
        res_notes = await update_application(application_id=104, notes="some notes")
        assert res_notes == mock_app
        mock_update.assert_awaited_once_with(104, notes="some notes")


@pytest.mark.asyncio
async def test_list_applications():
    from plugins.job_search_plugin.MCPTools.application_tools import list_applications
    
    mock_apps = [{"id": 1, "status": "drafted"}]
    with mock.patch("plugins.job_search_plugin.store.list_applications", new_callable=mock.AsyncMock) as mock_list:
        mock_list.return_value = mock_apps
        
        res = await list_applications(applicant_profile_id=1, status_filter="drafted")
        assert res["status"] == "ok"
        assert res["count"] == 1
        assert res["applications"] == mock_apps
        mock_list.assert_awaited_once_with(applicant_profile_id=1, status="drafted")
