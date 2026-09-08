"""HTTP routes for the portfolio plugin."""

import logging
import re
import time
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from plugins.portfolio_plugin.tenant import PORTFOLIO_TENANT_ID
from plugins.portfolio_plugin.themes import SUPPORTED_THEMES, public_raw_defs

logger = logging.getLogger("whiskers.plugins.portfolio.routes")

PATH = "/api/portfolio/public/layout"
JOB_LAYOUT_PATH = "/api/portfolio/public/layout/{job_id}"
LAYOUT_FOR_QUERY_PATH = "/api/portfolio/public/layout-for-query"
DESIGN_CONTEXT_PATH = "/api/portfolio/public/design-context"
AGENT_STATUS_PATH = "/api/portfolio/public/agent-status"
FRAGMENTS_PATH = "/api/portfolio/public/fragments"
COMPOSE_PATH = "/api/portfolio/public/compose"
ASSET_PATH = "/api/portfolio/public/asset/{short_id}"
AUDIENCES = {"recruiter", "hiring-manager", "peer", "default"}
JOB_ID_RE = re.compile(r"^[a-z0-9_]{1,80}$")

# Live layout-for-query cache (anonymous path; bounds outbound fan-out).
_QUERY_LAYOUT_TTL_S = 60.0
_query_layout_cache: dict[str, tuple[float, dict]] = {}
_compose_cache: dict[str, tuple[float, dict]] = {}


async def portfolio_layout(request: Request) -> JSONResponse:
    """Compose and return the portfolio layout for the requested audience.

    Uses the pure DB snapshot (``refresh=False``) so the 60s cache never
    amplifies anonymous GitHub fan-out.

    ``?tank=1`` opts into a fishTank block. Default stays off so the plain
    snapshot is text-only; the ask surface asks for the tank because that is
    the block a visitor question focuses or adds a specimen to.
    """
    audience = request.query_params.get("audience", "default")
    if audience not in AUDIENCES:
        audience = "default"
    want_tank = str(request.query_params.get("tank", "")).strip().lower() in {"1", "true", "yes"}
    try:
        from plugins.portfolio_plugin.compose.floor import build_floor_layout
        layout = await build_floor_layout(
            "", audience=audience, refresh=False, include_fish_tank=want_tank
        )
    except Exception:
        logger.exception("portfolio layout composition failed")
        return JSONResponse({"error": "layout_unavailable"}, status_code=503)
    return JSONResponse(layout, headers={"Cache-Control": "public, max-age=60"})


async def portfolio_design_context(request: Request) -> JSONResponse:
    """Return the design context (tokens, audience template, block types) for agents."""
    audience = request.query_params.get("audience", "default")
    if audience not in AUDIENCES:
        audience = "default"
    try:
        import plugins.portfolio_plugin.schema.ui_layout_schema as ui_layout_schema
        from plugins.portfolio_plugin.compose.composer import get_audience_template
        from plugins.portfolio_plugin.plugin_config import SETTINGS
        from plugins.portfolio_plugin.schema.block_catalog import fail_open_block_catalog

        normalized, template = get_audience_template(audience)
        presets = SETTINGS.get("layout_presets", {}) or {}
        payload = {
            "audience": normalized,
            "audience_template": template,
            "hero": SETTINGS.get("hero"),
            "design_tokens": SETTINGS.get("design_tokens", {}),
            "supported_block_types": sorted(ui_layout_schema.BLOCK_TYPES),
            "schema_docstring": ui_layout_schema.__doc__,
            "block_catalog": fail_open_block_catalog(),
            "layout_presets": list(presets.keys()) if isinstance(presets, dict) else [],
            "supported_themes": list(SUPPORTED_THEMES),
            "theme_defs": public_raw_defs(),
            "quick_actions": SETTINGS.get("quick_actions") or [],
            "fragments": [],
        }
    except Exception:
        logger.exception("portfolio design context failed")
        return JSONResponse({"error": "design_context_unavailable"}, status_code=503)
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=60"})


