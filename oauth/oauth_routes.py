"""
OAuth routes for the Whiskers Agent server.

Layer 1 (inbound) routes
------------------------
- ``GET /.well-known/jwks.json``             — JWKS public keys for JWT verification
- ``GET /oauth/plugin/{provider}/authorize`` — start the external OAuth relay flow
- ``GET /oauth/plugin/{provider}/callback``  — receive the provider callback and
                                               complete both Layer 2 (relay) and
                                               Layer 1 (MCP auth code) flows

Middleware — ``PublicPathMiddleware``, ``SessionGateMiddleware``, and
``AutoRegisterMiddleware`` live in ``api/middleware.py`` and are wired up in
``whiskers_agent_mcp.py``.
"""

import logging
import os

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from core.context import (
    FRONTEND_URL,
    MCP_SERVER_URL,
    mcp,
    oauth_provider,
    oauth_relay,
)

from urllib.parse import quote, urlencode

logger = logging.getLogger("whiskers")

# ---------------------------------------------------------------------------
# JWKS endpoint
# ---------------------------------------------------------------------------

@http_route_registry.route("/.well-known/jwks.json", methods=["GET"], name="jwks", auth_policy=AuthPolicy.PUBLIC, owner="oauth")
async def jwks_endpoint(request: Request) -> Response:
    """Return the JWKS for RS256 JWT verification."""
    if oauth_provider is None or not hasattr(oauth_provider, "_svc"):
        return JSONResponse({"keys": []}, status_code=200)
    jwks = await oauth_provider._svc.get_jwks()
    return JSONResponse(jwks)


# ---------------------------------------------------------------------------
# OAuth connect page — intermediate landing before relay redirect
# ---------------------------------------------------------------------------



@http_route_registry.route(
    "/oauth/connect/{auth_state}",
    methods=["GET"],
    name="oauth_connect",
    auth_policy=AuthPolicy.PUBLIC,
    owner="oauth",
)
async def oauth_connect(request: Request) -> Response:
    """Session-aware landing page for Layer 1 auth -> layer 2 external OAuth.

    1. If the request carries a valid admin session cookie AND a Layer 2 token
       is already in the Vault → auto-complete Layer 1 (no UI shown).
    2. If session valid but no L2 token → redirect to /?state=… so the
       user can trigger Layer 2 themselves.
    3. No valid session → redirect to FRONTEND_URL/login?state=… so the user
       can log in first.
    """
    from core.context import vault as _vault

    auth_state = request.path_params.get("auth_state", "")
    if not auth_state:
        return HTMLResponse("<h1>Error</h1><p>Missing auth state.</p>", status_code=400)

    default_auth_plugin = os.environ.get("DEFAULT_AUTH_PLUGIN_ID", "whiskers_core")

    session_cookie = request.cookies.get("session")
    if session_cookie and oauth_provider is not None:
        svc = getattr(oauth_provider, "_svc", None)
        if svc is not None:
            op = "svc.validate_token"
            try:
                payload = await svc.validate_token(session_cookie)
                role = payload.get("whiskers_role") or payload.get("ocat_role")
                user_id = payload.get("sub") or payload.get("whiskers_user_id") or payload.get("ocat_user_id")
                tenant_id = payload.get("whiskers_tenant") if payload.get("whiskers_tenant") is not None else payload.get("ocat_tenant")
                if role and hasattr(oauth_provider, "attach_session_role_to_pending_async"):
                    await oauth_provider.attach_session_role_to_pending_async(
                        auth_state, role, user_id, tenant_id
                    )
                elif role and hasattr(oauth_provider, "attach_session_role_to_pending"):
                    oauth_provider.attach_session_role_to_pending(auth_state, role, user_id, tenant_id)
                # Session is valid — check for a stored Layer 2 token
                l2_token = None
                if _vault is not None:
                    op = "_vault.get"
                    l2_token = await _vault.get(default_auth_plugin, "whiskers_access_token")
                if l2_token:
                    # Auto-complete Layer 1 — MCP client gets its auth code
                    return RedirectResponse(
                        url=f"/oauth/complete-layer1/{quote(auth_state, safe='')}",
                        status_code=302,
                    )
                # Session valid, no L2 token → send to dashboard with state preserved
                if FRONTEND_URL:
                    return RedirectResponse(
                        url=f"{FRONTEND_URL}/?state={quote(auth_state, safe='')}",
                        status_code=302,
                    )
            except Exception as e:
                logger.exception(
                    "OAuth connect check failed during %s (auth_state: %s, vault_present: %s): %s",
                    op,
                    auth_state,
                    _vault is not None,
                    e,
                )
                pass  # Invalid or expired session — fall through to login redirect

    # No valid session → redirect to login page with state preserved
    if FRONTEND_URL:
        return RedirectResponse(
            url=f"{FRONTEND_URL}/login?state={quote(auth_state, safe='')}",
            status_code=302,
        )
    # Fallback when FRONTEND_URL is not configured — skip landing page entirely
    return RedirectResponse(
        url=f"/oauth/plugin/{default_auth_plugin}/authorize?plugin_id={default_auth_plugin}&state={quote(auth_state, safe='')}",
        status_code=302,
    )


