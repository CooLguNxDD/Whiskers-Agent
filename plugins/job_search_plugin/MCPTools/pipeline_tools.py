"""
Adapter MCP Tools for the career_ops_apply_v1 FlowSpec.

FlowSpec deterministic stages dispatch through execute_operation and build
kwargs as ``{k: board.get(k) for k in stage.reads}`` — a blackboard slot name
is passed to the next op verbatim as that parameter's name, with no rename
facility. These two ops exist purely to bridge slot-name mismatches between
existing tools (fetch_job_posting's ``title``/``clean_description`` vs.
evaluate_offer_fit's ``offer_text``, bake_portfolio_for_job's ``short_id`` vs.
sync_application_status's ``portfolio_job_id``, etc.) without renaming any
already-shipped tool signature — those are a held external contract for
Career-Ops. Modelled on portfolio_plugin's own bridge op for the same reason,
``resolve_bake_job_signals`` (plugins/portfolio_plugin/MCPTools/bake_tools.py).
"""

import hashlib
import logging

from core.context import mcp
from plugins.job_search_plugin.portfolio_link import public_portfolio_url
from plugins.job_search_plugin.posting_ingest import extract_board_identity

logger = logging.getLogger("whiskers.plugins")


@mcp.tool(
    title="resolve_pipeline_signals",
    tags={"job_search_plugin", "pipeline", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def resolve_pipeline_signals(
    goal: str = "",
    title: str = "",
    company: str = "",
    clean_description: str = "",
    source_url: str = "",
    job_id: str = "",
    via: str = "",
    posting_entity: str = "",
    end_employer: str | None = None,
    employment_class: str = "",
    location: str = "",
    needs_browser_scrape: bool = False,
    ingest_status: str = "",
) -> dict:
    """Bridge fetch_job_posting's output slots into the fields downstream ops need.

    Maps title -> role, clean_description -> job_description/offer_text,
    source_url -> posting_url, and re-emits posting_url/role as url/role_title
    for check_job_liveness. Agency pastes keep ``via`` / ``end_employer``;
    ``company`` is the end employer when named, otherwise the via/agency so
    later stages have a slot without inventing a client. When ingest was a
    gated board fetch with no paste, returns ``needs_browser_scrape`` rather
    than a generic missing-signals error.
    """
    role = title
    job_description = clean_description
    offer_text = clean_description
    posting_url = source_url
    # Prefer a named client; fall back to the posting entity / agency so the
    # pipeline has a company slot. Structured via/end_employer keep the truth.
    company = (end_employer or company or "").strip()
    if not company:
        company = (via or posting_entity or "").strip()

    if needs_browser_scrape and not (job_description or "").strip():
        return {
            "status": "error",
            "error": "needs_browser_scrape",
            "needs_browser_scrape": True,
            "ingest_status": ingest_status or "needs_browser_scrape",
            "job_id": job_id,
            "posting_url": posting_url,
            "url": posting_url,
            "via": via,
            "posting_entity": posting_entity,
            "end_employer": end_employer or None,
            "employment_class": employment_class or "unknown",
            "location": location,
            "message": (
                "Posting URL is gated (Cloudflare/login/captcha). "
                "Paste the JD as raw_text — this is not a ghost-job signal."
            ),
        }

    if not company or not role:
        try:
            from core.route_registry.execute import execute_operation

            # resolve_bake_job_signals's own regex parse ("ROLE at COMPANY") runs
            # against "goal or job_description" — it prefers goal when both are
            # given. Our goal is a generic instruction ("apply to this job"),
            # never the "ROLE at COMPANY" phrasing; that pattern lives in the JD
            # text (job_description), so it must win here, with goal only as the
            # last-resort fallback when there's no JD text at all to parse.
            sig = await execute_operation(
                "portfolio_plugin",
                "portfolio_plugin__resolve_bake_job_signals",
                {
                    "goal": job_description or goal,
                    "job_description": job_description,
                    "company": company,
                    "role": role,
                },
                caller_scopes=None,
            )
            if isinstance(sig, dict) and sig.get("status") == "ok":
                company = company or sig.get("company") or ""
                role = role or sig.get("role") or ""
                job_description = job_description or sig.get("job_description") or ""
                offer_text = offer_text or job_description
        except Exception:
            logger.warning("pipeline_tools.py: signal resolution fallback failed", exc_info=True)

    if not job_id:
        basis = source_url or f"{company}|{role}"
        if basis == "|":
            basis = ""
        if basis:
            job_id = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    if not company or not role or not job_description:
        return {
            "status": "error",
            "error": "missing_job_signals",
            "missing_fields": [
                f for f, v in (("company", company), ("role", role), ("job_description", job_description)) if not v
            ],
        }

    return {
        "status": "ok",
        "company": company,
        "role": role,
        "job_description": job_description,
        "offer_text": offer_text,
        "posting_url": posting_url,
        "job_id": job_id,
        "url": posting_url,
        "role_title": role,
        "via": via,
        "posting_entity": posting_entity,
        "end_employer": end_employer or None,
        "employment_class": employment_class or "unknown",
        "location": location,
        "needs_browser_scrape": bool(needs_browser_scrape),
        "ingest_status": ingest_status or "ok",
    }


@mcp.tool(
    title="prepare_application_record",
    tags={"job_search_plugin", "pipeline", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def prepare_application_record(
    portfolio_job_id: str = "",
    job_id: str = "",
    company: str = "",
    role: str = "",
    verdict: dict | None = None,
    via: str = "",
    end_employer: str | None = None,
    employment_class: str = "",
) -> dict:
    """Bridge bake/fit output into sync_application_status's input slots.

    Builds a recruiter-safe portfolio_url (omitted when bake produced no
    short_id or CATPORTFOLIO_PUBLIC_DOMAIN is unset / localhost). Always
    emits application_status="drafted" — this pipeline prepares an
    application, it never submits one. An empty portfolio_job_id is not an
    error: a failed bake must not fail_closed this stage or the tailored
    resume is lost.
    """
    fit_score = None
    if isinstance(verdict, dict):
        fit_score = verdict.get("fit_score")

    notes_parts = []
    if fit_score is not None:
        notes_parts.append(f"fit_score={fit_score}")
    if isinstance(verdict, dict) and verdict.get("mismatch_reasons"):
        notes_parts.append("mismatches=" + "; ".join(str(m) for m in verdict["mismatch_reasons"][:5]))
    if via and not (end_employer or "").strip():
        notes_parts.append(f"via={via} (agency; unnamed client)")
    elif via:
        notes_parts.append(f"via={via}")
    if employment_class:
        notes_parts.append(f"employment_class={employment_class}")
    notes = " | ".join(notes_parts)

    # Never require a bake id — empty/None is a successful no-link record.
    portfolio_url = public_portfolio_url(portfolio_job_id or "")

    return {
        "status": "ok",
        "portfolio_url": portfolio_url,
        "job_id": job_id,
        "application_status": "drafted",
        "notes": notes,
    }


@mcp.tool(
    title="run_career_ops_pipeline",
    tags={"job_search_plugin", "pipeline", "enrich", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def run_career_ops_pipeline(
    goal: str,
    applicant_profile_id: int,
    url: str = "",
    raw_text: str = "",
    theme: str = "",
) -> dict:
    """Directly dispatch the career_ops_apply_v1 FlowSpec as a callable MCP tool.

    The flow is registered as a synthetic catalog op ("specialist/career_ops_apply_v1")
    reachable via execute_operation or GOAP-planner dispatch, and via natural-language
    triage routing into specialist_entry — but neither of those is a guaranteed,
    directly-callable surface for an external MCP client (there is no generic
    "execute this catalog op" tool exposed over the gateway, and LLM triage
    classification is probabilistic, not a reliable dispatch mechanism). This tool
    is that direct surface: a Career-Ops-driving CLI agent calls it exactly like any
    other MCP tool and gets the same 9-stage envelope run_flow produces.

    Caller scopes are read from the current MCP access token (fastmcp's
    get_access_token, via core_graph.mcp_tool._caller_scopes) — the same scopes
    that would gate this call through execute_operation's REST path — so a
    Career-Ops key missing a required stage's scope fails or continues at that
    stage exactly as documented in the career-ops-pipeline skill, not with a
    blanket bypass.
    """
    from core.context import current_tenant_id
    from core_graph.mcp_tool import _caller_scopes
    from core_graph.subgraphs.specialist.flow_registry import get_flow
    from core_graph.subgraphs.specialist.flow_runner import run_flow

    if url:
        import urllib.parse
        scheme = urllib.parse.urlsplit(url).scheme
        if scheme not in ("http", "https"):
            return {"status": "error", "error": "invalid_url", "message": "URL must use http or https"}

    # Gated Indeed/LinkedIn (Cloudflare 401) must not enter fail_closed ingest.
    # ATS URLs skip this preflight so we don't double-fetch a working board API.
    if url and not (raw_text or "").strip():
        from plugins.job_search_plugin.MCPTools.job_search_tools import (
            _detect_ats_provider,
            fetch_job_posting,
        )

        if _detect_ats_provider(url) is None:
            ingested = await fetch_job_posting(url=url, raw_text="")
            blocked = bool(ingested.get("needs_browser_scrape")) or ingested.get("error") == "needs_browser_scrape"
            has_text = bool((ingested.get("clean_description") or "").strip())
            if blocked and not has_text:
                identity = extract_board_identity(url)
                return {
                    "status": "ok",
                    "flow_id": "career_ops_apply_v1",
                    "ingest_status": "needs_browser_scrape",
                    "needs_browser_scrape": True,
                    "is_potential_ghost_job": False,
                    "provider": ingested.get("provider") or identity.get("provider") or "",
                    "job_id": ingested.get("job_id") or identity.get("job_id") or "",
                    "source_url": url,
                    "application_status": "drafted",
                    "portfolio_url": "",
                    "summary": (
                        "Posting URL is gated (Cloudflare/login/captcha). "
                        "Paste the JD as raw_text to continue. "
                        "This is not a ghost-job signal."
                    ),
                }

    flow = get_flow("career_ops_apply_v1")
    if flow is None:
        return {"status": "error", "error": "flow_not_registered"}

    raw_tid = current_tenant_id.get()
    if raw_tid is None:
        return {"status": "error", "error": "unauthorized", "message": "Missing tenant context"}
    tenant_id = int(raw_tid)
    if tenant_id <= 0:
        return {"status": "error", "error": "unauthorized", "message": "Invalid tenant ID"}

    envelope = await run_flow(
        flow,
        goal,
        tenant_id=tenant_id,
        caller_scopes=_caller_scopes(),
        inputs={
            "applicant_profile_id": applicant_profile_id,
            "url": url,
            "raw_text": raw_text,
            "theme": theme,
        },
    )
    return _curate_pipeline_result(envelope)


# run_flow's raw envelope carries every stage's full writes on "carry"
# (dict(board.slots)) plus, when a "layout" slot exists, a duplicated copy of
# the whole portfolio layout under both "layout" and "carry.layout"
# (core_graph/subgraphs/specialist/flow_runner.py::_envelope — shared by every
# flow, including portfolio_bake_v1/portfolio_ask_v1, which legitimately want
# that duplication for immediate re-render). A live run of this flow produced
# a ~1.3MB raw envelope that crashed FastMCP's response serializer with a
# RecursionError before any result reached the caller — the flow had already
# succeeded and persisted everything server-side, but the MCP call still
# failed. This tool's response is a public API boundary for an external
# client (Career-Ops), so it returns a bounded, curated result instead of the
# raw envelope, regardless of how large any individual stage's output gets.
_CARRY_FIELDS = (
    "company", "role", "job_description", "job_id", "verdict", "hybrid_used",
    "short_id", "portfolio_job_id", "degraded", "portfolio_url",
    "application_status", "notes", "tailored_text", "kind", "object_key",
    "presigned_url", "cover_letter_text", "application", "created",
    "via", "posting_entity", "end_employer", "employment_class", "location",
    "needs_browser_scrape", "ingest_status", "title", "provider", "source_url",
)


def _curate_pipeline_result(envelope: dict) -> dict:
    """Trim run_flow's raw envelope to a bounded, caller-facing result."""
    if not isinstance(envelope, dict):
        return {"status": "error", "error": "invalid_flow_envelope"}

    carry_raw = envelope.get("carry")
    carry = carry_raw if isinstance(carry_raw, dict) else {}
    result = {
        "status": envelope.get("status", "error"),
        "flow_id": envelope.get("flow_id"),
        "summary": envelope.get("summary"),
        "phases": envelope.get("phases"),
    }
    for key in ("error", "stage", "missing"):
        if key in envelope:
            result[key] = envelope[key]
    for key in _CARRY_FIELDS:
        if key in carry:
            result[key] = carry[key]
    return result