async def portfolio_fragments(request: Request) -> JSONResponse:
    """Gone — fragment catalog removed (floor composer owns deterministic layouts)."""
    return JSONResponse(
        {"error": "fragments_removed", "hint": "Use /layout or /layout-for-query"},
        status_code=410,
    )


async def portfolio_job_layout(request: Request) -> JSONResponse:
    """Return a previously baked job-specific layout artifact by short id.

    Read-only, stateless, no LLM call — just a DB lookup ("bake & send").
    """
    job_id = request.path_params.get("job_id", "")
    if not JOB_ID_RE.match(job_id):
        return JSONResponse({"error": "invalid_job_id"}, status_code=400)
    try:
        from plugins.portfolio_plugin.store import get_job_layout_by_short_id
        record = await get_job_layout_by_short_id(job_id)
    except Exception:
        logger.exception("portfolio job layout lookup failed")
        return JSONResponse({"error": "layout_unavailable"}, status_code=503)
    if record is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    # Match the sibling /public/layout TTL (60s) so a re-bake is visible within a minute.
    return JSONResponse(record["layout_json"], headers={"Cache-Control": "public, max-age=60"})


async def portfolio_layout_for_query(request: Request) -> JSONResponse:
    """Fast, public, no-MCP-handshake path for chat-driven layout re-render.

    Fragment-catalog compose + live context_sources refresh (fail-open), behind
    a 60s TTL cache keyed by normalized query. Used by CatPortfolio Ask mode
    for real-time page re-render without waiting on run_graph.
    """
    query = request.query_params.get("query", "")
    if not query.strip():
        return JSONResponse({"error": "missing_query"}, status_code=400)

    cache_key = " ".join(query.strip().lower().split())
    now = time.monotonic()
    hit = _query_layout_cache.get(cache_key)
    if hit and (now - hit[0]) < _QUERY_LAYOUT_TTL_S:
        return JSONResponse(hit[1], headers={"Cache-Control": "no-store", "X-Portfolio-Mode": "cache"})

    try:
        from plugins.portfolio_plugin.MCPTools.portfolio_tools import generate_layout_for_query
        result = await generate_layout_for_query(user_query=query)
    except Exception:
        logger.exception("portfolio layout-for-query failed")
        return JSONResponse({"error": "layout_unavailable"}, status_code=503)
    if result.get("status") != "ok":
        return JSONResponse(result, status_code=400)
    layout = result["layout"]
    # Envelope for FE: layout + mode so Ask can show "live · fragments"
    payload = {
        "layout": layout,
        "mode": result.get("mode") or "template",
        "audience": result.get("audience"),
        "fragments": result.get("fragments") or [],
    }
    _query_layout_cache[cache_key] = (now, payload)
    if len(_query_layout_cache) > 256:
        stale = [k for k, (ts, _) in _query_layout_cache.items() if (now - ts) >= _QUERY_LAYOUT_TTL_S]
        for k in stale:
            _query_layout_cache.pop(k, None)
    return JSONResponse(
        payload,
        headers={
            "Cache-Control": "no-store",
            "X-Portfolio-Mode": str(result.get("mode") or "template"),
        },
    )


