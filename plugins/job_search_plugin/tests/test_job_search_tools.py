"""Unit tests for Job Search Plugin tools.
"""

import pytest
from unittest.mock import patch, MagicMock

from plugins.job_search_plugin.MCPTools.job_search_tools import (
    search_jobs,
    get_job_details,
)


@pytest.mark.asyncio
async def test_search_jobs_no_api_provider():
    """Verify search_jobs with a NO_API provider doesn't call HTTP and needs scrape."""
    with patch("plugins.job_search_plugin.MCPTools.job_search_tools.requests.get") as mock_get:
        res = await search_jobs(query="Python Developer", location="Remote", providers=["linkedin"])
        assert res["status"] == "ok"
        assert res["count"] == 1
        assert len(res["results"]) == 1
        assert res["results"][0]["provider"] == "linkedin"
        assert res["results"][0]["needs_browser_scrape"] is True
        assert "linkedin" in res["browser_scrape_needed"]
        mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_search_jobs_aggregates_mocked_rest_provider():
    """Verify search_jobs aggregates normalized results from a mocked REST provider."""
    async def mock_vault_get(key):
        return "fake_key"

    mock_remotive_response = MagicMock()
    mock_remotive_response.status_code = 200
    mock_remotive_response.text = '{"jobs": [{"id": 12345, "title": "Software Engineer", "company_name": "Acme Corp", "candidate_required_location": "USA", "url": "https://remotive.com/job/12345"}]}'
    mock_remotive_response.json.return_value = {
        "jobs": [
            {
                "id": 12345,
                "title": "Software Engineer",
                "company_name": "Acme Corp",
                "candidate_required_location": "USA",
                "url": "https://remotive.com/job/12345"
            }
        ]
    }

    with patch("plugins.job_search_plugin.MCPTools.job_search_tools._get_vault_key", side_effect=mock_vault_get), \
         patch("plugins.job_search_plugin.MCPTools.job_search_tools.requests.get", return_value=mock_remotive_response):

        res = await search_jobs(query="Software Engineer", location="USA", providers=["remotive"])
        assert res["status"] == "ok"
        assert res["count"] == 1
        assert len(res["results"]) == 1
        assert res["results"][0]["provider"] == "remotive"
        assert res["results"][0]["job_id"] == "12345"
        assert res["results"][0]["title"] == "Software Engineer"
        assert res["results"][0]["company"] == "Acme Corp"
        assert res["results"][0]["location"] == "USA"
        assert res["results"][0]["url"] == "https://remotive.com/job/12345"


@pytest.mark.asyncio
async def test_search_jobs_skips_missing_vault_key():
    """Verify search_jobs skips a provider whose vault key is missing."""
    async def mock_vault_get_none(key):
        return None

    with patch("plugins.job_search_plugin.MCPTools.job_search_tools._get_vault_key", side_effect=mock_vault_get_none):
        res = await search_jobs(query="Designer", location="NY", providers=["greenhouse"])
        assert res["status"] == "ok"
        assert res["count"] == 0
        assert "greenhouse" in res["skipped"]


@pytest.mark.asyncio
async def test_get_job_details_no_api():
    """Verify get_job_details for a no-API provider returns error."""
    res = await get_job_details(job_id="123", provider="linkedin")
    assert res["status"] == "error"
    assert res["error"] == "no_api_for_provider"
    assert res["needs_browser_scrape"] is True


@pytest.mark.asyncio
async def test_get_job_details_rest_provider():
    """Verify get_job_details returns description text for a mocked REST provider."""
    async def mock_vault_get(key):
        return "fake_key"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"id": "lever-job-123", "title": "DevOps Engineer", "description": "Awesome job description here", "company": "Lever Client", "location": "Remote", "url": "https://jobs.lever.co/client/lever-job-123"}'
    mock_response.json.return_value = {
        "id": "lever-job-123",
        "title": "DevOps Engineer",
        "description": "Awesome job description here",
        "company": "Lever Client",
        "location": "Remote",
        "url": "https://jobs.lever.co/client/lever-job-123"
    }

    with patch("plugins.job_search_plugin.MCPTools.job_search_tools._get_vault_key", side_effect=mock_vault_get), \
         patch("plugins.job_search_plugin.MCPTools.job_search_tools.requests.get", return_value=mock_response):

        res = await get_job_details(job_id="lever-job-123", provider="lever")
        assert res["status"] == "ok"
        assert res["job_id"] == "lever-job-123"
        assert res["provider"] == "lever"
        assert res["title"] == "DevOps Engineer"
        assert res["description"] == "Awesome job description here"
