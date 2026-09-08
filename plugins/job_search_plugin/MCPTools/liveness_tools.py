"""MCP Tools for job posting liveness verification and ghost-job detection."""

import logging
from core.context import mcp
from core.proxy.ssrf_safety import _safe_async_client
from plugins.job_search_plugin import store

logger = logging.getLogger("whiskers.plugins")

# Deterministic ghost-job heuristics (Career-Ops Block G: Posting Legitimacy).
GHOST_REPOST_THRESHOLD = 2
GHOST_STALENESS_DAYS = 60
SCAM_KEYWORDS = (
    "telegram", "whatsapp", "wire transfer", "western union",
    "processing fee", "crypto payment", "gift card",
)


def _detect_scam_signals(*texts: str) -> list[str]:
    """Scan role_title/company text for common scam-wording signals.

    Limited to the fields check_job_liveness actually receives (url/company/
    role_title) — no full JD text is passed to this tool.
    """
    haystack = " ".join(t.lower() for t in texts if t)
    return [f"Mentions '{kw}'" for kw in SCAM_KEYWORDS if kw in haystack]


def _evaluate_ghost_signals(
    repost_count: int, staleness_days: int, company: str, role_title: str
) -> tuple[list[str], str]:
    """Deterministic ghost-job scoring rubric. Returns (reasons, legitimacy_tier)."""
    reasons: list[str] = []
    if repost_count >= GHOST_REPOST_THRESHOLD:
        reasons.append(f"Role has been reposted {repost_count} times")
    if staleness_days > GHOST_STALENESS_DAYS:
        reasons.append(f"Initial listing is {staleness_days} days old")

    scam_reasons = _detect_scam_signals(company, role_title)
    if scam_reasons:
        return reasons + scam_reasons, "scam_risk"
    if reasons:
        return reasons, "caution"
    return reasons, "verified"


async def _check_url_live(url: str) -> bool:
    """Lightweight SSRF-safe reachability check. False on any error/4xx/5xx."""
    try:
        async with _safe_async_client(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "WhiskersAgent/1.0"})
            return resp.status_code < 400
    except Exception:
        logger.debug("liveness_tools.py: liveness HTTP check failed", exc_info=True)
        return False


async def evaluate_and_record_liveness(
    url: str, company: str = "", role_title: str = "", provider: str = "",
) -> dict:
    """Shared liveness-check body used by both check_job_liveness and
    fetch_job_posting's inline auto-check. Always writes/refreshes a
    job_posting_liveness row.
    """
    is_live = await _check_url_live(url)
    record, _created = await store.upsert_liveness(
        url, company=company, role_title=role_title, provider=provider, is_live=is_live,
    )
    reasons, tier = _evaluate_ghost_signals(
        record["repost_count"], record["staleness_days"], record["company"], record["role_title"]
    )
    return {
        "status": "ok",
        "url": url,
        "is_live": is_live,
        "repost_count": record["repost_count"],
        "first_seen_at": record["first_seen_at"],
        "staleness_days": record["staleness_days"],
        "is_potential_ghost_job": bool(reasons),
        "ghost_reasons": reasons,
        "legitimacy_tier": tier,
    }


@mcp.tool(
    title="check_job_liveness",
    tags={"job_search_plugin", "liveness", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def check_job_liveness(
    url: str,
    company: str = "",
    role_title: str = "",
) -> dict:
    """Checks live HTTP/ATS status, calculates repost frequency, and flags ghost-job signals.

    Writes/updates a job_posting_liveness row — not read-only despite the GET-shaped call.
    """
    if not url:
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["url"]}
    return await evaluate_and_record_liveness(url, company=company, role_title=role_title)
