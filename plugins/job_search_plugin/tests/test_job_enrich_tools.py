"""
Unit tests for job enrich tools plugin.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# Import the functions to test
from plugins.job_search_plugin.pdf_render import render_text_pdf
from plugins.job_search_plugin.MCPTools.enrich_tools import (
    tailor_resume,
    tailor_cover_letter,
    render_resume_pdf,
)


def _mock_artifact_store(*, presigned_url: str = "http://minio/presigned_url") -> MagicMock:
    """Build a stub IArtifactStore for patching enrich_tools.get_artifact_store."""
    store = MagicMock()
    store.put_bytes = AsyncMock(return_value="mock_key")
    store.presigned_url = AsyncMock(return_value=presigned_url)
    return store


@pytest.mark.asyncio
async def test_render_text_pdf():
    """Verify render_text_pdf returns non-empty bytes beginning with b"%PDF"."""
    pdf_bytes = render_text_pdf("Software Developer", "Line 1 of resume.\n\nLine 2 of resume.")
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_render_text_pdf_with_contact_header():
    """Verify render_text_pdf still produces a valid PDF when a contact_header is given."""
    pdf_bytes = render_text_pdf(
        "Software Developer",
        "Line 1 of resume.\n\nLine 2 of resume.",
        contact_header={
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "555-1234",
            "portfolio_url": "https://portfolio.cat.io/?j=whiskers_successor_992",
        },
    )
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_tailor_resume_success():
    """Verify tailor_resume returns LLM tailored text and calls LLM with correct inputs."""
    mock_profile = {
        "id": 42,
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "base_resume_text": "Experienced Python Engineer.",
        "cover_letter_template": "Dear Hiring Manager,",
    }

    with patch("plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock(return_value=mock_profile)) as mock_get_profile:
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Tailored Resume Content by LLM"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("plugins.job_search_plugin.MCPTools.enrich_tools.get_chat_llm", return_value=mock_llm) as mock_get_llm:
            res = await tailor_resume(
                applicant_profile_id=42,
                job_description="Looking for Python Developer with Kubernetes knowledge.",
            )

            assert res == {
                "status": "ok",
                "kind": "resume",
                "tailored_text": "Tailored Resume Content by LLM",
            }

            mock_get_profile.assert_called_once_with(42)
            mock_get_llm.assert_called_once()

            mock_llm.ainvoke.assert_called_once()
            call_args = mock_llm.ainvoke.call_args[0][0]

            assert len(call_args) == 2
            system_msg = call_args[0]
            human_msg = call_args[1]

            assert system_msg.content is not None
            assert "Looking for Python Developer with Kubernetes knowledge." in human_msg.content
            assert "Experienced Python Engineer." in human_msg.content


@pytest.mark.asyncio
async def test_tailor_resume_missing_profile():
    """Verify tailor_resume returns error when applicant profile is not found."""
    with patch("plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock(return_value=None)) as mock_get_profile:
        res = await tailor_resume(applicant_profile_id=999, job_description="Python Job")
        assert res == {
            "status": "error",
            "error": "profile_not_found",
        }
        mock_get_profile.assert_called_once_with(999)


@pytest.mark.asyncio
async def test_tailor_cover_letter_success():
    """Verify tailor_cover_letter uses cover_letter_template if present and returns LLM text."""
    mock_profile = {
        "id": 42,
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "base_resume_text": "Experienced Python Engineer.",
        "cover_letter_template": "Dear Hiring Manager, template here",
    }

    with patch("plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock(return_value=mock_profile)) as mock_get_profile:
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Tailored Cover Letter Content by LLM"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("plugins.job_search_plugin.MCPTools.enrich_tools.get_chat_llm", return_value=mock_llm) as mock_get_llm:
            res = await tailor_cover_letter(
                applicant_profile_id=42,
                job_description="Looking for Python Developer.",
            )

            assert res == {
                "status": "ok",
                "kind": "cover_letter",
                "tailored_text": "Tailored Cover Letter Content by LLM",
                "cover_letter_text": "Tailored Cover Letter Content by LLM",
            }

            mock_get_profile.assert_called_once_with(42)
            mock_get_llm.assert_called_once()

            mock_llm.ainvoke.assert_called_once()
            call_args = mock_llm.ainvoke.call_args[0][0]
            assert len(call_args) == 2

            assert "Looking for Python Developer." in call_args[1].content
            assert "Dear Hiring Manager, template here" in call_args[1].content


@pytest.mark.asyncio
async def test_tailor_cover_letter_omits_localhost_portfolio_url():
    """A dead localhost bake link must never reach the cover-letter prompt."""
    mock_profile = {
        "id": 42,
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "base_resume_text": "resume",
        "cover_letter_template": None,
    }
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Dear Hiring Manager, ..."
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    with patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_profile",
        AsyncMock(return_value=mock_profile),
    ), patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_chat_llm", return_value=mock_llm
    ):
        await tailor_cover_letter(
            applicant_profile_id=42,
            job_description="JD text",
            portfolio_url="http://localhost:11000/?j=abc123",
        )

    human = mock_llm.ainvoke.call_args[0][0][1].content
    assert "localhost" not in human
    assert "Interactive Portfolio URL" not in human


@pytest.mark.asyncio
async def test_tailor_cover_letter_missing_profile():
    """Verify tailor_cover_letter returns error when profile is missing."""
    with patch("plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock(return_value=None)) as mock_get_profile:
        res = await tailor_cover_letter(applicant_profile_id=999, job_description="Python Job")
        assert res == {
            "status": "error",
            "error": "profile_not_found",
        }
        mock_get_profile.assert_called_once_with(999)


@pytest.mark.asyncio
async def test_render_resume_pdf_success():
    """Verify render_resume_pdf calls store.put_bytes with structured key and returns presigned URL."""
    store = _mock_artifact_store()
    with patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_artifact_store", return_value=store
    ):
        res = await render_resume_pdf(
            applicant_profile_id=42,
            tailored_text="Tailored Resume Content",
            kind="resume",
            job_id="job_abc_123",
        )

        expected_key = "42/job_abc_123/resume.pdf"
        assert res == {
            "status": "ok",
            "object_key": expected_key,
            "presigned_url": "http://minio/presigned_url",
            "kind": "resume",
        }

        store.put_bytes.assert_called_once()
        args, kwargs = store.put_bytes.call_args
        from core.artifact_store import DEFAULT_BUCKET
        assert args[0] == DEFAULT_BUCKET
        assert args[1] == expected_key
        assert args[2].startswith(b"%PDF")
        assert len(args) < 4 or args[3] == "application/pdf"

        store.presigned_url.assert_called_once_with(DEFAULT_BUCKET, expected_key)


@pytest.mark.asyncio
async def test_render_resume_pdf_no_job_id():
    """Verify render_resume_pdf uses 'general' key folder when job_id is empty."""
    store = _mock_artifact_store()
    with patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_artifact_store", return_value=store
    ):
        res = await render_resume_pdf(
            applicant_profile_id=42,
            tailored_text="Tailored Resume Content",
            kind="resume",
            job_id="",
        )

        expected_key = "42/general/resume.pdf"
        assert res == {
            "status": "ok",
            "object_key": expected_key,
            "presigned_url": "http://minio/presigned_url",
            "kind": "resume",
        }

        store.put_bytes.assert_called_once()
        args, kwargs = store.put_bytes.call_args
        assert args[1] == expected_key


@pytest.mark.asyncio
async def test_render_resume_pdf_without_portfolio_job_id_no_contact_header():
    """Regression guard: omitting portfolio_job_id skips the profile lookup and header entirely."""
    with patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_artifact_store",
        return_value=_mock_artifact_store(),
    ), \
         patch("plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock()) as mock_get_profile, \
         patch("plugins.job_search_plugin.MCPTools.enrich_tools.render_text_pdf") as mock_render_pdf:
        mock_render_pdf.return_value = b"%PDF-fake"

        await render_resume_pdf(
            applicant_profile_id=42,
            tailored_text="Tailored Resume Content",
            kind="resume",
            job_id="job_abc_123",
        )

        mock_get_profile.assert_not_called()
        _, kwargs = mock_render_pdf.call_args
        assert kwargs["contact_header"] is None


@pytest.mark.asyncio
async def test_render_resume_pdf_with_portfolio_job_id_bakes_contact_header():
    """portfolio_job_id set -> profile fetched and baked contact header passed to render_text_pdf."""
    mock_profile = {
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "555-1234",
    }
    with patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_artifact_store",
        return_value=_mock_artifact_store(),
    ), \
         patch("plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock(return_value=mock_profile)) as mock_get_profile, \
         patch("plugins.job_search_plugin.MCPTools.enrich_tools.render_text_pdf") as mock_render_pdf, \
         patch.dict("os.environ", {"CATPORTFOLIO_PUBLIC_DOMAIN": "portfolio.cat.io"}):
        mock_render_pdf.return_value = b"%PDF-fake"

        await render_resume_pdf(
            applicant_profile_id=42,
            tailored_text="Tailored Resume Content",
            kind="resume",
            job_id="job_abc_123",
            portfolio_job_id="whiskers_successor_992",
        )

        mock_get_profile.assert_awaited_once_with(42)
        _, kwargs = mock_render_pdf.call_args
        assert kwargs["contact_header"] == {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "555-1234",
            "portfolio_url": "https://portfolio.cat.io/?j=whiskers_successor_992",
        }


@pytest.mark.asyncio
async def test_render_resume_pdf_localhost_domain_omits_portfolio_link():
    """A bake short_id on localhost:11000 must not be written into the PDF header."""
    mock_profile = {
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "555-1234",
    }
    with patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_artifact_store",
        return_value=_mock_artifact_store(),
    ), \
         patch("plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock(return_value=mock_profile)), \
         patch("plugins.job_search_plugin.MCPTools.enrich_tools.render_text_pdf") as mock_render_pdf, \
         patch.dict("os.environ", {"CATPORTFOLIO_PUBLIC_DOMAIN": "http://localhost:11000"}):
        mock_render_pdf.return_value = b"%PDF-fake"

        await render_resume_pdf(
            applicant_profile_id=42,
            tailored_text="Tailored Resume Content",
            kind="resume",
            job_id="job_abc_123",
            portfolio_job_id="abc123",
        )

        _, kwargs = mock_render_pdf.call_args
        header = kwargs["contact_header"]
        assert "portfolio_url" not in header
        assert "localhost" not in str(header)
