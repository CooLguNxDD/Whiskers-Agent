#!/usr/bin/env python3
"""
Whiskers Agent MCP Server
Entrypoint – tools are organised as modules inside the MCPTools package.
"""

import logging
import socket
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables before importing MCPTools (which reads os.environ)
load_dotenv()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("whiskers_agent")

# ---------------------------------------------------------------------------
# Import core configuration and FastMCP instance
# ---------------------------------------------------------------------------
from core.context import (  # noqa: E402
    mcp, OAUTH_ENABLED, MCP_SERVER_URL, oauth_relay, _DB_AVAILABLE, vault,
)

# Plugin Registration
from core.plugin_loader.plugin_registry import PluginRegistry, _set_registry, Tier
from core.plugin_loader.plugin_loader import discover_and_load_plugins

import core_graph.mcp_tool  # noqa: F401  — registers run_graph as a core tool
import core_graph.goap_agent.mcp_tools  # noqa: F401  — GoapAgent_* node/CLI MCP tools (no HTTP routes)
# PortfolioAgent_run lives on portfolio_plugin (loaded via plugin module_paths).
import core.artifact_store.tools  # noqa: F401  — list/get/fetch_artifact MCP tools

# initialize the PluginRegistry — vault and relay are shared singletons from core.context
registry = PluginRegistry(mcp, vault=vault, relay=oauth_relay)
_set_registry(registry)

# Process manifest tracking and module loading decoupling
# Read the plugins to load from the config file and load them.
# discover_and_load_plugins(registry)  # defaults to config/plugin_config.json

import api.plugin_routes  # noqa: F401  — register /api/* routes with FastMCP
import api.admin_routes  # noqa: F401  — register /admin/* session routes with FastMCP
import api.api_key_routes  # noqa: F401  — register /api/auth/* + /admin/login-api-key
import api.route_routes   # noqa: F401  — register /api/routes/* endpoints
import api.tool_routes    # noqa: F401  — register /api/plugins/{id}/tools/* toggle endpoints
import api.config_routes  # noqa: F401  — register /api/config/* endpoints
import api.proxy_routes   # noqa: F401  — register /api/proxies/* control plane routes
import api.playground_routes  # noqa: F401  — register /api/playground/* endpoints
import api.analytics_routes   # noqa: F401  — register /api/analytics/* endpoints
import api.health_routes      # noqa: F401  — register /api/health system status
import api.log_routes         # noqa: F401  — register /api/logs/* SSE stream endpoints
import api.relay_routes       # noqa: F401  — register /api/relay/* endpoints
import api.catalog_routes     # noqa: F401  — register /api/catalog/* live contract catalog
import core.artifact_store.routes  # noqa: F401  — register /api/artifacts/session_gated/*
import oauth.oauth_routes  # noqa: F401  — register /oauth/connect, /jwks, /complete-layer1


@asynccontextmanager
async def _server_lifespan(_) -> AsyncIterator[None]:
    """Plugin lifecycle + embedding worker, tied to the actual server event loop.

    FastMCP calls this inside the event loop that handles connections, so any
    asyncio.create_task() here runs on the correct loop and survives for the
    server's lifetime.
    """
    from core.bootstrap import BootContext, run_pipeline, run_teardown
    # Inject the banner coroutine directly so the pipeline never re-imports this
    # entry module (which would re-run _set_registry with a fresh empty registry).
    ctx = BootContext(
        registry=registry, mcp=mcp, db_available=_DB_AVAILABLE, log_startup=_log_startup
    )
    await run_pipeline(ctx)
    yield
    await run_teardown(ctx)


# Attach our lifespan before the server loop starts.
# _lifespan_proxy in FastMCP closes over `mcp`, so patching _lifespan here
# is picked up at runtime by both stdio and HTTP transports.
mcp._lifespan = _server_lifespan


# ---------------------------------------------------------------------------
# Entrypoint helpers
# ---------------------------------------------------------------------------

