"""



API Key management and login routes.

Endpoints
---------
POST   /api/admin/public/login-api-key                       — Verify API key and issue HttpOnly session cookies.
GET    /api/auth/session_gated/api-key-presets               — List saved API key scope presets.
POST   /api/auth/session_gated/api-key-presets               — Create a new saved API key scope preset.
DELETE /api/auth/session_gated/api-key-presets/{preset_id}   — Delete a saved API key scope preset by ID.
GET    /api/auth/session_gated/api-keys                      — Lists API keys for the resolved subject.
POST   /api/auth/session_gated/api-keys                      — Create a new API key for the admin subject.
DELETE /api/auth/session_gated/api-keys/{key_id}             — Delete/remove an API key by ID.
POST   /api/auth/session_gated/api-keys/{key_id}/revoke      — Revoke an API key by ID and return a fresh replacement key (token shown once).
PUT    /api/auth/session_gated/api-keys/{key_id}/scopes      — Update scopes for an API key by ID.
GET    /api/auth/session_gated/scope-vocabulary              — Get valid global OAuth scopes.
"""

from datetime import datetime, timezone, timedelta
import logging
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from api.admin_routes import (
    _check_rate_limit_async,
    _record_attempt_async,
    _set_session_cookie,
    _set_refresh_cookie,
    _safe_internal_redirect,
    _get_oauth_service,
)
from core.api_key_management.store import (
    create_api_key,
    lookup_active_by_token,
    list_api_keys,
    revoke_api_key,
    rotate_api_key,
    delete_api_key,
    update_api_key_scopes,
    create_scope_preset,
    list_scope_presets,
    delete_scope_preset,
)
from utils.server_config import OAUTH_VALID_SCOPES
from core.api_key_management.scopes import scopes_for_role, role_has_admin_bypass

logger = logging.getLogger("whiskers")


async def _resolve_subject(request: Request) -> str:
    session_token = request.cookies.get("session")
    if not session_token:
        return "admin"
    svc = _get_oauth_service()
    if svc is None:
        return "admin"
    try:
        payload = await svc.validate_token(session_token)
        user_id = payload.get("whiskers_user_id") or payload.get("ocat_user_id") or payload.get("username")
        if user_id:
            return str(user_id)
        sub = payload.get("sub")
        if sub == "admin-console":
            return "admin"
        return sub or "admin"
    except Exception:
        logger.warning("_resolve_user_id: session token validation failed; defaulting to admin")
        return "admin"


async def _resolve_tenant_id(request: Request) -> int | None:
    """Resolve tenant_id from the session JWT whiskers_tenant/ocat_tenant claim.

    Returns None when the session is missing, invalid, or lacks the claim.
    Callers must check for None and fail closed (401 unauthenticated).
    """
    session_token = request.cookies.get("session")
    if not session_token:
        return None
    svc = _get_oauth_service()
    if svc is None:
        return None
    try:
        payload = await svc.validate_token(session_token)
        raw = payload.get("whiskers_tenant") if payload.get("whiskers_tenant") is not None else payload.get("ocat_tenant")
        if raw is None:
            return None
        return int(raw)
    except Exception:
        logger.warning("_resolve_tenant_id: session token validation failed")
        return None


def _lowest_privilege_role() -> str:
    """Role with the smallest resolved scope set, excluding admin-bypass roles.

    Fail-closed default for ``_resolve_issuer_role``: a missing/invalid
    ``ocat_role`` claim must never resolve to master privileges (that was
    the escalation path — a narrow API key logging in via
    ``admin_login_api_key`` got no ``ocat_role`` claim stamped, so this
    function's old unconditional ``"master"`` return let it mint
    master-vocabulary keys). Computed dynamically against the configured
    ``ROLES_CONFIG`` rather than hardcoded, so a deployment without a
    "viewer" role still gets a real least-privilege floor.
    """
    import utils.server_config

    candidates = [r for r in utils.server_config.ROLES_CONFIG if not role_has_admin_bypass(r)]
    if not candidates:
        return "viewer"
    return min(candidates, key=lambda r: len(scopes_for_role(r)))