async def portfolio_compose(request: Request) -> JSONResponse:
    """POST public compose — fragment page and/or free-text intent.

    Body JSON::
        {
          "query": "show me infra work",          # optional if page given
          "page": [{"fragment":"hero.compact"}], # optional explicit fragments
          "theme": "neon",
          "refresh": false                       # default false — avoid anon fan-out
        }

    Used by Ask mode for real-time fragment baking without MCP auth.
    Defaults ``refresh=False`` (same rationale as ``portfolio_layout``) so the
    60s cache never amplifies anonymous GitHub/Notion fan-out.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    query = str(body.get("query") or request.query_params.get("query") or "").strip()
    page = body.get("page")
    theme = str(body.get("theme") or "").strip()
    refresh = body.get("refresh")
    if refresh is None:
        refresh = False  # match portfolio_layout: no anonymous live enrich by default

    if not query and not (isinstance(page, list) and page):
        return JSONResponse(
            {"error": "missing_query_or_page", "message": "Provide query and/or page fragments."},
            status_code=400,
        )

    # Normalize query for cache key so trivial case/whitespace variants share TTL.
    query_norm = " ".join(query.lower().split())
    theme_norm = theme.strip().lower()
    page_key = page if isinstance(page, list) else None
    cache_key = f"{query_norm}|{theme_norm}|{bool(refresh)}|{page_key!r}"[:400]
    now = time.monotonic()
    hit = _compose_cache.get(cache_key)
    if hit and (now - hit[0]) < _QUERY_LAYOUT_TTL_S:
        return JSONResponse(hit[1], headers={"Cache-Control": "no-store"})

    try:
        from plugins.portfolio_plugin.compose.intent_compose import compose_intent_layout
        result = await compose_intent_layout(
            query or "portfolio showcase",
            tenant_id=PORTFOLIO_TENANT_ID,
            theme=theme,
            refresh=bool(refresh),
            page=page if isinstance(page, list) else None,
            use_fragments=True,
        )
    except Exception:
        logger.exception("portfolio compose failed")
        return JSONResponse({"error": "compose_unavailable"}, status_code=503)

    if result.get("status") != "ok":
        return JSONResponse(result, status_code=400)

    payload = {
        "status": "ok",
        "layout": result["layout"],
        "mode": result.get("mode"),
        "audience": result.get("audience"),
        "fragments": result.get("fragments") or [],
        "star_query": result.get("star_query"),
    }
    _compose_cache[cache_key] = (now, payload)
    if len(_compose_cache) > 256:
        stale = [k for k, (ts, _) in _compose_cache.items() if (now - ts) >= _QUERY_LAYOUT_TTL_S]
        for k in stale:
            _compose_cache.pop(k, None)
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


async def portfolio_asset(request: Request) -> Response:
    """Public, anonymous streaming route for a baked portfolio visual asset
    (Phase 6c PNG poster frames / OG images). Never a presign -- see
    asset_store.py's module docstring for why. The kind allowlist inside
    resolve_public_asset is the only thing standing between this route and
    becoming a public read surface over the whole GOAP offload bucket; it is
    not optional.
    """
    short_id = request.path_params.get("short_id", "")
    from plugins.portfolio_plugin.render.asset_store import is_valid_asset_short_id, resolve_public_asset

    if not is_valid_asset_short_id(short_id):
        return JSONResponse({"error": "invalid_short_id"}, status_code=400)
    try:
        result = await resolve_public_asset(short_id)
    except Exception:
        logger.exception("portfolio asset lookup failed")
        return JSONResponse({"error": "asset_unavailable"}, status_code=503)
    if result is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    meta, body = result
    return Response(
        content=body,
        media_type=meta.get("content_type") or "image/png",
        headers={
            # Content-addressed object key (sha8 of the bytes) -- safe to
            # cache forever; a changed asset gets a new key, not a new byte
            # range at the same URL.
            "Cache-Control": "public, max-age=31536000, immutable",
            # A plain <img> isn't CORS-gated, but THREE.TextureLoader into
            # WebGL is (canvas tainting) -- set this now so the eventual
            # webgl scene2d renderer doesn't need a route change later.
            "Access-Control-Allow-Origin": "*",
        },
    )


async def portfolio_agent_status(request: Request) -> JSONResponse:
    """Public, privacy-safe snapshot of the job-search agent's latest activity.

    Never returns applicant PII — only {job_id, status, updated_at}. Polled
    (not SSE) by the portfolio's live-status widget; real-time push is a
    deliberate future enhancement, not required for v1.
    """
    job_id = request.query_params.get("job_id") or None
    portfolio_job_id = request.query_params.get("j") or None
    try:
        # Cross-plugin dependency: job_search_plugin may not be in the boot list
        # on every install. Dispatched through the catalog (no import), and still
        # caught broadly so an absent plugin degrades to 503 rather than hard-
        # failing the portfolio route. caller_scopes=None is the trusted-local
        # marker used elsewhere for internal dispatch; the op itself returns no
        # PII, only {job_id, status, updated_at}.
        from core.route_registry.execute import execute_operation

        result = await execute_operation(
            "job_search_plugin",
            "get_agent_status",
            {
                "job_id": job_id or "",
                "portfolio_job_id": portfolio_job_id or "",
                "_response_shape": {"response_format": "json"},
            },
            caller_scopes=None,
        )
        status = (result or {}).get("activity") if isinstance(result, dict) else None
    except Exception:
        logger.exception("portfolio agent status lookup failed")
        return JSONResponse({"error": "status_unavailable"}, status_code=503)
    if status is None:
        return JSONResponse({"status": "ok", "activity": None}, headers={"Cache-Control": "no-store"})
    return JSONResponse({"status": "ok", "activity": status}, headers={"Cache-Control": "no-store"})


@http_route_registry.route(
    route="portfolio",
    endpoint="ask-turns",
    methods=["GET"],
    name="portfolio_ask_turns",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.analytics:read",),
    owner="portfolio_plugin",
)
async def portfolio_ask_turns(request: Request) -> Response:
    """Recent fish-tank ask turns for the admin console analytics page.

    This is the first non-PUBLIC route in this plugin — the existing operator
    surface for observability (portfolio_bake_runs) is an MCP tool
    (list_bake_runs), not an HTTP route. Tenant comes from the authenticated
    principal, mirroring the SESSION_GATED analytics routes in
    api/analytics_routes.py, not the public PORTFOLIO_TENANT_ID default used
    by the PUBLIC routes above.
    """
    from core.context import current_tenant_id
    from plugins.portfolio_plugin.store import list_ask_turns

    tenant_id = current_tenant_id.get()
    if tenant_id is None:
        return JSONResponse({"status": "error", "error": "tenant_unresolved"}, status_code=403)

    limit = request.query_params.get("limit", "50")
    intent = request.query_params.get("intent") or None
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = 50

    try:
        rows = await list_ask_turns(tenant_id=int(tenant_id), limit=limit_int, intent=intent)
    except Exception:
        logger.exception("portfolio_ask_turns: failed to list ask turns")
        return JSONResponse({"status": "error", "error": "list_failed"}, status_code=503)

    return JSONResponse({"status": "ok", "turns": rows, "count": len(rows)}, headers={"Cache-Control": "no-store"})


def register_routes() -> None:
    """Register HTTP routes for portfolio plugin."""
    http_route_registry.register_http_route(
        PATH,
        portfolio_layout,
        methods=["GET"],
        name="portfolio_layout",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        JOB_LAYOUT_PATH,
        portfolio_job_layout,
        methods=["GET"],
        name="portfolio_job_layout",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        LAYOUT_FOR_QUERY_PATH,
        portfolio_layout_for_query,
        methods=["GET"],
        name="portfolio_layout_for_query",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        COMPOSE_PATH,
        portfolio_compose,
        methods=["POST"],
        name="portfolio_compose",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        DESIGN_CONTEXT_PATH,
        portfolio_design_context,
        methods=["GET"],
        name="portfolio_design_context",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        AGENT_STATUS_PATH,
        portfolio_agent_status,
        methods=["GET"],
        name="portfolio_agent_status",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        FRAGMENTS_PATH,
        portfolio_fragments,
        methods=["GET"],
        name="portfolio_fragments",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        ASSET_PATH,
        portfolio_asset,
        methods=["GET"],
        name="portfolio_asset",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
