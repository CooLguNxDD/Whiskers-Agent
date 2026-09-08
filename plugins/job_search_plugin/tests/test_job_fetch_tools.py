"""Unit tests for fetch_job_posting (unified zero-auth ATS ingestion)."""

import pytest
from unittest import mock

from plugins.job_search_plugin.MCPTools.job_search_tools import fetch_job_posting


def _make_async_client(json_data=None, raise_status=False, http_error=None):
    """Build a mock object usable as `async with _safe_async_client(...) as client`."""
    client = mock.AsyncMock()

    resp = mock.Mock()
    if raise_status:
        resp.raise_for_status.side_effect = Exception("HTTP error")
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = json_data or {}

    if http_error:
        client.get = mock.AsyncMock(side_effect=http_error)
    else:
        client.get = mock.AsyncMock(return_value=resp)

    ctx = mock.MagicMock()
    ctx.__aenter__ = mock.AsyncMock(return_value=client)
    ctx.__aexit__ = mock.AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
async def test_fetch_job_posting_missing_url_and_raw_text():
    res = await fetch_job_posting()
    assert res["status"] == "error"
    assert res["error"] == "missing_required_fields"


@pytest.mark.asyncio
async def test_fetch_job_posting_raw_text_skips_fetch():
    with mock.patch(
        "plugins.job_search_plugin.MCPTools.job_search_tools._safe_async_client"
    ) as mock_client:
        res = await fetch_job_posting(raw_text="Some pasted JD text")

    assert res["status"] == "ok"
    assert res["provider"] == "raw_text"
    assert res["clean_description"] == "Some pasted JD text"
    assert res["title"]  # paste parse fills structured slots, not empty ATS-shaped fields
    mock_client.assert_not_called()


INDEED_PROBE_URL = (
    "https://ca.indeed.com/viewjob?jk=ded0d7160e9a67f0&fromjk=f74eff626fe1aa7f"
)
CORGTA_PASTE = """Intermediate Fullstack Developer (Typescript / React / Node)
CorGTA
Montréal, QC • Remote (Canada)
$110,000–$150,000 a year - Full-time

Full job description
CorGTA is looking for an Intermediate Fullstack Developer to support one of our clients in a contract capacity.
"""


@pytest.mark.asyncio
async def test_fetch_job_posting_raw_text_only_structured():
    with mock.patch(
        "plugins.job_search_plugin.MCPTools.job_search_tools._safe_async_client"
    ) as mock_client:
        res = await fetch_job_posting(raw_text=CORGTA_PASTE)

    assert res["status"] == "ok"
    assert res["ingest_method"] == "paste"
    assert res["title"].startswith("Intermediate Fullstack Developer")
    assert res["via"] == "CorGTA" or res["company"]
    assert res["location"]
    mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_job_posting_url_plus_raw_text_keeps_indeed_jk():
    with mock.patch(
        "core.route_registry.execute.execute_operation", new_callable=mock.AsyncMock
    ) as mock_exec:
        res = await fetch_job_posting(url=INDEED_PROBE_URL, raw_text=CORGTA_PASTE)

    mock_exec.assert_not_called()
    assert res["status"] == "ok"
    assert res["provider"] == "indeed"
    assert res["job_id"] == "ded0d7160e9a67f0"
    assert res["source_url"] == INDEED_PROBE_URL
    assert res["title"].startswith("Intermediate Fullstack Developer")
    assert res["via"] == "CorGTA"
    assert res["end_employer"] is None
    assert res["company"] == ""
    assert "Montréal" in res["location"] or "Montreal" in res["location"]
    assert res["needs_browser_scrape"] is False


@pytest.mark.asyncio
async def test_fetch_job_posting_indeed_cloudflare_without_raw_text():
    with mock.patch(
        "core.route_registry.execute.execute_operation", new_callable=mock.AsyncMock
    ) as mock_exec:
        mock_exec.return_value = {
            "status": "error",
            "message": "Client error '401 Unauthorized' for url 'https://ca.indeed.com/viewjob?jk=ded0d7160e9a67f0' — Cloudflare",
        }
        res = await fetch_job_posting(url=INDEED_PROBE_URL)

    assert res["error"] == "needs_browser_scrape"
    assert res["needs_browser_scrape"] is True
    assert res["is_potential_ghost_job"] is False
    assert res["ingest_status"] == "needs_browser_scrape"
    assert res["job_id"] == "ded0d7160e9a67f0"
    assert res["provider"] == "indeed"
    assert not (res.get("clean_description") or "").strip()


@pytest.mark.asyncio
async def test_fetch_job_posting_ashby_extracts_compensation():
    ashby_response = {
        "jobs": [
            {
                "id": "job-abc-123",
                "title": "Staff AI Systems Engineer",
                "location": {"name": "Remote - US/Canada"},
                "descriptionPlain": "We are seeking a Staff AI Systems Engineer to lead...",
                "publishedAt": "2026-08-15T12:00:00Z",
                "compensation": {
                    "summaryComponents": [
                        {"minValue": 180000, "maxValue": 240000, "currencyCode": "USD"}
                    ]
                },
            }
        ]
    }

    with mock.patch(
        "plugins.job_search_plugin.MCPTools.job_search_tools._safe_async_client",
        return_value=_make_async_client(json_data=ashby_response),
    ), mock.patch(
        "plugins.job_search_plugin.store.upsert_liveness"
    ) as mock_upsert:
        res = await fetch_job_posting(url="https://jobs.ashbyhq.com/example/job-abc-123")

    mock_upsert.assert_not_called()
    assert res["status"] == "ok"
    assert res["provider"] == "ashby"
    assert res["job_id"] == "job-abc-123"
    assert res["title"] == "Staff AI Systems Engineer"
    assert res["location"] == "Remote - US/Canada"
    assert res["compensation"] == {"min": 180000, "max": 240000, "currency": "USD"}
    assert "Staff AI Systems Engineer" in res["clean_description"]


