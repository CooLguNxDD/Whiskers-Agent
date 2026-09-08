"""FastMCP app instance + Layer-1/Layer-2 auth singletons (vault, oauth_provider, oauth_relay).
Split out of core/context.py (Phase 1 modularity refactor). Construction order preserved exactly."""
import logging

from fastmcp import FastMCP

from core.context._env import OAUTH_ENABLED, MCP_SERVER_URL, _DB_AVAILABLE
from core.context_builder import ContextBuilder
from utils.server_config import CONTEXT_CONFIG, PARAMETERS

logger = logging.getLogger("whiskers")

oauth_provider = None
oauth_relay = None
vault = None  # Single shared VaultService instance (initialised below when _DB_AVAILABLE)
_oauth_svc = None  # Layer-1 OAuthService instance backing oauth_provider (None unless OAUTH_ENABLED + _DB_AVAILABLE)

# Create a single VaultService instance shared by oauth_relay + PluginRegistry
if _DB_AVAILABLE:
    from db_layer.vault import VaultService
    vault = VaultService()

mcp_instructions = CONTEXT_CONFIG.get(
    "mcp_instructions_base",
    "You are an internal tool executor. \n\n"
    "PARALLELISM RULE: When a request contains multiple INDEPENDENT, DISTINCT goals "
    "(goals that don't depend on each other's output), emit ALL tool calls in a single response. "
    "Do not wait for one to complete before calling the next.\n\n"
    "IMPORTANT SCOPE OF PARALLELISM RULE: A single operation repeated over a list "
    "(create N of X, do the same thing for each item in a set) is NOT multiple independent goals "
    "— it is ONE run_graph call, never N individual tool calls. Let the server fan out.\n\n"
    "Independent = no output from task A is needed as input to task B, AND the tasks are genuinely distinct goals.\n"
    "Sequential = task B needs task A's result first.\n\n"
    "NEVER fabricate required field values.\n\n"
    "Whiskers Agent MCP platform — orchestrate plugin tools, proxy integrations, and multi-step workflows via run_graph.\n\n"
    "OFFLOAD DIRECTIVE: For any multi-step, batch, or repeated request, call run_graph ONCE with the whole goal "
    "as user_message. run_graph does GOAP planning, fan-out, and replanning server-side. "
    "Do NOT loop the underlying plugin tools yourself.\n\n"
    "Example (CORRECT): 'create 5 Jules sessions named A B C D E' "
    "\u2192 one run_graph(user_message='create 5 Jules sessions named A B C D E')\n"
    "Example (WRONG): five separate julescreate_session calls.\n\n"
    "Note: On some connections raw plugin and proxy tools may also be directly visible alongside run_graph. "
    "Even so, multi-step, batch, or repeated work must go through run_graph so the server's GOAP planner owns "
    "the plan and fan-out. Direct single-tool calls are acceptable only for a genuine one-shot single action "
    "(e.g. a single read/lookup with no follow-on steps).\n\n"
    "Use discover_tools (optionally with a query) to preview the live catalog of actions run_graph can orchestrate. "
    "discover_tools is optional and never a required precursor to run_graph.\n\n"
    "Keep platform rules: never fabricate entity data; require missing fields via need_input; "
    "bulk actions need confirmation."
)

if PARAMETERS:
    param_str = ", ".join([f"{k}: {v}" for k, v in PARAMETERS.items()])
    mcp_instructions += f" (Parameters: {param_str})"

logger.info(f"MCP instructions loaded, {mcp_instructions}")

mcp_context_builder = ContextBuilder(mcp_instructions)

# Core-owned response-shaping hints — unconditional so the FastMCP instructions
# always teach `_response_shape` usage, even with zero plugins loaded. Previously
# this text lived only in plugin config.json shape_hint/format_hint blocks
# (core/config_loader.py), so quarantining/unloading a plugin silently dropped it
# even though the shaping pipeline (utils/response_shape.py) kept running regardless.
from utils.response_shape_hints import get_response_hints as _get_core_response_hints
_core_shape_hint, _core_format_hint = _get_core_response_hints()
mcp_context_builder.add_context(_core_shape_hint)
mcp_context_builder.add_context(_core_format_hint)

