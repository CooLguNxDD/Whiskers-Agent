"""Job search and details tools for the job_search_plugin.

Provides search_jobs, get_job_details, and fetch_job_posting tools.
"""

import html.parser
import logging
import re
import requests
from typing import Any, Callable, Awaitable

from core.context import mcp
from core.proxy.ssrf_safety import _safe_async_client
from plugins.job_search_plugin.posting_ingest import (
    classify_fetch_block,
    empty_compensation,
    extract_board_identity,
    is_no_api_provider,
    parse_pasted_posting,
    posting_envelope,
)
from utils import safe_api_call, safe_json_response

logger = logging.getLogger("whiskers.plugins")


def _default_compensation() -> dict:
    return empty_compensation()


async def _get_vault_key(key_name: str) -> str | None:
    """Retrieve a configuration credential key from the database vault.

    Checks both the context vault singleton and the plugin registry.
    """
    from core.context import vault
    if vault is not None:
        return await vault.get("job_search_plugin", key_name)
    try:
        from core.plugin_loader.plugin_registry import get_registry
        reg = get_registry()
        if reg and reg.vault:
            return await reg.vault.get("job_search_plugin", key_name)
    except Exception:
        logger.debug("job_search_tools.py: swallowed exception", exc_info=True)
    return None


# Provider API implementation functions

async def search_greenhouse(query: str, location: str, credentials: dict) -> list[dict]:
    """Execute a job search on Greenhouse REST API."""
    api_key = credentials.get("GREENHOUSE_API_KEY", "")
    url = "https://api.greenhouse.io/v1/jobs"
    headers = {"Authorization": f"Basic {api_key}"}
    params = {"q": query}
    if location:
        params["location"] = location

    def make_req():
        """Send GET request to Greenhouse job API."""
        return requests.get(url, headers=headers, params=params, timeout=30)

    def on_success(resp):
        """Parse Greenhouse API response into structured list on success."""
        data = safe_json_response(resp)
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        out = []
        for j in jobs:
            out.append({
                "provider": "greenhouse",
                "job_id": str(j.get("id", "")),
                "title": j.get("title", ""),
                "company": j.get("company", {}).get("name") if isinstance(j.get("company"), dict) else j.get("company", ""),
                "location": j.get("location", {}).get("name") if isinstance(j.get("location"), dict) else j.get("location", ""),
                "url": j.get("url", j.get("absolute_url", ""))
            })
        return out

    res = await safe_api_call(
        make_req,
        on_success,
        context="search_greenhouse",
        plugin_id="job_search_plugin",
        raw_response=True,
        raise_tool_error=False,
    )
    return res if isinstance(res, list) else []


async def search_lever(query: str, location: str, credentials: dict) -> list[dict]:
    """Execute a job search on Lever REST API."""
    api_key = credentials.get("LEVER_API_KEY", "")
    url = "https://api.lever.co/v1/jobs"
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"q": query}
    if location:
        params["location"] = location

    def make_req():
        """Send GET request to Lever job API."""
        return requests.get(url, headers=headers, params=params, timeout=30)

    def on_success(resp):
        """Parse Lever API response into structured list on success."""
        data = safe_json_response(resp)
        jobs = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        out = []
        for j in jobs:
            out.append({
                "provider": "lever",
                "job_id": str(j.get("id", "")),
                "title": j.get("title", ""),
                "company": j.get("company", {}).get("name") if isinstance(j.get("company"), dict) else j.get("company", ""),
                "location": j.get("location", {}).get("name") if isinstance(j.get("location"), dict) else j.get("location", ""),
                "url": j.get("url", j.get("absolute_url", ""))
            })
        return out

    res = await safe_api_call(
        make_req,
        on_success,
        context="search_lever",
        plugin_id="job_search_plugin",
        raw_response=True,
        raise_tool_error=False,
    )
    return res if isinstance(res, list) else []