@pytest.mark.asyncio
async def test_fetch_job_posting_ashby_job_not_found():
    ashby_response = {"jobs": [{"id": "other-job"}]}

    with mock.patch(
        "plugins.job_search_plugin.MCPTools.job_search_tools._safe_async_client",
        return_value=_make_async_client(json_data=ashby_response),
    ):
        res = await fetch_job_posting(url="https://jobs.ashbyhq.com/example/missing-id")

    assert res["status"] == "error"
    assert res["error"] == "job_not_found"


@pytest.mark.asyncio
async def test_fetch_job_posting_greenhouse_strips_html():
    greenhouse_response = {
        "id": 555,
        "title": "Backend Engineer",
        "location": {"name": "NYC"},
        "content": "<p>Join our <b>team</b>.</p>",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/555",
        "updated_at": "2026-08-01T00:00:00Z",
    }

    with mock.patch(
        "plugins.job_search_plugin.MCPTools.job_search_tools._safe_async_client",
        return_value=_make_async_client(json_data=greenhouse_response),
    ):
        res = await fetch_job_posting(url="https://boards.greenhouse.io/acme/jobs/555")

    assert res["status"] == "ok"
    assert res["provider"] == "greenhouse"
    assert res["job_id"] == "555"
    assert "<p>" not in res["clean_description"]
    assert "Join our" in res["clean_description"]
    assert "team" in res["clean_description"]


@pytest.mark.asyncio
async def test_fetch_job_posting_greenhouse_strips_html_entity_encoded():
    """Live-observed edge case (GitLab's board): content arrives already HTML-entity-
    encoded ("&lt;div&gt;" literally, not "<div>") — a plain "<" in content check
    misses this entirely and the raw entities leak into clean_description unstripped.
    """
    greenhouse_response = {
        "id": 555,
        "title": "Backend Engineer",
        "location": {"name": "NYC"},
        "content": "&lt;div class=&quot;intro&quot;&gt;&lt;p&gt;Join our team.&lt;/p&gt;&lt;/div&gt;",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/555",
        "updated_at": "2026-08-01T00:00:00Z",
    }

    with mock.patch(
        "plugins.job_search_plugin.MCPTools.job_search_tools._safe_async_client",
        return_value=_make_async_client(json_data=greenhouse_response),
    ):
        res = await fetch_job_posting(url="https://boards.greenhouse.io/acme/jobs/555")

    assert res["status"] == "ok"
    assert "&lt;" not in res["clean_description"]
    assert "<div" not in res["clean_description"]
    assert "Join our team." in res["clean_description"]


@pytest.mark.asyncio
async def test_fetch_job_posting_lever_extracts_categories_and_salary():
    lever_response = {
        "id": "lever-999",
        "text": "Product Designer",
        "categories": {"location": "Remote", "team": "Design", "commitment": "Full-time"},
        "descriptionPlain": "Requirements: Figma, prototyping.",
        "salaryRange": {"min": 120000, "max": 160000, "currency": "USD"},
        "hostedUrl": "https://jobs.lever.co/acme/lever-999",
        "createdAt": "2026-07-01T00:00:00Z",
    }

    with mock.patch(
        "plugins.job_search_plugin.MCPTools.job_search_tools._safe_async_client",
        return_value=_make_async_client(json_data=lever_response),
    ):
        res = await fetch_job_posting(url="https://jobs.lever.co/acme/lever-999")

    assert res["status"] == "ok"
    assert res["provider"] == "lever"
    assert res["location"] == "Remote"
    assert res["compensation"] == {"min": 120000, "max": 160000, "currency": "USD"}


@pytest.mark.asyncio
async def test_fetch_job_posting_generic_web_fallback_dispatches_via_execute_operation():
    with mock.patch(
        "core.route_registry.execute.execute_operation", new_callable=mock.AsyncMock
    ) as mock_exec:
        mock_exec.return_value = {
            "status": "ok",
            "url": "https://example.com/careers/123",
            "content": "Some job posting text extracted from the page.",
            "truncated": False,
        }
        res = await fetch_job_posting(url="https://example.com/careers/123")

    assert res["status"] == "ok"
    assert res["provider"] == "generic_web"
    assert res["clean_description"] == "Some job posting text extracted from the page."
    mock_exec.assert_awaited_once_with(
        "search_plugin",
        "fetch_url",
        {"url": "https://example.com/careers/123"},
        caller_scopes=None,
    )


@pytest.mark.asyncio
async def test_fetch_job_posting_generic_web_fallback_propagates_error():
    with mock.patch(
        "core.route_registry.execute.execute_operation", new_callable=mock.AsyncMock
    ) as mock_exec:
        mock_exec.return_value = {"status": "error", "message": "refused: private IP range"}
        res = await fetch_job_posting(url="http://169.254.169.254/latest/meta-data/")

    assert res["status"] == "error"
    assert res["error"] == "api_error"


@pytest.mark.asyncio
async def test_fetch_job_posting_ashby_api_error_returns_error_dict():
    with mock.patch(
        "plugins.job_search_plugin.MCPTools.job_search_tools._safe_async_client",
        return_value=_make_async_client(http_error=Exception("connection refused")),
    ):
        res = await fetch_job_posting(url="https://jobs.ashbyhq.com/example/job-1")

    assert res["status"] == "error"
    assert res["error"] == "api_error"