if OAUTH_ENABLED:
    if _DB_AVAILABLE:
        from oauth.oauth_relay import ExternalOAuthRelay
        from oauth.oauth_service import OAuthService, OAuthService_FastMCPProvider

        _oauth_svc = OAuthService()
        from utils.server_config import OAUTH_VALID_SCOPES

        oauth_provider = OAuthService_FastMCPProvider(
            service=_oauth_svc,
            base_url=MCP_SERVER_URL,
            # Scopes loaded from server_config.json oauth.valid_scopes.
            # terminal:use / terminal:host gate the Cat Terminal Relay legs.
            # Contributed plugin scopes are enforced via get_valid_scopes at the gate/route level; static floor is acceptable at handshake time.
            valid_scopes=OAUTH_VALID_SCOPES,
        )

        oauth_relay = ExternalOAuthRelay(
            vault=vault,  # reuse the shared singleton from this module
            plugin_manifests={},
        )
    else:
        logger.warning(
            "DATABASE_URL not set — OAuth requires a database (ExternalOAuthRelay "
            "and the /oauth/plugin/* callback routes depend on it). "
            "Set DATABASE_URL and MASTER_KEY to enable OAuthService. "
            "OAuth is DISABLED for this session; falling back to env-var credentials."
        )
        # oauth_provider remains None; FastMCP below starts without auth.
        # Legacy provider-specific OAuth callbacks were removed in favour of
        # /oauth/plugin/{provider}/callback.

    if _DB_AVAILABLE:
        mcp_instructions += CONTEXT_CONFIG.get(
            "semantic_instructions_suffix",
            " Semantic search tools may be available per loaded plugin."
            " Prefer semantic search when offered; upsert indexed content after writes."
            " When semantic tools return no result, fall back to non-semantic tools so the user still gets a response.",
        )
    from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
    from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
    from fastmcp.server.middleware.logging import LoggingMiddleware
    from core.context.scope_middleware import ScopeEnforcementMiddleware
    from core.context.response_shape_middleware import ResponseShapeMiddleware
    from core.context.artifact_offload_middleware import ArtifactOffloadMiddleware

    mcp_middleware = [
        ErrorHandlingMiddleware(),
        ScopeEnforcementMiddleware(),
        # Shape (strip/project/limit/CSV) direct tool-call results before offload —
        # offload_minio already runs inside the shape pipeline, so ArtifactOffloadMiddleware
        # is a cheap idempotent no-op on anything ResponseShapeMiddleware touches.
        ResponseShapeMiddleware(),
        # Offload before size-limiting so an oversized-but-offloadable payload
        # gets a short_id marker instead of being destructively truncated.
        ArtifactOffloadMiddleware(),
        ResponseLimitingMiddleware(max_size=500_000),
        LoggingMiddleware(),
    ]

    mcp = FastMCP(
        "whiskers_agent",
        instructions=mcp_context_builder.get_instructions(),
        auth=oauth_provider,
        mask_error_details=True,
        middleware=mcp_middleware,
    )
    logger.info("OAuth authentication ENABLED")
else:
    from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
    from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
    from fastmcp.server.middleware.logging import LoggingMiddleware
    from core.context.scope_middleware import ScopeEnforcementMiddleware
    from core.context.response_shape_middleware import ResponseShapeMiddleware
    from core.context.artifact_offload_middleware import ArtifactOffloadMiddleware

    mcp_middleware = [
        ErrorHandlingMiddleware(),
        ScopeEnforcementMiddleware(),
        # Shape (strip/project/limit/CSV) direct tool-call results before offload —
        # offload_minio already runs inside the shape pipeline, so ArtifactOffloadMiddleware
        # is a cheap idempotent no-op on anything ResponseShapeMiddleware touches.
        ResponseShapeMiddleware(),
        # Offload before size-limiting so an oversized-but-offloadable payload
        # gets a short_id marker instead of being destructively truncated.
        ArtifactOffloadMiddleware(),
        ResponseLimitingMiddleware(max_size=500_000),
        LoggingMiddleware(),
    ]

    mcp = FastMCP(
        "whiskers_agent",
        instructions=mcp_context_builder.get_instructions(),
        mask_error_details=True,
        middleware=mcp_middleware,
    )
    logger.info("OAuth authentication DISABLED - using env-var credentials")

# Modern MCP subscriptions/listen support (2026-07-28 SEP-2575)
subscription_bus = None
listen_handler = None
try:
    from mcp.server.subscriptions import ListenHandler, InMemorySubscriptionBus
    from mcp_types import SubscriptionsListenRequestParams

    subscription_bus = InMemorySubscriptionBus()
    listen_handler = ListenHandler(subscription_bus)
    mcp._mcp_server.add_request_handler(
        "subscriptions/listen",
        SubscriptionsListenRequestParams,
        listen_handler,
    )
    logger.info("MCP subscriptions/listen handler registered successfully")
except Exception as _sub_err:
    logger.warning("Failed to register subscriptions/listen handler: %s", _sub_err)

if _DB_AVAILABLE and oauth_relay is None:
    from oauth.oauth_relay import ExternalOAuthRelay

    oauth_relay = ExternalOAuthRelay(
        vault=vault,  # reuse the shared singleton from this module
        plugin_manifests={},
    )