async def search_adzuna(query: str, location: str, credentials: dict) -> list[dict]:
    """Execute a job search on Adzuna REST API."""
    app_id = credentials.get("ADZUNA_APP_ID", "")
    app_key = credentials.get("ADZUNA_APP_KEY", "")
    url = "https://api.adzuna.com/v1/api/jobs/us/search/1"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": query,
    }
    if location:
        params["where"] = location

    def make_req():
        """Send GET request to Adzuna job API."""
        return requests.get(url, params=params, timeout=30)

    def on_success(resp):
        """Parse Adzuna API response into structured list on success."""
        data = safe_json_response(resp)
        jobs = data.get("results", []) if isinstance(data, dict) else []
        out = []
        for j in jobs:
            out.append({
                "provider": "adzuna",
                "job_id": str(j.get("id", "")),
                "title": j.get("title", ""),
                "company": j.get("company", {}).get("display_name") if isinstance(j.get("company"), dict) else j.get("company", ""),
                "location": j.get("location", {}).get("display_name") if isinstance(j.get("location"), dict) else j.get("location", ""),
                "url": j.get("redirect_url", j.get("url", ""))
            })
        return out

    res = await safe_api_call(
        make_req,
        on_success,
        context="search_adzuna",
        plugin_id="job_search_plugin",
        raw_response=True,
        raise_tool_error=False,
    )
    return res if isinstance(res, list) else []


async def search_remotive(query: str, location: str, credentials: dict) -> list[dict]:
    """Execute a job search on Remotive REST API."""
    url = "https://remotive.com/api/remote-jobs"
    params = {"search": query}
    if location:
        params["location"] = location

    def make_req():
        """Send GET request to Remotive job API."""
        return requests.get(url, params=params, timeout=30)

    def on_success(resp):
        """Parse Remotive API response into structured list on success."""
        data = safe_json_response(resp)
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        out = []
        for j in jobs:
            out.append({
                "provider": "remotive",
                "job_id": str(j.get("id", "")),
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("candidate_required_location", ""),
                "url": j.get("url", "")
            })
        return out

    res = await safe_api_call(
        make_req,
        on_success,
        context="search_remotive",
        plugin_id="job_search_plugin",
        raw_response=True,
        raise_tool_error=False,
    )
    return res if isinstance(res, list) else []


# Provider details implementation functions

async def get_greenhouse_details(job_id: str, credentials: dict) -> dict:
    """Fetch job details from Greenhouse REST API."""
    api_key = credentials.get("GREENHOUSE_API_KEY", "")
    url = f"https://api.greenhouse.io/v1/jobs/{job_id}"
    headers = {"Authorization": f"Basic {api_key}"}

    def make_req():
        """Send GET details request to Greenhouse API."""
        return requests.get(url, headers=headers, timeout=30)

    def on_success(resp):
        """Parse Greenhouse details response on success."""
        data = safe_json_response(resp)
        return {
            "status": "ok",
            "job_id": job_id,
            "provider": "greenhouse",
            "title": data.get("title", ""),
            "description": data.get("content", data.get("description", "")),
            "company": data.get("company", {}).get("name") if isinstance(data.get("company"), dict) else data.get("company", ""),
            "location": data.get("location", {}).get("name") if isinstance(data.get("location"), dict) else data.get("location", ""),
            "url": data.get("url", data.get("absolute_url", ""))
        }

    res = await safe_api_call(
        make_req,
        on_success,
        context="get_greenhouse_details",
        plugin_id="job_search_plugin",
        raw_response=True,
        raise_tool_error=True,
    )
    return res if isinstance(res, dict) else {}