async def _resolve_issuer_role(request: Request) -> str:
    """Resolve issuer role from the session JWT claims.

    No session cookie / no oauth service mirrors ``_resolve_subject``'s
    "unauthenticated request => trusted local admin-console" convention and
    still resolves "master" — these ``session_gated`` routes are only
    reachable in production once ``SessionGateMiddleware`` has already
    required a cookie, so an in-process caller with none is the local/admin
    path, not a network attacker.

    The actual escalation this fixes is narrower: a session cookie that
    *does* validate but carries no ``ocat_role`` claim (a narrow API key
    logged in via ``admin_login_api_key`` before that endpoint stamped the
    claim, or validation raising) must NOT be treated as master. Those cases
    fail closed to the lowest-privilege configured role
    (``_lowest_privilege_role()``).
    """
    role, _ = await _resolve_issuer_grant(request)
    return role


async def _resolve_issuer_grant(request: Request) -> tuple[str, list[str] | None]:
    """Resolve issuer role AND the session's actual scope grant.

    Returns ``(role, scopes)``. ``scopes`` is ``None`` — meaning "no extra
    restriction beyond the role floor" — for the two trusted-local paths (no
    session cookie / no oauth service) and for a session whose token carries
    no ``scopes`` claim at all (every real login path stamps one via
    ``_issue_token_pair(scopes=...)``; the only sessions without it predate
    that or are hand-built). ``[]`` is a real, present-but-narrower claim
    (e.g. a validation failure) and *does* collapse the ceiling to nothing.

    A present, non-empty list is what closes the actual escalation: a narrow
    API key logged in via ``admin_login_api_key`` always stamps its own
    ``scopes`` on the session (line ~287 below), so that path's grant is
    never ``None`` — it is intersected against the role floor for real.
    """
    session_token = request.cookies.get("session")
    if not session_token:
        return "master", None
    svc = _get_oauth_service()
    if svc is None:
        return "master", None
    try:
        payload = await svc.validate_token(session_token)
        role = payload.get("whiskers_role") or payload.get("ocat_role") or _lowest_privilege_role()
        scopes = payload.get("scopes")
        return role, list(scopes) if isinstance(scopes, list) else None
    except Exception:
        logger.warning(
            "_resolve_issuer_grant: session token validation failed; defaulting to lowest-privilege role"
        )
        return _lowest_privilege_role(), []


async def _validate_scopes_against_issuer(request: Request, requested_scopes: list[str]) -> JSONResponse | None:
    """Validate requested scopes against issuer's role scopes ∩ actual session grant.

    If requested scopes exceed issuer's ceiling, returns a 403 JSONResponse.
    Otherwise returns None. Empty scopes ([]) are always allowed.

    Full-access sentinels (``all`` / ``*``) may only be minted by master-like
    wildcard roles — never by listing them on a non-master role in config.

    Escalation fix: the ceiling used to be ``scopes_for_role(role)`` alone —
    a key logging in via ``admin_login_api_key`` always gets the same
    least-privilege *role*, so a key holding only e.g. ``core:apikey:write``
    could mint any default scope of that role floor it never actually held
    (``core:graph:read``, ...). The ceiling is now role scopes intersected
    with the session's real grant (widened through ``expand_implied`` so a
    held ``core:x:write`` still covers minting ``core:x:read``).
    """
    if not requested_scopes:
        return None

    role, session_scopes = await _resolve_issuer_grant(request)
    issuer_scopes = set(scopes_for_role(role))

    # If issuer's role has admin bypass, they are allowed to assign the "admin" scope.
    from core.api_key_management.scopes import role_has_admin_bypass, Scope
    if role_has_admin_bypass(role):
        issuer_scopes.add(Scope.ADMIN)

    # If issuer has full wildcard scopes (e.g. master role), they are allowed to assign sentinel/all scopes.
    import utils.server_config
    role_cfg = utils.server_config.ROLES_CONFIG.get(role, {})
    role_scopes_cfg = role_cfg.get("scopes")
    is_wildcard_issuer = (
        role == "master"
        or (isinstance(role_scopes_cfg, str) and role_scopes_cfg in (Scope.WILDCARD, "*", Scope.ALL, "all"))
    )
    if is_wildcard_issuer:
        return None
    else:
        # Strip full-access sentinels even if misconfigured into role scopes lists.
        issuer_scopes.discard(Scope.ALL)
        issuer_scopes.discard(Scope.WILDCARD)
        issuer_scopes.discard("all")
        issuer_scopes.discard("*")

    # Intersect with the session's actual grant — a role floor alone lets a
    # narrow key mint scopes it never held (that role's defaults). None here
    # only for the trusted-local paths (already returned above via
    # is_wildcard_issuer for role=="master"); a real non-master session
    # always has a list (possibly empty).
    if session_scopes is not None:
        from core.scope_management.grammar import expand_implied

        held_closure: set[str] = set()
        for tok in session_scopes:
            held_closure |= expand_implied(tok)
        issuer_scopes &= held_closure

    requested = set(requested_scopes)

    excess = requested - issuer_scopes
    if excess:
        return JSONResponse(
            {"error": "scopes_exceed_issuer", "excess": sorted(list(excess))},
            status_code=403
        )
    return None