# ---------------------------------------------------------------------------
# Layer 1 direct completion — skip Layer 2 external OAuth
# ---------------------------------------------------------------------------

@http_route_registry.route(
    "/oauth/complete-layer1/{auth_state}",
    methods=["GET"],
    name="oauth_complete_layer1",
    auth_policy=AuthPolicy.PUBLIC,
    owner="oauth",
)
async def oauth_complete_layer1(request: Request) -> Response:
    """Complete the Layer 1 MCP auth-code flow directly, without Layer 2 external OAuth.

    Use this when credentials are already available server-side (e.g. username/password
    env vars) and the MCP client just needs its authorization code redirect.
    """
    auth_state = request.path_params.get("auth_state", "")
    if not auth_state:
        return HTMLResponse("<h1>Error</h1><p>Missing auth state.</p>", status_code=400)

    if oauth_provider is None:
        return HTMLResponse(
            "<h1>Error</h1><p>OAuthProvider not configured.</p>",
            status_code=500,
        )

    if not hasattr(oauth_provider, "_complete_authorization"):
        return HTMLResponse(
            "<h1>Error</h1><p>OAuthProvider does not support direct completion.</p>",
            status_code=500,
        )

    # Role-aware elevation: stamp pending + extra_scopes from session role
    # (admin/master still resolve to full+admin via playground_mcp_scopes).
    extra_scopes: list[str] = []
    session_cookie = request.cookies.get("session")
    svc = getattr(oauth_provider, "_svc", None)
    if session_cookie and svc is not None:
        try:
            payload = await svc.validate_token(session_cookie)
            role = payload.get("whiskers_role") or payload.get("ocat_role")
            user_id = payload.get("sub") or payload.get("whiskers_user_id") or payload.get("ocat_user_id")
            tenant_id = payload.get("whiskers_tenant") if payload.get("whiskers_tenant") is not None else payload.get("ocat_tenant")
            if role:
                from core.api_key_management.scopes import playground_mcp_scopes
                extra_scopes = list(playground_mcp_scopes(role))
                # Also stamp pending so union path has role even without cookie later
                if hasattr(oauth_provider, "attach_session_role_to_pending"):
                    oauth_provider.attach_session_role_to_pending(
                        auth_state, role, user_id, tenant_id
                    )
        except Exception:
            # invalid/expired session → no elevation
            logger.debug("oauth_routes: session cookie elevation skipped", exc_info=True)

    try:
        redirect_url = await oauth_provider._complete_authorization(
            auth_state=auth_state,
            provider="whiskers_core",
            extra_scopes=extra_scopes,
        )
        logger.info(
            "oauth_complete_layer1: Layer 1 completed for state=%s (extra_scopes=%d)",
            auth_state,
            len(extra_scopes),
        )
        return RedirectResponse(url=redirect_url, status_code=303)
    except Exception as exc:
        logger.error("oauth_complete_layer1 error: %s", exc)
        return HTMLResponse(f"<h1>Error</h1><p>{exc}</p>", status_code=500)


# ---------------------------------------------------------------------------
# Layer 2 relay — authorize entry point
# ---------------------------------------------------------------------------

@http_route_registry.route(
    "/oauth/plugin/{provider}/authorize",
    methods=["GET"],
    name="oauth_relay_authorize",
    auth_policy=AuthPolicy.PUBLIC,
    owner="oauth",
)
async def oauth_relay_authorize(request: Request) -> Response:
    """Start the external OAuth PKCE flow for a plugin/provider pair.

    Query params:
      - ``plugin_id``    — which plugin is requesting the token
      - ``state``        — MCP pending auth state (from OAuthService_FastMCPProvider.authorize)
    """
    provider = request.path_params.get("provider", "")
    plugin_id = request.query_params.get("plugin_id", provider)
    auth_state = request.query_params.get("state", "")

    if not provider:
        return HTMLResponse("<h1>Error</h1><p>Missing provider.</p>", status_code=400)

    if oauth_relay is None:
        return HTMLResponse(
            "<h1>Error</h1><p>ExternalOAuthRelay is not configured (DATABASE_URL missing).</p>",
            status_code=500,
        )

    try:
        callback_url = f"{MCP_SERVER_URL}/oauth/plugin/{provider}/callback"
        authorize_url = await oauth_relay.build_authorize_url(
            plugin_id=plugin_id,
            provider=provider,
            redirect_uri=callback_url,
            extra_context={"mcp_auth_state": auth_state},
        )
        return RedirectResponse(url=authorize_url, status_code=302)
    except Exception as exc:
        logger.error("oauth_relay_authorize error: %s", exc)
        return HTMLResponse(f"<h1>Error</h1><p>{exc}</p>", status_code=500)


