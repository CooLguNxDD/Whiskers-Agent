"""
MCP Tools for resume and cover letter tailoring and enrichment.
"""

import os
import logging
from core.context import mcp
from core.llm_provider_management import LLMProvider, get_chat_llm
from langchain_core.messages import SystemMessage, HumanMessage

from plugins.job_search_plugin.store import get_profile
from core.artifact_store import DEFAULT_BUCKET, get_artifact_store
from plugins.job_search_plugin.pdf_render import render_text_pdf
from plugins.job_search_plugin.portfolio_link import public_portfolio_url, sanitize_portfolio_url
from plugins.job_search_plugin.prompts.resume_prompt import (
    RESUME_SYSTEM_PROMPT,
    COVER_LETTER_SYSTEM_PROMPT,
)

logger = logging.getLogger("whiskers.plugins")


async def _auto_bake_portfolio(job_description: str, company: str, role: str) -> str | None:
    """Dispatch portfolio_plugin's bake_portfolio_for_job via execute_operation.

    Never a direct import (CLAUDE.md §1: no plugin->plugin imports) — dispatches
    by identity, modelled on portfolio_plugin/bake/job_signals.py::_call_plugin_op.
    Deliberately omits job_application_job_id/provider: bake_portfolio_for_job
    would otherwise call back into this plugin's get_job_details via those
    fields, creating a dispatch cycle.

    operation_id is fully qualified ("portfolio_plugin__bake_portfolio_for_job")
    — RouteDescriptor.operation_id (static_tool_loader._qualify) is always
    ``{plugin_id}__{tool_name}``, and both OperationCatalog.get and
    execute_operation's RouteRegistry fallback key on the exact qualified id.
    A bare tool name here would 404 on every call; this bug was latent because
    auto_bake_portfolio defaults to False.
    """
    from core.route_registry.execute import execute_operation

    try:
        result = await execute_operation(
            "portfolio_plugin",
            "portfolio_plugin__bake_portfolio_for_job",
            {"job_description": job_description, "company": company, "role": role},
            caller_scopes=None,
        )
    except Exception:
        logger.warning("enrich_tools.py: auto-bake dispatch failed", exc_info=True)
        return None

    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    return result.get("portfolio_job_id") or result.get("short_id")


