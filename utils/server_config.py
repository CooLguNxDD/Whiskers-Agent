"""Config loader for server_config.json.

Loads once at import time.  All modules that need pagination defaults or the
MCP context instruction strings import from here.

For endpoint meta (paginated flag, default page sizes, response formats) see
``utils.tools_api_config``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, NamedTuple

from utils.config_registry import get_config_registry

logger = logging.getLogger("whiskers")

SERVER_CONFIG: dict[str, Any] = get_config_registry().server


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    """Coerce to int (falling back to default), then clamp into [lo, hi]. Logs if out of range."""
    n = _to_int(value, default)
    if n < lo:
        logger.warning("config value %s below bound %s; coerced to %s", n, lo, lo)
        return lo
    if n > hi:
        logger.warning("config value %s above bound %s; coerced to %s", n, hi, hi)
        return hi
    return n


def _validate_step_up_method(value: Any) -> str:
    """Return one of {'totp','password','both'}; fall back to 'totp' (logged) otherwise."""
    if value in ("totp", "password", "both"):
        return value
    logger.warning("invalid step_up_method %r; defaulting to 'totp'", value)
    return "totp"


def _validate_null_scopes_policy(value: Any) -> str:
    """Validate null_scopes_policy, returning 'legacy_full' or 'deny'."""
    if value == "legacy_full":
        return "legacy_full"
    return "deny"


# ---------------------------------------------------------------------------
# setting defaults
# ---------------------------------------------------------------------------
PAGINATION_CONFIG: dict[str, Any] = SERVER_CONFIG.get("pagination", {})
CONTEXT_CONFIG: dict[str, Any] = SERVER_CONFIG.get("context", {})
PARAMETERS: dict[str, Any] = SERVER_CONFIG.get("parameters", {})
OAUTH_CONFIG: dict[str, Any] = SERVER_CONFIG.get("oauth", {})
GRAPH_CONFIG: dict[str, Any] = SERVER_CONFIG.get("graph", {})
SCHEDULED_JOBS_CONFIG: dict[str, Any] = SERVER_CONFIG.get("scheduled_jobs", {})
TERMINAL_RELAY_CONFIG: dict[str, Any] = SERVER_CONFIG.get("terminal_relay", {})
GATEWAY_CONFIG: dict[str, Any] = SERVER_CONFIG.get("gateway", {})
SECURITY_CONFIG: dict[str, Any] = SERVER_CONFIG.get("security", {})

API_KEY_NULL_SCOPES_POLICY: str = _validate_null_scopes_policy(SECURITY_CONFIG.get("null_scopes_policy"))
# Built-in role map so a missing/partial security.roles never leaves master/admin
# with empty issuer scopes (which 403s API-key scope create/update).
_DEFAULT_ROLES_CONFIG: dict[str, Any] = {
    "master": {"scopes": "*"},
    "admin": {"scopes": ["admin"]},
    "operator": {
        "scopes": [
            "core:whiskers.console:write",
            "core:whiskers.plugins:write",
            "core:whiskers.proxy:write",
            "core:whiskers.analytics:read",
            "core:graph:write",
            "core:config:read",
            "core:apikey:read",
        ],
        "include_plugin_tokens": True,
    },
    "viewer": {
        "scopes": [
            "core:whiskers.console:read",
            "core:whiskers.plugins:read",
            "core:whiskers.proxy:read",
            "core:whiskers.analytics:read",
            "core:graph:read",
        ],
        "include_plugin_tokens": "read",
    },
}
_configured_roles = SECURITY_CONFIG.get("roles") or {}
if not isinstance(_configured_roles, dict):
    logger.warning("security.roles is not a dict (%r); using built-in role defaults only", type(_configured_roles).__name__)
    _configured_roles = {}
elif not _configured_roles:
    logger.warning("security.roles missing or empty; merging built-in role defaults")
else:
    _REMOVED_HEURISTIC_KEYS = {"include_data_plugins", "exclude_prefixes", "include_groups_matching"}
    for _role_name, _role_def in _configured_roles.items():
        if isinstance(_role_def, dict):
            _found = _REMOVED_HEURISTIC_KEYS.intersection(_role_def.keys())
            if _found:
                logger.warning(
                    "security.roles.%s contains removed heuristic key(s) %s; replace with explicit token lists and include_plugin_tokens",
                    _role_name,
                    sorted(_found),
                )
ROLES_CONFIG: dict[str, Any] = {**_DEFAULT_ROLES_CONFIG, **_configured_roles}
FORCE_EXECUTE_ROLES: list[str] = list(SECURITY_CONFIG.get("force_execute_roles", ["master"]))
ADMIN_BYPASS_ROLES: list[str] = list(SECURITY_CONFIG.get("admin_bypass_roles", ["master", "admin"]))
# Plugin/group/terminal OAuth scope gating. Override off via SCOPE_ENFORCEMENT_OFF=1.
SCOPE_ENFORCEMENT_ENABLED: bool = bool(SECURITY_CONFIG.get("scope_enforcement_enabled", True))
# enforce | audit | off — off requires BOTH this value AND env SCOPE_ENFORCEMENT_OFF=1.
SCOPE_ENFORCEMENT_MODE: str = str(
    SECURITY_CONFIG.get("scope_enforcement_mode", "enforce") or "enforce"
).strip().lower()

# Gateway (run_graph unified) mode: when on, run_graph is the single visible MCP
# tool and every tool/plugin flagged hidden is removed from MCP exposure while
# staying reachable internally by run_graph. See core.proxy_tools.tool_visibility.
GATEWAY_UNIFIED: bool = bool(GATEWAY_CONFIG.get("run_graph_unified", False))

# Tools that are NEVER hidden in gateway unified mode — even when GATEWAY_UNIFIED
# hides everything else.  Configurable via server_config.json:
#
#   "gateway": {
#     "run_graph_unified": true,
#     "always_visible_tools": [
#       "run_graph", "discover_tools", "authenticate", "complete_authentication",
#       "fetch_artifact"
#     ]
#   }
#
# When the key is absent the hardcoded defaults below apply.
# Only fetch_artifact is always-visible for offloads (deep-read). list/get stay
# registered but gateway-hideable so hosts are not steered into a meta hop.
_GATEWAY_ALWAYS_VISIBLE_DEFAULT: frozenset[str] = frozenset(
    {
        "run_graph",
        "discover_tools",
        "authenticate",
        "complete_authentication",
        # artifact_store — deep-read only (preview is inline on the parent tool)
        "fetch_artifact",
        # PortfolioAgent_run deliberately NOT allowlisted: it is a
        # plugin-level entrypoint into the full portfolio agent (writes),
        # and skipping the scope check here would let any authenticated
        # caller (incl. a narrowly-scoped public ask key) bypass ask mode
        # entirely. It still resolves normally via its own tags/scopes.
    }
)
_gateway_always_visible_cfg = GATEWAY_CONFIG.get("always_visible_tools")
GATEWAY_ALWAYS_VISIBLE: frozenset[str] = (
    frozenset(str(t) for t in _gateway_always_visible_cfg if t)
    if isinstance(_gateway_always_visible_cfg, list)
    else _GATEWAY_ALWAYS_VISIBLE_DEFAULT
)

# Layer 1 inbound OAuth token TTLs
OAUTH_ACCESS_TOKEN_TTL_SECONDS: int = _to_int(
    OAUTH_CONFIG.get("access_token_ttl_seconds"),
    15 * 60,
)
OAUTH_REFRESH_TOKEN_TTL_SECONDS: int = _to_int(
    OAUTH_CONFIG.get("refresh_token_ttl_seconds"),
    7 * 24 * 3600,
)
OAUTH_AUTH_CODE_TTL_SECONDS: int = _to_int(
    OAUTH_CONFIG.get("auth_code_ttl_seconds"),
    10 * 60,
)

# Inbound DCR / authorize scopes the server advertises and accepts.
OAUTH_VALID_SCOPES: list[str] = OAUTH_CONFIG.get("valid_scopes") or ["whiskers"]
# Max POST /register body size accepted by RegisterScopeSanitizerMiddleware (DoS guard).
MAX_REGISTER_BODY_BYTES: int = _to_int(OAUTH_CONFIG.get("max_register_body_bytes"), 65_536)

TERMINAL_STEP_UP_METHOD: str = _validate_step_up_method(TERMINAL_RELAY_CONFIG.get("step_up_method"))
# elevation_ttl clamped to [60, 900] per the §11 loader bound
TERMINAL_ELEVATION_TTL_SECONDS: int = _clamp_int(TERMINAL_RELAY_CONFIG.get("elevation_ttl_seconds"), 300, 60, 900)
TERMINAL_STEP_UP_MAX_ATTEMPTS: int = _to_int(TERMINAL_RELAY_CONFIG.get("max_attempts"), 3)
TERMINAL_STEP_UP_LOCKOUT_SECONDS: int = _to_int(TERMINAL_RELAY_CONFIG.get("lockout_seconds"), 300)
TERMINAL_HOST_TOKEN_TTL_SECONDS: int = _to_int(
    TERMINAL_RELAY_CONFIG.get("terminal_host_token_ttl_seconds"),
    OAUTH_ACCESS_TOKEN_TTL_SECONDS,
)

_PLATFORM_RULES_DEFAULT = (
    "- NEVER fabricate entity data (names, IDs, or other sensitive values).\n"
    "- If required fields are missing, return status=need_input.\n"
    "- Bulk operations require explicit confirmation."
)

PLATFORM_RULES: str = CONTEXT_CONFIG.get(
    "platform_rules",
    CONTEXT_CONFIG.get("platform_rules", _PLATFORM_RULES_DEFAULT),
)

# Backward-compatible alias for older imports and config keys.
PLATFORM_RULES: str = PLATFORM_RULES

PLATFORM_DESCRIPTION: str = CONTEXT_CONFIG.get(
    "platform_description",
    "the Whiskers Agent MCP platform",
)

LONG_CHAIN_THRESHOLD: int = GRAPH_CONFIG.get("long_chain_threshold", 3)
CONFIDENCE_EXECUTE_THRESHOLD: float = GRAPH_CONFIG.get("confidence_execute_threshold", 0.70)
CONFIDENCE_CONFIRM_THRESHOLD: float = GRAPH_CONFIG.get("confidence_confirm_threshold", 0.50)
MAX_REPLANS: int = GRAPH_CONFIG.get("max_replans", 2)
GOAP_MAX_STEPS: int = _to_int(GRAPH_CONFIG.get("goap_max_steps"), 12)
GOAP_MAX_EXPANSIONS: int = _to_int(GRAPH_CONFIG.get("goap_max_expansions"), 1000)
CANDIDATE_TOP_K: int = _to_int(GRAPH_CONFIG.get("candidate_top_k"), 25)
# Decompose-first pipeline: run the sub-task decomposition LLM pass before
# candidate embedding retrieval (decompose → embedder → planner). Flag-off
# restores the legacy embedder-first wiring.
DECOMPOSE_FIRST: bool = bool(GRAPH_CONFIG.get("decompose_first", True))
MAX_SUBTASKS: int = _to_int(GRAPH_CONFIG.get("max_subtasks"), 4)
CANDIDATE_POOL_MAX: int = _to_int(GRAPH_CONFIG.get("candidate_pool_max"), 30)
MAX_ITERATIONS: int = _to_int(GRAPH_CONFIG.get("max_iterations"), 20)
MAX_FANOUT: int = _to_int(GRAPH_CONFIG.get("max_fanout"), 10)
FANOUT_CONCURRENCY: int = _to_int(GRAPH_CONFIG.get("fanout_concurrency"), 5)
MAX_SKILL_FILE_CHARS: int = _to_int(GRAPH_CONFIG.get("max_skill_file_chars"), 6500)
MAX_PROXY_CUSTOM_DESCRIPTION_CHARS: int = _to_int(GRAPH_CONFIG.get("max_proxy_custom_description_chars"), 2000)
MAX_SKILL_TOTAL_CHARS: int = _to_int(GRAPH_CONFIG.get("max_skill_total_chars"), 10000)
MAX_FORMATTED_PLUGIN_SKILLS_CHARS: int = _to_int(GRAPH_CONFIG.get("max_formatted_plugin_skills_chars"), 7000)
ELICIT_KEEPALIVE_INTERVAL_S: int = _to_int(GRAPH_CONFIG.get("elicit_keepalive_interval_s"), 15)
ENVELOPE_BUDGET_BYTES: int = _to_int(
    GRAPH_CONFIG.get("envelope_budget_bytes", GRAPH_CONFIG.get("envelope_budget")),
    450_000,
)
ENVELOPE_BUDGET: int = ENVELOPE_BUDGET_BYTES

# GOAP MinIO artifact offload (large step_results → short_id refs)
_ARTIFACT_OFFLOAD_RAW = (
    GRAPH_CONFIG.get("artifact_offload")
    if isinstance(GRAPH_CONFIG.get("artifact_offload"), dict)
    else {}
)
ARTIFACT_OFFLOAD_ENABLED: bool = bool(_ARTIFACT_OFFLOAD_RAW.get("enabled", True))
# Default 2000 so shaped Jules CSV (~5–15 KB) still offloads; adaptive path
# also fires when total string leaves exceed this even if no single leaf does.
ARTIFACT_OFFLOAD_MIN_FIELD_BYTES: int = _to_int(
    _ARTIFACT_OFFLOAD_RAW.get("min_field_bytes"), 2000
)
ARTIFACT_OFFLOAD_MAX_PER_ROUND: int = _to_int(
    _ARTIFACT_OFFLOAD_RAW.get("max_artifacts_per_round"), 8
)
ARTIFACT_OFFLOAD_URL_EXPIRY_SECONDS: int = _to_int(
    _ARTIFACT_OFFLOAD_RAW.get("url_expiry_seconds"), 3600
)
ARTIFACT_OFFLOAD_FETCH_DEFAULT_MAX_CHARS: int = _to_int(
    _ARTIFACT_OFFLOAD_RAW.get("fetch_default_max_chars"), 100_000
)
# Inline preview chars kept in offload markers so agents can continue without
# fetch_artifact. 0 = pointer-only (legacy). Clamped 0–8000 at use sites.
ARTIFACT_OFFLOAD_PREVIEW_CHARS: int = _to_int(
    _ARTIFACT_OFFLOAD_RAW.get("preview_chars"), 1500
)


# Core harness (static instructions + RAG plan recipes / anti-patterns)
_HARNESS_RAW = GRAPH_CONFIG.get("harness") if isinstance(GRAPH_CONFIG.get("harness"), dict) else {}
HARNESS_CONFIG: dict = {
    "enabled": bool(_HARNESS_RAW.get("enabled", True)),
    "recipe_top_k": _to_int(_HARNESS_RAW.get("recipe_top_k"), 3),
    "anti_top_k": _to_int(_HARNESS_RAW.get("anti_top_k"), 3),
    "similarity_threshold": float(_HARNESS_RAW.get("similarity_threshold", 0.78) or 0.78),
    "max_block_chars": _to_int(_HARNESS_RAW.get("max_block_chars"), 3500),
    "include_user_memory": bool(_HARNESS_RAW.get("include_user_memory", False)),
    "write_recipes": bool(_HARNESS_RAW.get("write_recipes", True)),
    "write_anti_patterns": bool(_HARNESS_RAW.get("write_anti_patterns", True)),
}
HARNESS_ENABLED: bool = bool(HARNESS_CONFIG.get("enabled", True))

# Triage node: context-aware chat/task classification + contextual chat replies
# (core_graph/node/triage.py). `harness_memory_enabled` opts into an extra
# cross-session semantic memory lookup folded into the triage/chat context block
# alongside working_memory/last_summary/history (off by default — adds an LLM/DB
# round trip per turn).
_TRIAGE_RAW = GRAPH_CONFIG.get("triage") if isinstance(GRAPH_CONFIG.get("triage"), dict) else {}
TRIAGE_CONFIG: dict = {
    "harness_memory_enabled": bool(_TRIAGE_RAW.get("harness_memory_enabled", False)),
    "harness_memory_top_k": _to_int(_TRIAGE_RAW.get("harness_memory_top_k"), 3),
}

# Spec-driven multi-model role selection (core_graph/model_roles/). Off by
# default for one release: run_role_ladder degrades to a single fallback_llm
# attempt (still recording audit + token accounting) until flipped on, so the
# observability win lands before any routing behaviour changes. See
# core_graph/model_roles/ladder.py and .claude/skills/model-role-specs/.
MODEL_ROLES_ENABLED: bool = bool(GRAPH_CONFIG.get("model_roles_enabled", False))

HYBRID_SEARCH_CONFIG: dict[str, Any] = GRAPH_CONFIG.get("hybrid_search", {})
HYBRID_SEARCH_ENABLED: bool = bool(HYBRID_SEARCH_CONFIG.get("enabled", True))
HYBRID_DENSE_TOP_N: int = _to_int(HYBRID_SEARCH_CONFIG.get("dense_top_n"), 30)
HYBRID_SPARSE_TOP_N: int = _to_int(HYBRID_SEARCH_CONFIG.get("sparse_top_n"), 30)
HYBRID_RRF_K: int = _to_int(HYBRID_SEARCH_CONFIG.get("rrf_k"), 60)
HYBRID_FUSED_TOP_N: int = _to_int(HYBRID_SEARCH_CONFIG.get("fused_top_n"), 30)
# Per-collection override of HYBRID_SEARCH_ENABLED, e.g. {"routes": true, "memory": true,
# "messages": false}. Collections not listed fall back to the global flag above. Consumed
# by db_layer/embeddings/search_engine.py::search() — the single choke point every
# embedding search (routes, memory, search index, messages, unity) funnels
# through, so hybrid-vs-dense is never mixed ad hoc per call site.
HYBRID_SEARCH_COLLECTIONS: dict[str, bool] = {
    str(k): bool(v) for k, v in (HYBRID_SEARCH_CONFIG.get("collections") or {}).items()
}

RERANK_CONFIG: dict[str, Any] = GRAPH_CONFIG.get("rerank", {})
# Default off: opt-in LLM cost/latency until graph.rerank is configured explicitly.
RERANK_ENABLED: bool = bool(RERANK_CONFIG.get("enabled", False))
RERANK_TOP_N_IN: int = _to_int(RERANK_CONFIG.get("top_n_in"), 30)
RERANK_TOP_K_OUT: int = _to_int(RERANK_CONFIG.get("top_k_out"), 12)
RERANK_TIMEOUT_S: int = _to_int(RERANK_CONFIG.get("timeout_s"), 8)

SAFE_DEFAULT_PAGE_SIZE: int = PAGINATION_CONFIG.get("safe_default_page_size", 20)
SAFE_DEFAULT_PAGE_INDEX: int = PAGINATION_CONFIG.get("safe_default_page_index", 1)
MAX_RECOMMENDED_PAGE_SIZE: int = PAGINATION_CONFIG.get("max_recommended_page_size", 100)

RATE_LIMIT_CONFIG: dict[str, Any] = SERVER_CONFIG.get("rate_limit", {})
RATE_LIMIT_ENABLED: bool = bool(RATE_LIMIT_CONFIG.get("enabled", False))
RATE_LIMIT_REQUESTS: int = _clamp_int(RATE_LIMIT_CONFIG.get("requests_per_window"), 30, 1, 10000)
RATE_LIMIT_WINDOW_SECONDS: int = _clamp_int(RATE_LIMIT_CONFIG.get("window_seconds"), 60, 1, 3600)

_configured_prefixes = RATE_LIMIT_CONFIG.get("path_prefixes")
if isinstance(_configured_prefixes, list):
    RATE_LIMIT_PATH_PREFIXES: tuple[str, ...] = tuple(str(p) for p in _configured_prefixes)
else:
    RATE_LIMIT_PATH_PREFIXES = ("/mcp", "/api/portfolio")

RATE_LIMIT_TRUST_PROXY: bool = bool(RATE_LIMIT_CONFIG.get("trust_proxy", False))
RATE_LIMIT_MAX_IPS: int = _clamp_int(RATE_LIMIT_CONFIG.get("max_tracked_ips"), 10000, 100, 1000000)



# Layer 2 plugin token fallback TTLs (used when provider omits expires_in)
PLUGIN_EXTERNAL_OAUTH_TOKEN_DEFAULT_TTL_SECONDS: int = _to_int(
    OAUTH_CONFIG.get("plugin_external_oauth_token_default_ttl_seconds"),
    12 * 3600,
)
PLUGIN_DIRECT_AUTH_TOKEN_DEFAULT_TTL_SECONDS: int = _to_int(
    OAUTH_CONFIG.get("plugin_direct_auth_token_default_ttl_seconds"),
    12 * 3600,
)


# ---------------------------------------------------------------------------
# CORS policy (env-driven, resolved per-process at server startup)
# ---------------------------------------------------------------------------
# Defaults cover console (:3000) and CatPortfolio docker nginx (:11000).
CORS_DEFAULT_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:11000,http://127.0.0.1:11000"
)

_TRUTHY = ("1", "true", "yes", "on")


class CorsPolicy(NamedTuple):
    """Resolved CORS decision: relaxed origin-reflection mode + the allowlist."""

    relaxed: bool
    origins: list[str]
    #: Operator-facing misconfiguration message, or "" when the config is coherent.
    warning: str


def resolve_cors_policy(env: Mapping[str, str] | None = None) -> CorsPolicy:
    """Resolve the CORS mode from the environment.

    Relaxed mode reflects *any* Origin with ``allow_credentials=True``, which is a
    real browser security boundary — so it requires an explicit ``MCP_CORS_RELAXED``
    opt-in. A wildcard ``MCP_CORS_ORIGINS=*`` deliberately does **not** imply it:
    silently widening the boundary because an allowlist looked permissive is how
    dev config leaks into shared stacks.
    """
    src = os.environ if env is None else env

    raw = src.get("MCP_CORS_ORIGINS", CORS_DEFAULT_ORIGINS)
    wildcard = raw.strip() in ("*", "all")
    relaxed = src.get("MCP_CORS_RELAXED", "").strip().lower() in _TRUTHY
    origins = [
        o.strip() for o in raw.split(",") if o.strip() and o.strip() not in ("*", "all")
    ]

    warning = ""
    if wildcard and not relaxed:
        # The wildcard contributes no entries, so the allowlist is now empty and
        # every cross-origin browser call fails. Name the fix rather than 403ing mutely.
        warning = (
            "MCP_CORS_ORIGINS is '*' but MCP_CORS_RELAXED is not set — wildcard origins "
            "no longer imply relaxed mode, so the allowlist is EMPTY and all cross-origin "
            "browser requests will be blocked. Set MCP_CORS_RELAXED=1 to opt into "
            "origin reflection (dev only), or list real origins in MCP_CORS_ORIGINS."
        )

    return CorsPolicy(relaxed=relaxed, origins=origins, warning=warning)