# ---------------------------------------------------------------------------
# Layer 2 relay — callback
# ---------------------------------------------------------------------------

@http_route_registry.route(
    "/oauth/plugin/{provider}/callback",
    methods=["GET"],
    name="oauth_relay_callback",
    auth_policy=AuthPolicy.PUBLIC,
    owner="oauth",
)
async def oauth_relay_callback(request: Request) -> Response:
    """Receive the provider's authorization code, exchange it, and redirect the MCP client.

    This route handles both:
    - Layer 2: stores encrypted tokens in ``plugin_oauth_tokens``
    - Layer 1: completes the MCP auth-code flow so the MCP client gets its JWT
    """
    provider = request.path_params.get("provider", "")
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    error = request.query_params.get("error", "")

    if error:
        logger.warning("OAuth callback received error from provider %s: %s", provider, error)
        return HTMLResponse(
            f"<h1>Login Failed</h1><p>{error}</p>",
            status_code=400,
        )

    if not code or not state:
        return HTMLResponse(
            "<h1>Error</h1><p>Missing authorization code or state parameter.</p>",
            status_code=400,
        )

    if oauth_relay is None:
        return HTMLResponse(
            "<h1>Error</h1><p>ExternalOAuthRelay is not configured.</p>",
            status_code=500,
        )

    try:
        # Layer 2: exchange code at provider and atomically consume the PKCE state.
        # handle_callback returns plugin_id, provider, and context from the deleted row —
        # no separate pre-query needed.
        result = await oauth_relay.handle_callback(code=code, state=state)
        plugin_id = result["plugin_id"]
        context = result.get("context") or {}

        logger.info(
            "oauth_relay_callback: Layer 2 tokens stored for plugin=%s provider=%s",
            plugin_id, provider,
        )

        # Layer 1: complete the MCP auth-code flow (role-aware union)
        mcp_auth_state = context.get("mcp_auth_state", "")
        if oauth_provider is not None and mcp_auth_state and hasattr(
            oauth_provider, "_complete_authorization"
        ):
            extra_scopes: list[str] = []
            session_cookie = request.cookies.get("session")
            svc = getattr(oauth_provider, "_svc", None)
            if session_cookie and svc is not None:
                try:
                    payload = await svc.validate_token(session_cookie)
                    role = payload.get("whiskers_role") or payload.get("ocat_role")
                    user_id = payload.get("sub") or payload.get("whiskers_user_id") or payload.get("ocat_user_id")
                    tenant_id = payload.get("whiskers_tenant") if payload.get("whiskers_tenant") is not None else payload.get("ocat_tenant")
                    if role:
                        from core.api_key_management.scopes import playground_mcp_scopes
                        extra_scopes = list(playground_mcp_scopes(role))
                        if hasattr(oauth_provider, "attach_session_role_to_pending"):
                            oauth_provider.attach_session_role_to_pending(
                                mcp_auth_state, role, user_id, tenant_id
                            )
                except Exception:
                    logger.debug("oauth_routes.py: swallowed exception", exc_info=True)
            redirect_url = await oauth_provider._complete_authorization(
                auth_state=mcp_auth_state,
                provider=provider,
                extra_scopes=extra_scopes,
            )
            logger.info("OAuth callback successful – redirecting MCP client")
            return RedirectResponse(url=redirect_url, status_code=303)

        # No MCP auth state — standalone Layer 2 token acquisition; redirect to frontend
        if FRONTEND_URL:
            if mcp_auth_state:
                # Preserve state so the Finish button on /connect still works
                params = urlencode({"oauth_success": plugin_id, "state": mcp_auth_state})
                return RedirectResponse(
                    url=f"{FRONTEND_URL}/?{params}",
                    status_code=303,
                )
            return RedirectResponse(
                url=f"{FRONTEND_URL}/?{urlencode({'oauth_success': plugin_id})}",
                status_code=303,
            )
        return HTMLResponse(
            "<h1>Connected</h1><p>External OAuth token stored successfully. "
            "You may close this window.</p>",
            status_code=200,
        )

    except Exception as exc:
        logger.error("oauth_relay_callback error: %s", exc)
        return HTMLResponse(f"<h1>Error</h1><p>{exc}</p>", status_code=500)