def _get_local_ip() -> str:
    """Get the local IP address."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "localhost"


async def _log_startup() -> None:
    """Log server startup information with focus on plugins."""
    local_ip = _get_local_ip()
    logger.info("=" * 70)
    logger.info("Whiskers Agent MCP Server Starting (Plugin-Based Architecture)")
    logger.info("=" * 70)
    logger.info(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Local IP: {local_ip}")
    logger.info(f"Hostname: {socket.gethostname()}")
    logger.info("-" * 70)
    logger.info("Configuration:")
    logger.info(f"  OAuth Enabled: {OAUTH_ENABLED}")
    core_api_url = (
        os.environ.get("WHISKERS_API_URL")
        or os.environ.get("WHISKERS_API_URL")
        or os.environ.get("API_URL")
    )
    if core_api_url:
        logger.info(f"  Core API URL: {core_api_url}")
    if OAUTH_ENABLED:
        logger.info(f"  MCP Server URL: {MCP_SERVER_URL}")
        oauth_redirect_uri = (
            os.environ.get("OAUTH_REDIRECT_URI")
            or f"{MCP_SERVER_URL}/oauth/plugin/whiskers_agent/callback"
        )
        logger.info(f"  OAuth Redirect URI: {oauth_redirect_uri}")
    logger.info("-" * 70)
    
    # Log loaded plugins and their types
    logger.info("Plugin System Status:")
    logger.info(f"  System Tier: {getattr(registry.system_tier, 'name', str(registry.system_tier))}")
    
    # Introspect registry for loaded plugins
    if hasattr(registry.lifecycle, "_plugins") and registry.lifecycle._plugins:
        logger.info(f"  Loaded Plugins ({len(registry.lifecycle._plugins)}):")
        for p in registry.lifecycle._plugins:
            t = getattr(p, 'tier', None)
            try:
                tier_label = Tier(t).name.upper()
            except (ValueError, KeyError):
                tier_label = str(t).upper()
            tier_str = f" [{tier_label}]" if t is not None else ""
            ver_str = f" v{p.version}" if hasattr(p, 'version') else ""
            logger.info(f"    - {p.name}{ver_str}{tier_str}")
    else:
        # Fallback if discovery happened via manifest logging in discover_and_load_plugins
        logger.info("  Plugins discovered and tools registered via dynamic discovery.")
    
    logger.info("-" * 70)
    
    # List registered tools from the FastMCP instance
    try:
        import inspect as _inspect
        _result = mcp.list_tools()
        tools = await _result if _inspect.iscoroutine(_result) else _result
        if tools:
            logger.info(f"Available Tools ({len(tools)}):")
            for tool in sorted(tools, key=lambda t: t.name):
                logger.info(f"  * {tool.name}")
        else:
            logger.info("No tools registered.")
    except Exception:
        logger.warning("Tool enumeration failed", exc_info=True)
        
    logger.info("=" * 70)
    logger.info("Server ready to accept connections")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Whiskers Agent MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport mode: 'stdio' (default, for VS Code subprocess) or 'http' (standalone HTTP server)",
    )
    default_port = int(os.environ.get("MCP_PORT", "10000"))
    parser.add_argument("--port", type=int, default=default_port, help=f"HTTP port (default: {default_port})")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (default: 127.0.0.1)")
    args = parser.parse_args()

    # Run the MCP server with the selected transport
    if args.transport == "http":
        import uvicorn

        os.environ["WHISKERS_TRANSPORT"] = "http"
        os.environ["WHISKERS_TRANSPORT"] = "http"
        os.environ["FASTMCP_STATELESS"] = "true"

        from urllib.parse import urlsplit

        def _hostport(url: str) -> str | None:
            """Safely extracts the 'host:port' (netloc) from a URL string."""
            u = (url or "").strip()
            if not u:
                return None
            if "://" not in u:
                u = f"//{u}"
            return urlsplit(u).netloc or None

        _allowed_hosts = {
            h
            for h in (
                _hostport(os.environ.get("MCP_SERVER_URL", "")),
                _hostport(os.environ.get("FRONTEND_URL", "")),
            )
            if h
        }
        _allowed_hosts.update(
            h.strip()
            for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",")
            if h.strip()
        )

        app = mcp.http_app(path="/mcp", allowed_hosts=sorted(_allowed_hosts) or None)

        from core.context import http_route_registry
        http_route_registry.bind_app(app)

        from core.telemetry.otel_setup import init_telemetry, instrument_app
        init_telemetry()
        instrument_app(app)

        if OAUTH_ENABLED:
            from api.middleware import (
                AutoRegisterMiddleware,
                PublicPathMiddleware,
                RegisterScopeSanitizerMiddleware,
                SessionGateMiddleware,
            )

            # 1. Public-path bypass — must wrap BEFORE auto-register + FastMCP auth
            app = PublicPathMiddleware(app)

            # 2. Session gate — requires admin cookie on frontend/OAuth paths
            app = SessionGateMiddleware(app)

            # 3. Auto-register MCP clients that skip /register (e.g. Claude Desktop)
            app = AutoRegisterMiddleware(app)

            # 4. DCR scope leniency — drop out-of-set scopes so /register 201s
            app = RegisterScopeSanitizerMiddleware(app)

        from utils.server_config import (
            RATE_LIMIT_ENABLED,
            RATE_LIMIT_REQUESTS,
            RATE_LIMIT_WINDOW_SECONDS,
            RATE_LIMIT_PATH_PREFIXES,
            RATE_LIMIT_TRUST_PROXY,
            RATE_LIMIT_MAX_IPS,
        )
        if RATE_LIMIT_ENABLED:
            from api.rate_limit_middleware import RateLimitMiddleware
            app = RateLimitMiddleware(
                app,
                enabled=RATE_LIMIT_ENABLED,
                max_requests=RATE_LIMIT_REQUESTS,
                window_seconds=RATE_LIMIT_WINDOW_SECONDS,
                path_prefixes=RATE_LIMIT_PATH_PREFIXES,
                trust_proxy=RATE_LIMIT_TRUST_PROXY,
                max_tracked_ips=RATE_LIMIT_MAX_IPS,
            )
            logger.info(
                "Rate limiting enabled: %d requests per %d seconds for prefixes: %s",
                RATE_LIMIT_REQUESTS,
                RATE_LIMIT_WINDOW_SECONDS,
                ", ".join(RATE_LIMIT_PATH_PREFIXES),
            )

        from starlette.middleware.cors import CORSMiddleware
        from utils.server_config import resolve_cors_policy

        _cors = resolve_cors_policy()
        if _cors.warning:
            logger.error("CORS misconfigured: %s", _cors.warning)
        if _cors.relaxed:
            app = CORSMiddleware(
                app,
                allow_origins=_cors.origins or [],
                allow_origin_regex=r".*",
                allow_credentials=True,
                allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                expose_headers=["mcp-session-id", "mcp-protocol-version"],
            )
            logger.warning(
                "CORS RELAXED (dev, MCP_CORS_RELAXED opt-in): reflecting ANY Origin via "
                "allow_origin_regex=.* with allow_credentials=True — any site a browser "
                "visits can make credentialed calls to this server."
            )
        else:
            app = CORSMiddleware(
                app,
                allow_origins=_cors.origins,
                allow_credentials=True,
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                expose_headers=["mcp-session-id", "mcp-protocol-version"],
            )
            logger.info("CORS enabled for MCP origins: %s", ", ".join(_cors.origins))

        from api.middleware import UnhandledExceptionShieldMiddleware
        app = UnhandledExceptionShieldMiddleware(app)

        logger.info(f"Starting HTTP server on {args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        os.environ["WHISKERS_TRANSPORT"] = "stdio"
        os.environ["WHISKERS_TRANSPORT"] = "stdio"
        mcp.run(transport="stdio")