@http_route_registry.route(
    route="admin",
    endpoint="login-api-key",
    methods=["POST"],
    name="admin_login_api_key",
    auth_policy=AuthPolicy.PUBLIC,
    owner="api.apikeys",
)
async def admin_login_api_key(request: Request) -> Response:
    """Verify API key and issue HttpOnly session cookies."""
    ip = request.client.host if request.client else "unknown"
    retry_after = await _check_rate_limit_async(ip)
    if retry_after is not None:
        return JSONResponse(
            {"error": "too_many_attempts", "retry_after": retry_after},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("admin_login_api_key: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    api_key = body.get("api_key")
    state = body.get("state", "")
    next_url = body.get("next", "")

    # Record attempt BEFORE checking token to prevent timing-based bypass
    retry_after = await _record_attempt_async(ip)
    if retry_after is not None:
        return JSONResponse(
            {"error": "too_many_attempts", "retry_after": retry_after},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    if not api_key or not isinstance(api_key, str):
        return JSONResponse({"error": "invalid_api_key"}, status_code=401)

    key_record = await lookup_active_by_token(api_key)
    if not key_record:
        return JSONResponse({"error": "invalid_api_key"}, status_code=401)

    # Key is valid and active, issue session + refresh cookies
    svc = _get_oauth_service()
    if svc is None:
        return JSONResponse({"error": "oauth_service_unavailable"}, status_code=503)

    # Derive session scopes from the key — never hardcode admin (privilege escalation).
    from core.scope_management.sentinels import SCOPE_ADMIN, SCOPE_ALL, SCOPE_WILDCARD

    key_scopes = key_record.get("scopes")
    if key_scopes is None or not isinstance(key_scopes, list):
        # Legacy null scopes treated as full access (pre-scope keys)
        session_scopes = [SCOPE_ADMIN]
    elif SCOPE_ALL in key_scopes or SCOPE_WILDCARD in key_scopes or SCOPE_ADMIN in key_scopes:
        session_scopes = [SCOPE_ADMIN]
    elif not key_scopes:
        # Empty list is deny-all — refuse login rather than mint a useless session
        return JSONResponse({"error": "api_key_no_scopes"}, status_code=403)
    else:
        session_scopes = list(key_scopes)

    extra_claims: dict = {}
    if key_record.get("tenant_id") is not None:
        extra_claims["whiskers_tenant"] = int(key_record["tenant_id"])
        extra_claims["ocat_tenant"] = int(key_record["tenant_id"])
    if key_record.get("subject"):
        extra_claims["whiskers_user_id"] = str(key_record["subject"])
        extra_claims["ocat_user_id"] = str(key_record["subject"])
    # Escalation fix: stamp a real role so _resolve_issuer_role never
    # falls back to "master" for this session. Only a key resolving to the
    # admin sentinel above mints a master-role session; every other key
    # gets the least-privilege role floor.
    role_val = "master" if session_scopes == [SCOPE_ADMIN] else _lowest_privilege_role()
    extra_claims["whiskers_role"] = role_val
    extra_claims["ocat_role"] = role_val

    await svc.ensure_internal_client("admin-console", scopes="admin")
    pair = await svc._issue_token_pair(
        client_id="admin-console",
        scopes=session_scopes,
        extra_claims=extra_claims or None,
    )
    redirect = f"/?state={quote(state, safe='')}" if state else _safe_internal_redirect(next_url)
    response = JSONResponse({"redirect": redirect})
    _set_session_cookie(response, pair["access_token"])
    _set_refresh_cookie(response, pair["refresh_token"])
    logger.info("admin_login_api_key: session issued for IP=%s via API key prefix=%s", ip, api_key[:10])
    return response


@http_route_registry.route(
    route="auth",
    endpoint="api-keys",
    methods=["GET"],
    name="api_list_api_keys",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:apikey:read",),
    owner="api.apikeys",
)
async def api_list_api_keys(request: Request) -> Response:
    """
    Lists API keys for the resolved subject.
    Returns a JSON response of keys. Fetches keys via subject.
    """
    subject = await _resolve_subject(request)
    tenant_id = await _resolve_tenant_id(request)
    if tenant_id is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    keys = await list_api_keys(subject, tenant_id=tenant_id)
    
    # Parse query parameters
    query = request.query_params.get("query", "").strip().lower()
    status = request.query_params.get("status", "all").strip().lower()
    sort_by = request.query_params.get("sort", "created_at").strip().lower()
    
    serialized_keys = []
    for key in keys:
        serialized_keys.append({
            "key_id": key["key_id"],
            "name": key["name"],
            "prefix": key["prefix"],
            "status": key["status"],
            "scopes": key.get("scopes"),
            "expires_at": key["expires_at"].isoformat() if key["expires_at"] else None,
            "last_used_at": key["last_used_at"].isoformat() if key["last_used_at"] else None,
            "created_at": key["created_at"].isoformat() if key["created_at"] else None,
            "revoked_at": key["revoked_at"].isoformat() if key["revoked_at"] else None,
        })

    # Apply filtering
    filtered_keys = []
    for key in serialized_keys:
        if query:
            name_match = query in (key["name"] or "").lower()
            id_match = query in (key["key_id"] or "").lower()
            prefix_match = query in (key["prefix"] or "").lower()
            if not (name_match or id_match or prefix_match):
                continue
                
        if status == "active" and key["status"] != "active":
            continue
        elif status == "revoked" and key["status"] != "revoked":
            continue
            
        filtered_keys.append(key)

    # Apply sorting
    if sort_by == "name":
        filtered_keys.sort(key=lambda k: (k["name"] or "").lower())
    elif sort_by == "last_used_at":
        # Sort so most recently used are first; None goes to the end
        filtered_keys.sort(key=lambda k: k.get("last_used_at") or "", reverse=True)
    elif sort_by == "expires_at":
        # Sort so expiring soonest are first; None goes to the end
        filtered_keys.sort(key=lambda k: k.get("expires_at") or "9999-12-31")
    else:  # default or "created_at"
        filtered_keys.sort(key=lambda k: k.get("created_at") or "", reverse=True)

    # Pagination
    total = len(filtered_keys)
    page_str = request.query_params.get("page")
    per_page_str = request.query_params.get("per_page")
    
    if page_str or per_page_str:
        try:
            page = max(1, int(page_str)) if page_str else 1
        except ValueError:
            page = 1
            
        try:
            per_page = max(1, int(per_page_str)) if per_page_str else 10
        except ValueError:
            per_page = 10
            
        start = (page - 1) * per_page
        end = start + per_page
        paginated_keys = filtered_keys[start:end]
        pages = max(1, (total + per_page - 1) // per_page)
        
        return JSONResponse({
            "keys": paginated_keys,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages
        })
    
    return JSONResponse({"keys": filtered_keys})


@http_route_registry.route(
    route="auth",
    endpoint="scope-vocabulary",
    methods=["GET"],
    name="api_get_scope_vocabulary",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:apikey:read",),
    owner="api.apikeys",
)
async def api_get_scope_vocabulary(request: Request) -> Response:
    """Get valid global OAuth scopes.

    Returns a JSON containing the list of valid global scopes configured,
    plugin-contributed scopes, level-1 core scopes, and level metadata.
    """
    from core.scope_management.registration import get_permission_registry
    from core.scope_management.grammar import parse_scope, ScopeKind

    all_perms = get_permission_registry().all_permissions()
    core_scopes = []
    plugin_scopes = []

    for entry in all_perms:
        token = entry.get("token", "")
        parsed = parse_scope(token)
        if parsed.kind == ScopeKind.CORE or entry.get("plugin_id") == "core":
            core_scopes.append({
                "token": token,
                "description": entry.get("description", ""),
                "level": 1,
                "kind": parsed.kind.value,
                "domain": parsed.id_or_domain,
                "access": parsed.access or entry.get("access", "read"),
                "id_or_domain": parsed.id_or_domain,
            })
        else:
            plugin_scopes.append({
                **entry,
                "level": 2,
                "kind": parsed.kind.value,
                "id_or_domain": parsed.id_or_domain,
                "access": parsed.access or entry.get("access"),
            })

    return JSONResponse({
        "global_scopes": OAUTH_VALID_SCOPES,
        "plugin_scopes": plugin_scopes,
        "core_scopes": core_scopes,
        "levels": {"1": "core platform", "2": "plugin", "3": "plugin gate"},
    })


@http_route_registry.route(
    route="auth",
    endpoint="api-keys",
    methods=["POST"],
    name="api_create_api_key",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:apikey:write",),
    owner="api.apikeys",
)
async def api_create_api_key(request: Request) -> Response:
    """Create a new API key for the admin subject."""
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("api_key_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    name = body.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        return JSONResponse({"error": "name_required"}, status_code=400)

    expires_in = body.get("expires_in")
    expires_at = None
    if expires_in is not None:
        try:
            expires_in_int = int(expires_in)
            if expires_in_int < 0:
                return JSONResponse({"error": "invalid_expires_in"}, status_code=400)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_int)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_expires_in"}, status_code=400)

    scopes = body.get("scopes")
    if scopes is None:
        scopes = []
    elif not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        return JSONResponse({"error": "invalid_scopes"}, status_code=400)

    # Validate scopes do not exceed issuer's scopes
    validation_err = await _validate_scopes_against_issuer(request, scopes)
    if validation_err:
        return validation_err

    subject = await _resolve_subject(request)
    tenant_id = await _resolve_tenant_id(request)
    if tenant_id is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    res = await create_api_key(
        subject, name.strip(), expires_at, scopes=scopes, tenant_id=tenant_id
    )
    serialized_res = {
        "key_id": res["key_id"],
        "token": res["token"],
        "prefix": res["prefix"],
        "name": res["name"],
        "expires_at": res["expires_at"].isoformat() if res["expires_at"] else None,
        "scopes": res.get("scopes"),
    }
    return JSONResponse(serialized_res)


@http_route_registry.route(
    route="auth",
    endpoint="api-keys/{key_id}/revoke",
    methods=["POST"],
    name="api_revoke_api_key",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:apikey:write",),
    owner="api.apikeys",
)
async def api_revoke_api_key(request: Request) -> Response:
    """Revoke an API key by ID and return a fresh replacement key (token shown once).

    The replacement uses the same name as the revoked key and is returned using the
    same shape as a normal create response so the UI can show the one-time secret.
    """
    key_id = request.path_params.get("key_id")
    if not key_id:
        return JSONResponse({"error": "key_id_required"}, status_code=400)

    subject = await _resolve_subject(request)
    tenant_id = await _resolve_tenant_id(request)
    if tenant_id is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    new_key = await rotate_api_key(subject, key_id, tenant_id=tenant_id)
    if not new_key:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(new_key)


@http_route_registry.route(
    route="auth",
    endpoint="api-keys/{key_id}",
    methods=["DELETE"],
    name="api_delete_api_key",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:apikey:write",),
    owner="api.apikeys",
)
async def api_delete_api_key(request: Request) -> Response:
    """Delete/remove an API key by ID."""
    key_id = request.path_params.get("key_id")
    if not key_id:
        return JSONResponse({"error": "key_id_required"}, status_code=400)

    subject = await _resolve_subject(request)
    tenant_id = await _resolve_tenant_id(request)
    if tenant_id is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    success = await delete_api_key(subject, key_id, tenant_id=tenant_id)
    if not success:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True})


@http_route_registry.route(
    route="auth",
    endpoint="api-keys/{key_id}/scopes",
    methods=["PUT"],
    name="api_update_api_key_scopes",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:apikey:write",),
    owner="api.apikeys",
)
async def api_update_api_key_scopes(request: Request) -> Response:
    """Update scopes for an API key by ID."""
    key_id = request.path_params.get("key_id")
    if not key_id:
        return JSONResponse({"error": "key_id_required"}, status_code=400)

    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("api_key_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    if "scopes" not in body:
        return JSONResponse({"error": "scopes_required", "message": "The 'scopes' field is required"}, status_code=400)

    scopes = body.get("scopes")
    if scopes is not None:
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            return JSONResponse({"error": "invalid_scopes"}, status_code=400)

    # Validate scopes do not exceed issuer's scopes
    validation_err = await _validate_scopes_against_issuer(request, scopes or [])
    if validation_err:
        return validation_err

    subject = await _resolve_subject(request)
    tenant_id = await _resolve_tenant_id(request)
    if tenant_id is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    success = await update_api_key_scopes(subject, key_id, scopes, tenant_id=tenant_id)
    if not success:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True})


@http_route_registry.route(
    route="auth",
    endpoint="api-key-presets",
    methods=["GET"],
    name="api_list_scope_presets",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:apikey:read",),
    owner="api.apikeys",
)
async def api_list_scope_presets(request: Request) -> Response:
    """List saved API key scope presets."""
    subject = await _resolve_subject(request)
    presets = await list_scope_presets(subject)
    serialized_presets = []
    for preset in presets:
        serialized_presets.append({
            "id": preset["id"],
            "name": preset["name"],
            "scopes": preset["scopes"],
            "created_at": preset["created_at"].isoformat() if preset["created_at"] else None,
        })
    return JSONResponse({"presets": serialized_presets})


@http_route_registry.route(
    route="auth",
    endpoint="api-key-presets",
    methods=["POST"],
    name="api_create_scope_preset",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:apikey:write",),
    owner="api.apikeys",
)
async def api_create_scope_preset(request: Request) -> Response:
    """Create a new saved API key scope preset."""
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("api_key_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    name = body.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        return JSONResponse({"error": "name_required"}, status_code=400)

    scopes = body.get("scopes")
    if scopes is not None:
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            return JSONResponse({"error": "invalid_scopes"}, status_code=400)

    # Validate scopes do not exceed issuer's scopes
    validation_err = await _validate_scopes_against_issuer(request, scopes or [])
    if validation_err:
        return validation_err

    subject = await _resolve_subject(request)
    res = await create_scope_preset(subject, name.strip(), scopes)
    serialized_res = {
        "id": res["id"],
        "name": res["name"],
        "scopes": res["scopes"],
        "created_at": res["created_at"].isoformat() if res["created_at"] else None,
    }
    return JSONResponse(serialized_res)


@http_route_registry.route(
    route="auth",
    endpoint="api-key-presets/{preset_id}",
    methods=["DELETE"],
    name="api_delete_scope_preset",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:apikey:write",),
    owner="api.apikeys",
)
async def api_delete_scope_preset(request: Request) -> Response:
    """Delete a saved API key scope preset by ID."""
    preset_id = request.path_params.get("preset_id")
    if not preset_id:
        return JSONResponse({"error": "preset_id_required"}, status_code=400)

    subject = await _resolve_subject(request)
    success = await delete_scope_preset(subject, preset_id)
    if not success:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True})

