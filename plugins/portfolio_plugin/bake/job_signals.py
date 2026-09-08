"""Job-signal resolution for the bake pipeline.

Widens the stored job description with whatever external signal is reachable:
board detail, the posting URL body, and a soft company-stack web search. Every
hop is independently fail-open and records its cause on the ``BakeContext``,
so "we composed against the stored blurb only" stays visible instead of
log-only.

Siblings are reached by operation identity through the catalog — this module
imports no other plugin.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.bake.run_context import BakeContext, BakeErrorCode, Timer

logger = logging.getLogger("whiskers.plugins.portfolio.bake_tools")


async def _call_plugin_op(plugin_id: str, operation_id: str, args: dict[str, Any]) -> Any:
    """Dispatch another plugin's registered operation through the catalog.

    Modelled on ``discovery/sources.py::invoke_proxy`` — resolves via
    ``OperationCatalog``, authorizes through a real ``AccessRequest`` and
    JSON-Schema-validates args, so the bake never imports a sibling plugin.
    ``caller_scopes=None`` marks a trusted local caller, matching the existing
    discovery dispatch.
    """
    from core.route_registry.execute import execute_operation

    return await execute_operation(
        plugin_id,
        operation_id,
        {**args, "_response_shape": {"response_format": "json"}},
        caller_scopes=None,
    )


async def resolve_job_posting_text(
    job_description: str,
    *,
    job_application_job_id: str = "",
    provider: str = "",
    posting_url: str = "",
    company: str = "",
    ctx: BakeContext | None = None,
) -> str:
    """Fail-open live posting resolution for richer audience inference.

    Priority: board detail API (when id+provider) → fetch_url(posting_url) →
    stored description. Optional web_search for company stack is folded into
    the text as soft signal; never raises.

    Each hop is independently fail-open. When ``ctx`` is supplied every swallowed
    failure is also recorded there, so "the JD we composed against was only the
    stored description" becomes visible instead of log-only.
    """
    text = (job_description or "").strip()
    timer = Timer()

    # 1) Board detail when both id and provider are present (optional job_search)
    if job_application_job_id and provider:
        try:
            detail = await _call_plugin_op(
                "job_search_plugin",
                "get_job_details",
                {"job_id": job_application_job_id, "provider": provider},
            )
            if isinstance(detail, dict) and detail.get("status") != "error":
                for key in ("description", "job_description", "full_description", "content"):
                    val = detail.get(key)
                    if isinstance(val, str) and val.strip() and len(val.strip()) > len(text):
                        text = val.strip()
                        break
                data = detail.get("data")
                if isinstance(data, dict):
                    for key in ("description", "job_description", "full_description"):
                        val = data.get(key)
                        if isinstance(val, str) and val.strip() and len(val.strip()) > len(text):
                            text = val.strip()
                            break
        except Exception as exc:
            # Fail-open: keep stored description; warn so misconfig is visible.
            logger.warning("bake: get_job_details fail-open: %s", exc)
            if ctx is not None:
                ctx.add_exc("resolve", BakeErrorCode.RESOLVE_DETAIL_FAILED, exc)

    # 2) Posting URL fetch (search_plugin)
    if posting_url and posting_url.startswith(("http://", "https://")):
        try:
            fetched = await _call_plugin_op(
                "search_plugin",
                "fetch_url",
                {"url": posting_url, "max_chars": 12000},
            )
            if isinstance(fetched, dict) and fetched.get("status") == "ok":
                body = fetched.get("content")
                if isinstance(body, str) and body.strip() and len(body.strip()) > len(text):
                    text = body.strip()
        except Exception as exc:
            logger.warning("bake: fetch_url fail-open: %s", exc)
            if ctx is not None:
                ctx.add_exc("resolve", BakeErrorCode.RESOLVE_FETCH_FAILED, exc)

    # 3) Optional company stack web_search (soft signal only)
    if company and company.strip():
        try:
            ws = await _call_plugin_op(
                "search_plugin",
                "web_search",
                {"query": f"{company.strip()} engineering stack", "max_results": 3},
            )
            if isinstance(ws, dict) and ws.get("status") == "ok":
                snippets: list[str] = []
                for item in (ws.get("results") or [])[:3]:
                    if not isinstance(item, dict):
                        continue
                    snip = item.get("snippet") or item.get("content") or item.get("title")
                    if isinstance(snip, str) and snip.strip():
                        snippets.append(snip.strip()[:300])
                if snippets:
                    text = f"{text}\n\nCompany stack signals:\n" + "\n".join(snippets)
        except Exception as exc:
            logger.warning("bake: web_search fail-open: %s", exc)
            if ctx is not None:
                ctx.add_exc("resolve", BakeErrorCode.RESOLVE_WEBSEARCH_FAILED, exc)

    if ctx is not None:
        ctx.mark("resolve", timer.ms)
    return text or (job_description or "")