async def get_lever_details(job_id: str, credentials: dict) -> dict:
    """Fetch job details from Lever REST API."""
    api_key = credentials.get("LEVER_API_KEY", "")
    url = f"https://api.lever.co/v1/jobs/{job_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    def make_req():
        """Send GET details request to Lever API."""
        return requests.get(url, headers=headers, timeout=30)

    def on_success(resp):
        """Parse Lever details response on success."""
        data = safe_json_response(resp)
        return {
            "status": "ok",
            "job_id": job_id,
            "provider": "lever",
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "company": data.get("company", {}).get("name") if isinstance(data.get("company"), dict) else data.get("company", ""),
            "location": data.get("location", {}).get("name") if isinstance(data.get("location"), dict) else data.get("location", ""),
            "url": data.get("url", data.get("absolute_url", ""))
        }

    res = await safe_api_call(
        make_req,
        on_success,
        context="get_lever_details",
        plugin_id="job_search_plugin",
        raw_response=True,
        raise_tool_error=True,
    )
    return res if isinstance(res, dict) else {}


async def get_adzuna_details(job_id: str, credentials: dict) -> dict:
    """Fetch job details from Adzuna REST API."""
    app_id = credentials.get("ADZUNA_APP_ID", "")
    app_key = credentials.get("ADZUNA_APP_KEY", "")
    url = f"https://api.adzuna.com/v1/api/jobs/us/details/{job_id}"
    params = {"app_id": app_id, "app_key": app_key}

    def make_req():
        """Send GET details request to Adzuna API."""
        return requests.get(url, params=params, timeout=30)

    def on_success(resp):
        """Parse Adzuna details response on success."""
        data = safe_json_response(resp)
        return {
            "status": "ok",
            "job_id": job_id,
            "provider": "adzuna",
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "company": data.get("company", {}).get("display_name") if isinstance(data.get("company"), dict) else data.get("company", ""),
            "location": data.get("location", {}).get("display_name") if isinstance(data.get("location"), dict) else data.get("location", ""),
            "url": data.get("redirect_url", data.get("url", ""))
        }

    res = await safe_api_call(
        make_req,
        on_success,
        context="get_adzuna_details",
        plugin_id="job_search_plugin",
        raw_response=True,
        raise_tool_error=True,
    )
    return res if isinstance(res, dict) else {}


async def get_remotive_details(job_id: str, credentials: dict) -> dict:
    """Fetch job details from Remotive REST API."""
    url = f"https://remotive.com/api/remote-jobs/{job_id}"

    def make_req():
        """Send GET details request to Remotive API."""
        return requests.get(url, timeout=30)

    def on_success(resp):
        """Parse Remotive details response on success."""
        data = safe_json_response(resp)
        return {
            "status": "ok",
            "job_id": job_id,
            "provider": "remotive",
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "company": data.get("company_name", ""),
            "location": data.get("candidate_required_location", ""),
            "url": data.get("url", "")
        }

    res = await safe_api_call(
        make_req,
        on_success,
        context="get_remotive_details",
        plugin_id="job_search_plugin",
        raw_response=True,
        raise_tool_error=True,
    )
    return res if isinstance(res, dict) else {}


# Registry dictionaries mapped by lowercase provider key

PROVIDERS: dict[str, Callable[[str, str, dict], Awaitable[list[dict]]]] = {
    "greenhouse": search_greenhouse,
    "lever": search_lever,
    "adzuna": search_adzuna,
    "remotive": search_remotive,
}

DETAIL_PROVIDERS: dict[str, Callable[[str, dict], Awaitable[dict]]] = {
    "greenhouse": get_greenhouse_details,
    "lever": get_lever_details,
    "adzuna": get_adzuna_details,
    "remotive": get_remotive_details,
}

NO_API_PROVIDERS: list[str] = ["linkedin", "indeed"]

REQUIRED_CREDENTIALS: dict[str, list[str]] = {
    "greenhouse": ["GREENHOUSE_API_KEY"],
    "lever": ["LEVER_API_KEY"],
    "adzuna": ["ADZUNA_APP_ID", "ADZUNA_APP_KEY"],
    "remotive": [],
}


# MCP Tool Definitions