@mcp.tool(
    title="tailor_resume",
    tags={"job_search_plugin", "enrich", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def tailor_resume(
    applicant_profile_id: int,
    job_description: str,
    company: str = "",
    role: str = "",
    job_id: str = "",
    auto_bake_portfolio: bool = False,
) -> dict:
    """Tailors base resume to JD with anti-fabrication constraints.

    auto_bake_portfolio=True dispatches portfolio_plugin's bake_portfolio_for_job via
    execute_operation (never a direct import) with only job_description/company/role —
    omitting job_id/provider so the bake can't dispatch back into this plugin's
    get_job_details and cycle. Defaults to False: baking persists a row + mints a
    short_id, so it isn't a read-only op and isn't free to run on every tailor call.
    """
    profile = await get_profile(applicant_profile_id)
    if not profile:
        return {"status": "error", "error": "profile_not_found"}

    base_resume_text = profile.get("base_resume_text") or ""

    provider_str = os.environ.get("LLM_PROVIDER", "openai").lower()
    try:
        provider = LLMProvider(provider_str)
    except ValueError:
        provider = LLMProvider.OPENAI

    model = os.environ.get("LLM_MODEL", "")
    llm = get_chat_llm(provider, model)

    user_content = f"Base Resume:\n{base_resume_text}\n\nJob Description:\n{job_description}"

    resp = await llm.ainvoke([
        SystemMessage(content=RESUME_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ])

    result = {
        "status": "ok",
        "kind": "resume",
        "tailored_text": resp.content,
    }

    if auto_bake_portfolio:
        portfolio_job_id = await _auto_bake_portfolio(job_description, company, role)
        if portfolio_job_id:
            result["portfolio_job_id"] = portfolio_job_id
            portfolio_url = public_portfolio_url(portfolio_job_id)
            if portfolio_url:
                result["portfolio_url"] = portfolio_url

    return result


@mcp.tool(
    title="tailor_cover_letter",
    tags={"job_search_plugin", "enrich", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def tailor_cover_letter(
    applicant_profile_id: int,
    job_description: str,
    job_id: str = "",
    portfolio_url: str = "",
) -> dict:
    """Tailor a cover letter based on the applicant's profile and cover letter template using LLM.

    portfolio_url (optional, e.g. from tailor_resume's auto_bake_portfolio result) is passed
    through to the "Interactive Portfolio Reference" closing section; omitted entirely from
    the letter when not supplied — the prompt is instructed to never invent a placeholder link.
    """
    profile = await get_profile(applicant_profile_id)
    if not profile:
        return {"status": "error", "error": "profile_not_found"}

    base_resume_text = profile.get("base_resume_text") or ""
    cover_letter_template = profile.get("cover_letter_template")

    provider_str = os.environ.get("LLM_PROVIDER", "openai").lower()
    try:
        provider = LLMProvider(provider_str)
    except ValueError:
        provider = LLMProvider.OPENAI

    model = os.environ.get("LLM_MODEL", "")
    llm = get_chat_llm(provider, model)

    human_parts = []
    if cover_letter_template:
        human_parts.append(f"Cover Letter Template:\n{cover_letter_template}")
    if base_resume_text:
        human_parts.append(f"Base Resume:\n{base_resume_text}")
    safe_portfolio_url = sanitize_portfolio_url(portfolio_url)
    if safe_portfolio_url:
        human_parts.append(f"Interactive Portfolio URL:\n{safe_portfolio_url}")
    human_parts.append(f"Job Description:\n{job_description}")

    user_content = "\n\n".join(human_parts)

    resp = await llm.ainvoke([
        SystemMessage(content=COVER_LETTER_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ])

    return {
        "status": "ok",
        "kind": "cover_letter",
        "tailored_text": resp.content,
        # Alias of tailored_text under a distinct slot name — the
        # career_ops_apply_v1 FlowSpec runs tailor_resume and
        # tailor_cover_letter as separate stages sharing one blackboard;
        # both writing "tailored_text" would let the later stage clobber
        # the earlier one before render_resume_pdf reads it.
        "cover_letter_text": resp.content,
    }


@mcp.tool(
    title="render_resume_pdf",
    tags={"job_search_plugin", "enrich", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def render_resume_pdf(
    applicant_profile_id: int,
    tailored_text: str,
    kind: str = "resume",
    job_id: str = "",
    portfolio_job_id: str = "",
) -> dict:
    """Render the tailored text as a searchable PDF and upload it to MinIO storage.

    When ``portfolio_job_id`` is set (see portfolio_plugin bake_tools), bakes a contact
    header with the job-specific portfolio URL into the PDF ("bake & send"), rendered as
    a real clickable hyperlink. Returns the object storage key and temporary presigned URL.
    """
    import asyncio

    contact_header = None
    if portfolio_job_id:
        profile = await get_profile(applicant_profile_id)
        if profile:
            portfolio_url = public_portfolio_url(portfolio_job_id)
            contact_header = {
                "name": profile.get("full_name"),
                "email": profile.get("email"),
                "phone": profile.get("phone"),
            }
            if portfolio_url:
                contact_header["portfolio_url"] = portfolio_url

    title = f"Tailored {kind.replace('_', ' ').title()}"
    pdf_bytes = await asyncio.to_thread(render_text_pdf, title, tailored_text, contact_header=contact_header)

    key = f"{applicant_profile_id}/{job_id or 'general'}/{kind}.pdf"

    store = get_artifact_store()
    await store.put_bytes(DEFAULT_BUCKET, key, pdf_bytes, "application/pdf")
    url = await store.presigned_url(DEFAULT_BUCKET, key)

    return {
        "status": "ok",
        "object_key": key,
        "presigned_url": url,
        "kind": kind,
    }


@mcp.tool(
    title="render_cover_letter_pdf",
    tags={"job_search_plugin", "enrich", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def render_cover_letter_pdf(
    applicant_profile_id: int,
    cover_letter_text: str,
    job_id: str = "",
    portfolio_job_id: str = "",
) -> dict:
    """Render the tailored cover letter as a searchable PDF and upload it to MinIO storage.

    Bakes the exact same contact header (name, email, phone, and job-specific
    portfolio URL) and theme/typography styling as the resume PDF. Returns the object
    storage key and temporary presigned URL.
    """
    res = await render_resume_pdf(
        applicant_profile_id=applicant_profile_id,
        tailored_text=cover_letter_text,
        kind="cover_letter",
        job_id=job_id,
        portfolio_job_id=portfolio_job_id,
    )
    return {
        "status": res.get("status", "ok"),
        "cover_letter_object_key": res.get("object_key"),
        "cover_letter_presigned_url": res.get("presigned_url"),
        "object_key": res.get("object_key"),
        "presigned_url": res.get("presigned_url"),
        "kind": "cover_letter",
    }

