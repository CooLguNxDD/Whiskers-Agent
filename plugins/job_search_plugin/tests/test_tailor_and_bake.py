"""Unit tests for Stage 5: anti-fabrication tailoring and portfolio auto-bake."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from plugins.job_search_plugin.MCPTools.enrich_tools import tailor_resume
from plugins.job_search_plugin.pdf_render import render_text_pdf


_PROFILE = {
    "id": 42,
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "base_resume_text": "Experienced Python Engineer.",
}


def _mock_llm(text="Tailored Resume Content by LLM"):
    llm = MagicMock()
    resp = MagicMock()
    resp.content = text
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


@pytest.mark.asyncio
async def test_tailor_resume_auto_bake_dispatches_via_execute_operation():
    """auto_bake_portfolio=True calls execute_operation("portfolio_plugin",
    "bake_portfolio_for_job", ...), never a direct plugin import.
    """
    with patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock(return_value=_PROFILE)
    ), patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_chat_llm", return_value=_mock_llm()
    ), patch(
        "core.route_registry.execute.execute_operation", new_callable=AsyncMock
    ) as mock_exec:
        mock_exec.return_value = {"status": "ok", "portfolio_job_id": "whiskers_successor_992"}

        with patch.dict("os.environ", {"CATPORTFOLIO_PUBLIC_DOMAIN": "portfolio.cat.io"}):
            res = await tailor_resume(
                applicant_profile_id=42,
                job_description="Looking for a Python Developer.",
                company="Acme",
                role="Staff Engineer",
                job_id="job_abc_123",
                auto_bake_portfolio=True,
            )

    assert res["status"] == "ok"
    assert res["portfolio_job_id"] == "whiskers_successor_992"
    assert res["portfolio_url"].endswith("?j=whiskers_successor_992")

    mock_exec.assert_awaited_once_with(
        "portfolio_plugin",
        "portfolio_plugin__bake_portfolio_for_job",
        {
            "job_description": "Looking for a Python Developer.",
            "company": "Acme",
            "role": "Staff Engineer",
        },
        caller_scopes=None,
    )


@pytest.mark.asyncio
async def test_tailor_resume_auto_bake_never_passes_job_id_or_provider():
    """Cycle guard: even though tailor_resume itself received job_id, the bake
    dispatch args must never include job_application_job_id/provider — those
    would round-trip bake_portfolio_for_job back into this plugin's get_job_details.
    """
    with patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock(return_value=_PROFILE)
    ), patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_chat_llm", return_value=_mock_llm()
    ), patch(
        "core.route_registry.execute.execute_operation", new_callable=AsyncMock
    ) as mock_exec:
        mock_exec.return_value = {"status": "ok", "portfolio_job_id": "abc"}

        await tailor_resume(
            applicant_profile_id=42,
            job_description="JD text",
            job_id="job_abc_123",
            auto_bake_portfolio=True,
        )

    call_args = mock_exec.await_args.args
    dispatched_kwargs = call_args[2]
    assert "job_application_job_id" not in dispatched_kwargs
    assert "provider" not in dispatched_kwargs
    assert "job_id" not in dispatched_kwargs


@pytest.mark.asyncio
async def test_tailor_resume_no_auto_bake_by_default():
    """auto_bake_portfolio defaults to False — no execute_operation call at all."""
    with patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock(return_value=_PROFILE)
    ), patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_chat_llm", return_value=_mock_llm()
    ), patch(
        "core.route_registry.execute.execute_operation", new_callable=AsyncMock
    ) as mock_exec:
        res = await tailor_resume(applicant_profile_id=42, job_description="JD text")

    assert "portfolio_job_id" not in res
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_tailor_resume_auto_bake_failure_is_fail_open():
    """A failed bake dispatch must not break tailor_resume's own result."""
    with patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_profile", AsyncMock(return_value=_PROFILE)
    ), patch(
        "plugins.job_search_plugin.MCPTools.enrich_tools.get_chat_llm", return_value=_mock_llm()
    ), patch(
        "core.route_registry.execute.execute_operation", new_callable=AsyncMock
    ) as mock_exec:
        mock_exec.side_effect = Exception("catalog dispatch failed")

        res = await tailor_resume(
            applicant_profile_id=42, job_description="JD text", auto_bake_portfolio=True
        )

    assert res["status"] == "ok"
    assert "portfolio_job_id" not in res


# ─── render_text_pdf clickable portfolio link ──────────────────────────────

def test_render_text_pdf_contact_header_has_real_anchor_not_escaped_literal():
    """The portfolio_url must reach the PDF as an <a href> ReportLab markup tag,
    not a double-escaped literal '&lt;a href=...&gt;'.
    """
    from reportlab.platypus import Paragraph

    captured = {}
    original_init = Paragraph.__init__

    def _capture(self, text, *args, **kwargs):
        captured.setdefault("texts", []).append(text)
        return original_init(self, text, *args, **kwargs)

    with patch.object(Paragraph, "__init__", _capture):
        render_text_pdf(
            "Software Developer",
            "Body text.",
            contact_header={
                "name": "Jane Doe",
                "portfolio_url": "https://portfolio.cat.io/?j=whiskers_successor_992",
            },
        )

    contact_para_text = captured["texts"][0]
    assert '<a href="https://portfolio.cat.io/?j=whiskers_successor_992">' in contact_para_text
    assert "&lt;a href" not in contact_para_text
    assert "&amp;lt;" not in contact_para_text