@mcp.tool(
    title="search_jobs",
    tags={"job_search_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def search_jobs(
    query: str,
    location: str = "",
    providers: list[str] | None = None,
) -> dict:
    """Search for jobs matching query and location across specified providers.

    Aggregates results from available providers. Requires relevant vault keys.
    """
    # Build list of normalized providers to query
    if providers is None:
        target_providers = list(PROVIDERS.keys()) + NO_API_PROVIDERS
    else:
        target_providers = [p.lower() for p in providers]

    results = []
    browser_scrape_needed = []
    skipped = []

    for p in target_providers:
        if p in NO_API_PROVIDERS:
            # NO_API providers yield a mock browser fallback result
            results.append({
                "provider": p,
                "needs_browser_scrape": True,
                "query": query,
                "location": location,
            })
            browser_scrape_needed.append(p)
        elif p in PROVIDERS:
            # Check for credential presence in vault
            req_keys = REQUIRED_CREDENTIALS.get(p, [])
            credentials = {}
            has_creds = True
            for key in req_keys:
                val = await _get_vault_key(key)
                if not val:
                    has_creds = False
                    break
                credentials[key] = val

            if not has_creds:
                skipped.append(p)
                continue

            # Call search callable
            search_func = PROVIDERS[p]
            p_jobs = await search_func(query, location, credentials)
            results.extend(p_jobs)
        else:
            # Unrecognized/unsupported provider
            skipped.append(p)

    return {
        "status": "ok",
        "count": len(results),
        "results": results,
        "browser_scrape_needed": browser_scrape_needed,
        "skipped": skipped,
    }


@mcp.tool(
    title="get_job_details",
    tags={"job_search_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_job_details(
    job_id: str,
    provider: str,
) -> dict:
    """Fetch the full job description and posting details for a specific job.

    Fetches from the REST detail endpoint or identifies if scraping is needed.
    """
    p_low = provider.lower()
    if p_low in NO_API_PROVIDERS or p_low not in DETAIL_PROVIDERS:
        return {
            "status": "error",
            "error": "no_api_for_provider",
            "needs_browser_scrape": True,
        }

    # Fetch required credentials
    req_keys = REQUIRED_CREDENTIALS.get(p_low, [])
    credentials = {}
    for key in req_keys:
        val = await _get_vault_key(key)
        if not val:
            return {
                "status": "error",
                "error": "missing_credentials",
                "message": f"Missing credentials {key} for provider {provider}",
            }
        credentials[key] = val

    # Call provider details function
    detail_func = DETAIL_PROVIDERS[p_low]
    return await detail_func(job_id, credentials)


# ─── Unified zero-auth ingestion (fetch_job_posting) ───────────────────────
#
# Ashby/Greenhouse/Lever public board APIs are NOT routed through PROVIDERS/
# REQUIRED_CREDENTIALS/_get_vault_key above — that machinery gates *keyed*
# REST providers (Greenhouse basic-auth, Lever bearer, Adzuna app_id/app_key)
# and would silently skip these zero-auth endpoints for lacking vault keys
# they don't need. These go through their own code path, gated by the same
# SSRF-safe transport (_safe_async_client / _SSRFSafeTransport) that guards
# every other caller-supplied-URL fetch in this codebase.

_ASHBY_URL_RE = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([^/?#]+)")
_GREENHOUSE_URL_RE = re.compile(r"boards\.greenhouse\.io/([^/?#]+)/jobs/([^/?#]+)")
_LEVER_URL_RE = re.compile(r"jobs\.lever\.co/([^/?#]+)/([^/?#]+)")


def _detect_ats_provider(url: str) -> tuple[str, str, str] | None:
    """Detect (provider, company_slug, job_id) from a known ATS posting URL, else None."""
    for provider, pattern in (
        ("ashby", _ASHBY_URL_RE),
        ("greenhouse", _GREENHOUSE_URL_RE),
        ("lever", _LEVER_URL_RE),
    ):
        m = pattern.search(url)
        if m:
            return provider, m.group(1), m.group(2)
    return None


class _TextExtractor(html.parser.HTMLParser):
    """Minimal HTML-to-text extractor for ATS description fields.

    Local implementation, not imported from search_plugin — plugins may not
    import a sibling's internals (CLAUDE.md §1).
    """

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", " ".join(self._parts)).strip()


def _html_to_text(raw_html: str, max_input: int = 100_000) -> str:
    """Strip HTML tags/scripts/styles down to readable plain text."""
    parser = _TextExtractor()
    try:
        parser.feed(raw_html[:max_input])
    except Exception:
        logger.debug("job_search_tools.py: HTML parse failed, returning raw text", exc_info=True)
        return raw_html[:max_input]
    return parser.text()


def _clean_html_description(raw: str) -> str:
    """Normalize an ATS description field to plain text.

    Some boards (observed live on Greenhouse) return content already
    HTML-entity-encoded (literal "&lt;div&gt;" rather than "<div>"), so a
    plain "<" in raw check misses it entirely and the entities pass through
    unstripped. Unescape first, then strip tags only if any remain.
    """
    if not raw:
        return raw
    unescaped = html.unescape(raw)
    return _html_to_text(unescaped) if "<" in unescaped else unescaped


async def _fetch_ashby_posting(company_slug: str, job_id: str) -> dict:
    """Fetch one posting from Ashby's public job-board API (zero-auth)."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}?includeCompensation=true"
    try:
        async with _safe_async_client(timeout=30) as client:
            resp = await client.get(url, headers={"User-Agent": "WhiskersAgent/1.0"})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return {"status": "error", "error": "api_error", "message": str(exc)}

    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    job = next((j for j in jobs if str(j.get("id")) == job_id), None)
    if job is None:
        return {"status": "error", "error": "job_not_found", "provider": "ashby"}

    comp = _default_compensation()
    comp_block = job.get("compensation")
    if isinstance(comp_block, dict):
        components = comp_block.get("summaryComponents") or []
        if components and isinstance(components[0], dict):
            first = components[0]
            comp["min"] = first.get("minValue")
            comp["max"] = first.get("maxValue")
            comp["currency"] = first.get("currencyCode")

    location = job.get("location") or job.get("locationName") or ""
    if isinstance(location, dict):
        location = location.get("name", "")

    description = job.get("descriptionHtml") or job.get("descriptionPlain") or ""
    clean_description = _clean_html_description(description)

    return {
        "status": "ok",
        "provider": "ashby",
        "job_id": job_id,
        "title": job.get("title", ""),
        "company": company_slug,
        "location": location,
        "compensation": comp,
        "clean_description": clean_description,
        "source_url": f"https://jobs.ashbyhq.com/{company_slug}/{job_id}",
        "posted_at": job.get("publishedAt") or job.get("publishedDate") or None,
    }


async def _fetch_greenhouse_posting(company_slug: str, job_id: str) -> dict:
    """Fetch one posting from Greenhouse's public board API (zero-auth)."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs/{job_id}"
    try:
        async with _safe_async_client(timeout=30) as client:
            resp = await client.get(url, headers={"User-Agent": "WhiskersAgent/1.0"})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return {"status": "error", "error": "api_error", "message": str(exc)}

    if not isinstance(data, dict) or not data.get("id"):
        return {"status": "error", "error": "job_not_found", "provider": "greenhouse"}

    location = data.get("location", {})
    if isinstance(location, dict):
        location = location.get("name", "")

    content = data.get("content", "")
    clean_description = _clean_html_description(content)

    return {
        "status": "ok",
        "provider": "greenhouse",
        "job_id": job_id,
        "title": data.get("title", ""),
        "company": company_slug,
        "location": location,
        "compensation": _default_compensation(),
        "clean_description": clean_description,
        "source_url": data.get("absolute_url", url),
        "posted_at": data.get("updated_at"),
    }


async def _fetch_lever_posting(company_slug: str, job_id: str) -> dict:
    """Fetch one posting from Lever's public postings API (zero-auth)."""
    url = f"https://api.lever.co/v0/postings/{company_slug}/{job_id}"
    try:
        async with _safe_async_client(timeout=30) as client:
            resp = await client.get(url, headers={"User-Agent": "WhiskersAgent/1.0"})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return {"status": "error", "error": "api_error", "message": str(exc)}

    if not isinstance(data, dict) or not data.get("id"):
        return {"status": "error", "error": "job_not_found", "provider": "lever"}

    categories = data.get("categories", {}) if isinstance(data.get("categories"), dict) else {}
    salary = data.get("salaryRange") if isinstance(data.get("salaryRange"), dict) else {}

    description = data.get("descriptionPlain") or data.get("description") or ""
    clean_description = _clean_html_description(description)

    return {
        "status": "ok",
        "provider": "lever",
        "job_id": job_id,
        "title": data.get("text", ""),
        "company": company_slug,
        "location": categories.get("location", ""),
        "compensation": {
            "min": salary.get("min"),
            "max": salary.get("max"),
            "currency": salary.get("currency"),
        },
        "clean_description": clean_description,
        "source_url": data.get("hostedUrl") or data.get("applyUrl") or url,
        "posted_at": data.get("createdAt"),
    }


async def _fetch_generic_web_posting(url: str) -> dict:
    """Fallback for non-ATS URLs (including Workday — no dedicated adapter).

    Dispatches search_plugin's fetch_url via execute_operation instead of a
    direct import — SSRF safety is inherited from that tool's own
    _safe_async_client usage (CLAUDE.md §1: no plugin→plugin imports).
    """
    from core.route_registry.execute import execute_operation

    try:
        result = await execute_operation(
            "search_plugin",
            "fetch_url",
            {"url": url},
            caller_scopes=None,
        )
    except Exception as exc:
        return {"status": "error", "error": "api_error", "message": str(exc)}

    if not isinstance(result, dict) or result.get("status") != "ok":
        message = result.get("message") if isinstance(result, dict) else "fetch_url failed"
        blocked = classify_fetch_block(result if isinstance(result, dict) else None)
        return {
            "status": "error",
            "error": blocked or "api_error",
            "message": message,
            "needs_browser_scrape": bool(blocked),
            "is_potential_ghost_job": False,
        }

    content = result.get("content", "") or ""
    blocked = classify_fetch_block(result, content)
    if blocked:
        return {
            "status": "error",
            "error": "needs_browser_scrape",
            "message": "URL fetch returned a login/captcha wall, not a job posting.",
            "needs_browser_scrape": True,
            "is_potential_ghost_job": False,
            "clean_description": "",
        }

    return {
        "status": "ok",
        "provider": "generic_web",
        "job_id": "",
        "title": "",
        "company": "",
        "location": "",
        "compensation": _default_compensation(),
        "clean_description": content,
        "source_url": url,
        "posted_at": None,
    }


def _envelope_from_parsed(
    parsed: dict,
    *,
    provider: str,
    job_id: str,
    source_url: str,
    ingest_method: str,
    posted_at=None,
    extra: dict | None = None,
) -> dict:
    """Merge paste/page parse slots onto the ATS-shaped envelope."""
    return posting_envelope(
        status="ok",
        provider=provider,
        job_id=job_id,
        title=parsed.get("title") or "",
        company=parsed.get("company") or "",
        location=parsed.get("location") or "",
        compensation=parsed.get("compensation") or _default_compensation(),
        clean_description=parsed.get("clean_description") or "",
        source_url=source_url,
        posted_at=posted_at,
        job_type=parsed.get("job_type") or "",
        posting_entity=parsed.get("posting_entity") or "",
        via=parsed.get("via") or "",
        end_employer=parsed.get("end_employer"),
        employment_class=parsed.get("employment_class") or "unknown",
        ingest_status="ok",
        ingest_method=ingest_method,
        is_agency=bool(parsed.get("is_agency")),
        extra=extra,
    )


def _blocked_envelope(url: str, identity: dict, fetch_result: dict | None) -> dict:
    """Degraded ingest for a gated NO_API / challenge-page fetch."""
    message = ""
    if isinstance(fetch_result, dict):
        message = str(fetch_result.get("message") or "")
    return posting_envelope(
        status="error",
        provider=identity.get("provider") or "generic_web",
        job_id=identity.get("job_id") or "",
        source_url=url,
        ingest_status="needs_browser_scrape",
        ingest_method="blocked",
        needs_browser_scrape=True,
        error="needs_browser_scrape",
        message=message or (
            "Posting URL is gated (Cloudflare/login/captcha). "
            "Paste the JD as raw_text to continue — this is not a ghost-job signal."
        ),
    )


@mcp.tool(
    title="fetch_job_posting",
    tags={"job_search_plugin", "search", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fetch_job_posting(
    url: str = "",
    raw_text: str = "",
) -> dict:
    """Fetches and normalizes a job posting from any URL or raw text.

    Auto-detects Ashby, Greenhouse, or Lever public board URLs and calls their
    zero-auth REST APIs directly. Anything else (including Workday — no
    dedicated adapter, deliberately) falls to the generic-web path via
    search_plugin.fetch_url. ``url`` and ``raw_text`` may be supplied together:
    the paste is parsed into the ATS slot schema and the URL is kept (Indeed
    ``jk``/``vjk`` becomes ``job_id``) without fetching a gated board.
    Indeed/LinkedIn Cloudflare/401 is ``needs_browser_scrape``, never a
    ghost-job signal. Every outbound fetch of a caller-supplied URL goes
    through the SSRF-safe transport (core.proxy.ssrf_safety).
    """
    identity = extract_board_identity(url) if url else {"provider": "", "job_id": "", "source_url": ""}

    if raw_text:
        parsed = parse_pasted_posting(raw_text)
        provider = identity.get("provider") or "raw_text"
        return _envelope_from_parsed(
            parsed,
            provider=provider,
            job_id=identity.get("job_id") or "",
            source_url=url or identity.get("source_url") or "",
            ingest_method="paste",
        )

    if not url:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["url"],
            "message": "Provide either url or raw_text.",
        }

    detected = _detect_ats_provider(url)
    if detected is None:
        result = await _fetch_generic_web_posting(url)
        blocked = bool(result.get("needs_browser_scrape")) or classify_fetch_block(result) == "needs_browser_scrape"
        no_api = is_no_api_provider(identity.get("provider") or "", url)
        if blocked or (no_api and result.get("status") != "ok"):
            return _blocked_envelope(url, identity, result)
        if result.get("status") != "ok":
            return result
        parsed = parse_pasted_posting(result.get("clean_description") or "")
        # Prefer page-extracted description; fill empty ATS-like slots from parse.
        merged = {
            **parsed,
            "clean_description": result.get("clean_description") or parsed.get("clean_description") or "",
        }
        return _envelope_from_parsed(
            merged,
            provider=identity.get("provider") or result.get("provider") or "generic_web",
            job_id=identity.get("job_id") or result.get("job_id") or "",
            source_url=url,
            ingest_method="generic_web",
            posted_at=result.get("posted_at"),
        )

    provider, company_slug, job_id = detected
    if provider == "ashby":
        result = await _fetch_ashby_posting(company_slug, job_id)
    elif provider == "greenhouse":
        result = await _fetch_greenhouse_posting(company_slug, job_id)
    else:
        result = await _fetch_lever_posting(company_slug, job_id)

    if isinstance(result, dict) and result.get("status") == "ok":
        result.setdefault("job_type", "")
        result.setdefault("posting_entity", result.get("company") or company_slug)
        result.setdefault("via", "")
        result.setdefault("end_employer", result.get("company") or None)
        result.setdefault("employment_class", "unknown")
        result.setdefault("needs_browser_scrape", False)
        result.setdefault("ingest_status", "ok")
        result.setdefault("ingest_method", "ats")
        result.setdefault("is_agency", False)
        result.setdefault("is_potential_ghost_job", False)
    return result
