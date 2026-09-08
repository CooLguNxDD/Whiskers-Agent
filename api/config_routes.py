"""




Server configuration REST API.

Exposes read/write of non-sensitive runtime config overrides stored in the
``server_settings`` DB table.  API keys are NEVER returned or accepted here —
they remain in environment variables only.

Endpoints
---------
GET    /api/config/session_gated/gateway                             — Return current gateway unified state (DB override or JSON default).
POST   /api/config/session_gated/gateway                             — Persist gateway unified flag and live-apply visibility hides/shows.
GET    /api/config/session_gated/llm                                 — Return current LLM/RAG settings merged from env vars + DB overrides.
POST   /api/config/session_gated/llm                                 — Persist non-sensitive LLM/RAG settings to server_settings table.
GET    /api/config/session_gated/llm/pool                            — List pool entries (no tokens) plus the active chat/embedding selection.
POST   /api/config/session_gated/llm/pool                            — Add (or upsert by name) a pool entry. ``api_key`` is encrypted at rest.
POST   /api/config/session_gated/llm/pool/active                     — Set the active pool entry for a kind (chat | embedding) and rebuild the graph.
DELETE /api/config/session_gated/llm/pool/{entry_id}                 — Delete a pool entry by id.
POST   /api/config/session_gated/llm/pool/{entry_id}/active-toggle   — Toggle the is_active status of a pool entry.
GET    /api/config/session_gated/step-models                         — Return the step model policy plus the names of active chat pool entries (for UI dropdowns).
POST   /api/config/session_gated/step-models                         — Merge-save the step model policy patch; returns the updated policy.
GET    /api/config/session_gated/model-roles                         — Return every model-role ladder (source-tagged) + effort_map + resolved preview.
POST   /api/config/session_gated/model-roles                         — Save one role's ladder (400 on invalid spec) or the effort_map.
DELETE /api/config/session_gated/model-roles/{role_id}                — Revert one role's DB override to its core/plugin default.
GET    /api/config/session_gated/plugin-gates                         — Return every plugin's effective level-3 gate (manifest or DB override), source-tagged.
POST   /api/config/session_gated/plugin-gates                         — Save one plugin's DB gate override (400 on invalid spec); fully replaces the manifest gate.
DELETE /api/config/session_gated/plugin-gates/{plugin_id}             — Revert one plugin's DB gate override to its manifest default (or no ceiling).
GET    /api/config/session_gated/cli-agents                            — Return registered CLI agent drivers with binary availability.
GET    /api/workflows/session_gated                                  — List recent compiled and executed YAML workflow plans.
GET    /api/workflows/session_gated/{workflow_id}                    — Retrieve details for a single YAML workflow plan.
"""

import asyncio
import logging
import os
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from utils.error_response import safe_error_response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from core.context import mcp
from db_layer import config_store

logger = logging.getLogger("whiskers")

_ALLOWED_LLM_KEYS = {
    "cli_agent_provider",
    "llm_provider",
    "llm_model",
    "embed_provider",
    "embed_model",
    "embed_base_url",
    "embed_dimensions",
    "rag_enabled",
    "local_llm_enabled",
    "local_base_url",
}


def _env_defaults() -> dict:
    """Build LLM settings from environment variables."""
    return {
        "llm_provider": os.environ.get("LLM_PROVIDER", "openai"),
        "llm_model": os.environ.get("LLM_MODEL", ""),
        "embed_provider": os.environ.get("EMBED_PROVIDER", ""),
        "embed_model": os.environ.get("EMBED_MODEL", ""),
        "embed_base_url": os.environ.get("EMBED_BASE_URL", ""),
        "embed_dimensions": int(os.environ.get("EMBED_DIMENSIONS", "1536")),
        "rag_enabled": True,
        "local_llm_enabled": False,
        "local_base_url": os.environ.get("LLM_BASE_URL", ""),
        "cli_agent_provider": os.environ.get("CLI_AGENT_PROVIDER", ""),
    }


def _has_api_key(provider: str) -> bool:
    """Return True if the relevant env-var API key is set (never expose the key itself)."""
    checks = {
        "openai":    lambda: bool(os.environ.get("OPENAI_API_KEY")),
        "anthropic": lambda: bool(os.environ.get("ANTHROPIC_API_KEY")),
        "gemini":    lambda: bool(os.environ.get("GOOGLE_API_KEY")),
    }
    return checks.get(provider, lambda: False)()


# ---------------------------------------------------------------------------
# GET /api/config/llm
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="config",
    endpoint="llm",
    methods=["GET"],
    name="api_get_llm_config",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:read",),
)
async def get_llm_config(request: Request) -> Response:
    """Return current LLM/RAG settings merged from env vars + DB overrides."""
    settings = _env_defaults()

    try:
        db_overrides = await config_store.get_llm_settings(_ALLOWED_LLM_KEYS)
        settings.update(db_overrides)
    except Exception as exc:
        # DB unavailable — serve env defaults; config page still renders
        logger.warning("api/config/llm GET: DB unavailable, using env defaults: %s", exc)

    settings["has_api_key"] = _has_api_key(settings.get("llm_provider", "openai"))
    return JSONResponse(settings)


# ---------------------------------------------------------------------------
# POST /api/config/llm
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="config",
    endpoint="llm",
    methods=["POST"],
    name="api_save_llm_config",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def save_llm_config(request: Request) -> Response:
    """Persist non-sensitive LLM/RAG settings to server_settings table."""
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("config_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    # Strip disallowed keys — never persist API key variants
    filtered = {k: v for k, v in body.items() if k in _ALLOWED_LLM_KEYS}
    if not filtered:
        return JSONResponse({"error": "no valid keys provided"}, status_code=400)

    try:
        # Merge with existing value so a partial POST doesn't wipe other settings
        merged = await config_store.get_llm_settings()
        merged.update(filtered)
        await config_store.upsert_llm_settings(merged)

        logger.info("LLM config updated: %s", list(filtered.keys()))
        return JSONResponse({"ok": True, "saved": filtered})

    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/config/llm POST")


# ---------------------------------------------------------------------------
# Dynamic LLM pool (core_019) — selectable chat/embedding models + tokens.
# Tokens are write-only here: accepted on POST, never returned on GET.
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="config",
    endpoint="llm/pool",
    methods=["GET"],
    name="api_list_llm_pool",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:read",),
)
async def list_llm_pool(request: Request) -> Response:
    """List pool entries (no tokens) plus the active chat/embedding selection."""
    from core.llm_config_service import get_active_map, list_pool
    try:
        entries = await list_pool()
        active = await get_active_map()
    except Exception as exc:
        logger.warning("api/config/llm/pool GET: %s", exc)
        return JSONResponse({"entries": [], "active": {}})
    return JSONResponse({"entries": entries, "active": active})


@http_route_registry.route(
    route="config",
    endpoint="llm/pool",
    methods=["POST"],
    name="api_add_llm_pool",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def add_llm_pool(request: Request) -> Response:
    """Add (or upsert by name) a pool entry. ``api_key`` is encrypted at rest."""
    from core.llm_config_service import add_pool_entry
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("config_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    name = (body.get("name") or "").strip()
    provider = (body.get("provider") or "").strip()
    model = (body.get("model") or "").strip()
    if not name or not provider or not model:
        return JSONResponse({"error": "name, provider and model are required"}, status_code=400)

    try:
        strength_val = body.get("strength")
        if strength_val is not None:
            try:
                strength = float(strength_val)
            except (ValueError, TypeError):
                return JSONResponse({"error": "strength must be a valid number"}, status_code=400)
        else:
            strength = 1.0

        result = await add_pool_entry(
            name=name,
            provider=provider,
            model=model,
            kind=body.get("kind", "chat"),
            dimensions=body.get("dimensions"),
            base_url=body.get("base_url"),
            api_key=body.get("api_key"),
            strength=strength,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/config/llm/pool POST")
    return JSONResponse({"ok": True, "entry": result})


@http_route_registry.route(
    route="config",
    endpoint="llm/pool/{entry_id}",
    methods=["DELETE"],
    name="api_delete_llm_pool",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def delete_llm_pool(request: Request) -> Response:
    """Delete a pool entry by id."""
    from core.llm_config_service import delete_pool_entry
    entry_id = request.path_params["entry_id"]
    try:
        uuid.UUID(entry_id)
    except ValueError:
        return JSONResponse({"error": "invalid_id"}, status_code=400)
    try:
        await delete_pool_entry(entry_id)
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/config/llm/pool DELETE")
    return JSONResponse({"ok": True})


@http_route_registry.route(
    route="config",
    endpoint="llm/pool/active",
    methods=["POST"],
    name="api_set_llm_active",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def set_llm_active(request: Request) -> Response:
    """Set the active pool entry for a kind (chat | embedding) and rebuild the graph."""
    from core.llm_config_service import set_active
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("config_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    kind = body.get("kind")
    entry_id = body.get("id")
    if not kind or not entry_id:
        return JSONResponse({"error": "kind and id are required"}, status_code=400)

    try:
        active = await set_active(kind, entry_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/config/llm/pool/active POST")

    # Rebuild the graph so the new chat model takes effect without a restart.
    try:
        from core_graph.mcp_tool import invalidate_graph
        invalidate_graph()
    except Exception as exc:
        logger.warning("invalidate_graph after active change failed: %s", exc)

    return JSONResponse({"ok": True, "active": active})


@http_route_registry.route(
    route="config",
    endpoint="llm/pool/{entry_id}/active-toggle",
    methods=["POST"],
    name="api_toggle_llm_active",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def toggle_llm_active(request: Request) -> Response:
    """Toggle the is_active status of a pool entry."""
    from core.llm_config_service import set_pool_entry_active
    entry_id = request.path_params["entry_id"]
    try:
        uuid.UUID(entry_id)
    except ValueError:
        return JSONResponse({"error": "invalid_id"}, status_code=400)
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("config_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    enabled = body.get("enabled")
    if enabled is None:
        return JSONResponse({"error": "enabled is required"}, status_code=400)

    try:
        await set_pool_entry_active(entry_id, bool(enabled))
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/config/llm/pool/{entry_id}/active-toggle POST")

    # Rebuild the graph so changes take effect
    try:
        from core_graph.mcp_tool import invalidate_graph
        invalidate_graph()
    except Exception as exc:
        logger.warning("invalidate_graph after active-toggle failed: %s", exc)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Workflow plans GET (core_020) — audit and logs REST API.
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="workflows",
    endpoint="",
    methods=["GET"],
    name="api_list_workflows",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
)
async def list_workflows(request: Request) -> Response:
    """List recent compiled and executed YAML workflow plans."""
    from db_layer.workflow_store import list_workflow_plans
    limit = 20
    try:
        if "limit" in request.query_params:
            limit = int(request.query_params["limit"])
    except ValueError:
        pass
    try:
        plans = await list_workflow_plans(limit=limit)
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/workflows GET")
    return JSONResponse({"workflows": plans})


@http_route_registry.route(
    route="workflows",
    endpoint="{workflow_id}",
    methods=["GET"],
    name="api_get_workflow",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
)
async def get_workflow(request: Request) -> Response:
    """Retrieve details for a single YAML workflow plan."""
    from db_layer.workflow_store import get_workflow_plan
    workflow_id = request.path_params["workflow_id"]
    try:
        plan = await get_workflow_plan(workflow_id)
        if not plan:
            return JSONResponse({"error": "not_found"}, status_code=404)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/workflows/{workflow_id} GET")
    return JSONResponse(plan)


# ---------------------------------------------------------------------------
# Gateway (run_graph_unified) live config (Part B).
# Persisted in server_settings via gateway_settings_store; layered over JSON default.
# Live apply of visibility without restart.
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="config",
    endpoint="gateway",
    methods=["GET"],
    name="api_get_gateway_config",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:read",),
)
async def get_gateway_config(request: Request) -> Response:
    """Return current gateway unified state (DB override or JSON default)."""
    from db_layer.gateway_settings_store import get_gateway_unified
    try:
        enabled = await get_gateway_unified()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("api/config/gateway GET failed", exc_info=True)
        from utils.server_config import GATEWAY_UNIFIED
        enabled = GATEWAY_UNIFIED
    return JSONResponse({"run_graph_unified": enabled})


@http_route_registry.route(
    route="config",
    endpoint="gateway",
    methods=["POST"],
    name="api_save_gateway_config",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def save_gateway_config(request: Request) -> Response:
    """Persist gateway unified flag and live-apply visibility hides/shows.

    ON: hide every registered MCP tool except GATEWAY_ALWAYS_VISIBLE.
    OFF: restore those tools, then re-apply disabled + selective is_hidden.
    """
    from db_layer.gateway_settings_store import (
        set_gateway_unified,
        collect_gateway_unified_hide_names,
        collect_selective_hidden_names,
    )
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("config_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    enabled_raw = body.get("run_graph_unified")
    if enabled_raw is None:
        return JSONResponse({"error": "run_graph_unified is required"}, status_code=400)
    try:
        enabled = bool(enabled_raw)
    except Exception:
        return JSONResponse({"error": "run_graph_unified must be boolean"}, status_code=400)

    try:
        new_val = await set_gateway_unified(enabled)
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/config/gateway POST")

    applied = 0
    try:
        from core.context import tool_visibility
        # Unified set = all live MCP tools minus allowlist (not selective flags).
        names = await collect_gateway_unified_hide_names()
        if new_val:
            applied = tool_visibility.apply_hidden(names)
            logger.info("Gateway ON (live): hid %d tools", applied)
        else:
            # Restore everything gateway had hidden, then re-assert disabled + selective.
            for n in names:
                if tool_visibility.show_gateway(n):
                    applied += 1
            try:
                from db_layer.tool_config_store import get_disabled_tools
                tool_visibility.apply_persisted(await get_disabled_tools())
            except Exception as exc:
                logger.warning("Gateway OFF: re-apply disabled tools failed: %s", exc)
            try:
                selective = await collect_selective_hidden_names()
                if selective:
                    tool_visibility.apply_hidden(selective)
            except Exception as exc:
                logger.warning("Gateway OFF: re-apply selective hidden failed: %s", exc)
            logger.info("Gateway OFF (live): restored %d tools", applied)
    except Exception as exc:
        logger.warning("Gateway live apply after toggle failed: %s", exc)

    # Refresh instructions summary after visibility change
    try:
        from db_layer.gateway_settings_store import refresh_tools_summary
        await refresh_tools_summary()
    except Exception as exc:
        logger.warning("refresh after gateway toggle: %s", exc)

    return JSONResponse({"run_graph_unified": new_val, "applied": applied})


# ---------------------------------------------------------------------------
# Step model policy (GOAP plan step LLM assignment + parallelism).
# Persisted in server_settings via step_model_settings_store.
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="config",
    endpoint="step-models",
    methods=["GET"],
    name="api_get_step_models",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:read",),
)
async def get_step_models(request: Request) -> Response:
    """Return the step model policy plus the names of active chat pool entries (for UI dropdowns)."""
    from db_layer.step_model_settings_store import DEFAULT_POLICY, get_step_model_policy
    import copy

    try:
        policy = await get_step_model_policy()
    except Exception as exc:
        logger.warning("api/config/step-models GET policy: %s", exc)
        policy = copy.deepcopy(DEFAULT_POLICY)

    pool_names: list[str] = []
    try:
        from core.llm_config_service import list_pool
        entries = await list_pool(kind="chat")
        pool_names = [e["name"] for e in entries if e.get("is_active") and e.get("name")]
    except Exception as exc:
        logger.warning("api/config/step-models GET pool: %s", exc)

    return JSONResponse({"policy": policy, "pool_names": pool_names})


@http_route_registry.route(
    route="config",
    endpoint="step-models",
    methods=["POST"],
    name="api_save_step_models",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def save_step_models(request: Request) -> Response:
    """Merge-save the step model policy patch; returns the updated policy."""
    from db_layer.step_model_settings_store import set_step_model_policy

    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("config_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    merged = await set_step_model_policy(body)

    try:
        from core_graph.mcp_tool import invalidate_graph
        invalidate_graph()
    except Exception as exc:
        logger.warning("invalidate_graph after step-models save failed: %s", exc)

    return JSONResponse({"ok": True, "policy": merged})


# ---------------------------------------------------------------------------
# Model-role ladders (core_graph/model_roles/) — spec-driven per-node model
# selection. Separate server_settings key (model_role_specs, via
# db_layer.model_role_store) from step_model_policy above: different
# lifecycle (read-time ladder resolution vs write-time step stamping) and
# strict validation on write (400 on ModelRoleSpecError) rather than
# step_model_policy's permissive patch-merge.
# ---------------------------------------------------------------------------

def _role_spec_to_dict(spec) -> dict:
    """Serialize a ModelRoleSpec (frozen dataclass) into a JSON-safe dict."""
    validate = None
    if spec.validate is not None:
        validate = {
            "require_json": spec.validate.require_json,
            "required_keys": list(spec.validate.required_keys),
            "enum_field": spec.validate.enum_field,
            "enum_values": list(spec.validate.enum_values),
            "non_empty": spec.validate.non_empty,
        }
    return {
        "role_id": spec.role_id,
        "description": spec.description,
        "ladder": [
            {"selector": r.selector, "max_attempts": r.max_attempts, "timeout_s": r.timeout_s}
            for r in spec.ladder
        ],
        "validate": validate,
        "entry_conditions": [
            {"field": c.field, "op": c.op, "value": c.value, "advance": c.advance}
            for c in spec.entry_conditions
        ],
        "escalate_on_exception": spec.escalate_on_exception,
        "escalate_on_invalid": spec.escalate_on_invalid,
        "terminal_fallback": spec.terminal_fallback,
        "owner": spec.owner,
    }


@http_route_registry.route(
    route="config",
    endpoint="model-roles",
    methods=["GET"],
    name="api_get_model_roles",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:read",),
)
async def get_model_roles(request: Request) -> Response:
    """Return every model-role ladder (source-tagged) + effort_map + resolved preview."""
    from core_graph.model_roles.node_bindings import NODE_ROLES
    from core_graph.model_roles.registry import get_model_role_registry, list_model_roles
    from core_graph.model_roles.resolver import get_effort_map, resolve_role_llm
    from core_graph.model_roles.role_spec import _ALIASES, _EFFORTS

    try:
        registry = get_model_role_registry()
        specs = list_model_roles()
        roles: list[dict] = []
        for spec in specs:
            entry = _role_spec_to_dict(spec)
            entry["source"] = registry.source_of(spec.role_id)
            preview: list[dict] = []
            for rung in spec.ladder:
                try:
                    _llm, model_name = await resolve_role_llm(rung.selector)
                except Exception as exc:
                    logger.debug("api/config/model-roles preview rung failed: %s", exc)
                    model_name = None
                preview.append({"selector": rung.selector, "resolved_model": model_name})
            entry["preview"] = preview
            roles.append(entry)
        effort_map = get_effort_map()
    except Exception as exc:
        logger.warning("api/config/model-roles GET: %s", exc)
        return safe_error_response(exc, log_ctx="api/config/model-roles GET")

    pool_names: list[str] = []
    try:
        from core.llm_config_service import list_pool
        entries = await list_pool(kind="chat")
        pool_names = [e["name"] for e in entries if e.get("is_active") and e.get("name")]
    except Exception as exc:
        logger.warning("api/config/model-roles GET pool: %s", exc)

    return JSONResponse({
        "roles": roles,
        "effort_map": effort_map,
        "pool_names": pool_names,
        "aliases": sorted(_ALIASES),
        "efforts": sorted(_EFFORTS),
        "node_roles": {k: list(v) for k, v in NODE_ROLES.items()},
    })


async def _reload_model_role_overlay() -> None:
    """Best-effort: reload DB overrides into the registry, then bust the pool
    snapshot + compiled-graph cache. Split from a single sync call because
    the overlay reload is async (a DB read) while ``invalidate_graph`` is
    sync and already the universal config-save hook."""
    try:
        from core_graph.model_roles.db_overlay import apply_db_overrides
        await apply_db_overrides()
    except Exception as exc:
        logger.warning("model-roles overlay reload failed: %s", exc)
    try:
        from core_graph.mcp_tool import invalidate_graph
        invalidate_graph()
    except Exception as exc:
        logger.warning("invalidate_graph after model-roles save failed: %s", exc)


@http_route_registry.route(
    route="config",
    endpoint="model-roles",
    methods=["POST"],
    name="api_save_model_role",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def save_model_role(request: Request) -> Response:
    """Save one role's ladder (body: {role_id, spec}) or the effort_map (body: {effort_map})."""
    from core_graph.model_roles.role_spec import ModelRoleSpecError
    from db_layer.model_role_store import set_effort_map, set_model_role_override

    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("config_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    if "effort_map" in body:
        try:
            await set_effort_map(body.get("effort_map"))
        except ModelRoleSpecError as exc:
            return JSONResponse({"error": "invalid_spec", "message": str(exc)}, status_code=400)
        except Exception as exc:
            return safe_error_response(exc, log_ctx="api/config/model-roles POST effort_map")
        await _reload_model_role_overlay()
        from core_graph.model_roles.resolver import get_effort_map
        return JSONResponse({"ok": True, "effort_map": get_effort_map()})

    role_id = body.get("role_id")
    spec = body.get("spec")
    if not isinstance(role_id, str) or not role_id.strip():
        return JSONResponse({"error": "invalid_json", "message": "'role_id' is required"}, status_code=400)
    if not isinstance(spec, dict):
        return JSONResponse({"error": "invalid_json", "message": "'spec' must be an object"}, status_code=400)

    try:
        await set_model_role_override(role_id, spec)
    except ModelRoleSpecError as exc:
        return JSONResponse({"error": "invalid_spec", "message": str(exc)}, status_code=400)
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/config/model-roles POST")

    await _reload_model_role_overlay()

    from core_graph.model_roles.registry import get_model_role
    effective = get_model_role(role_id)
    return JSONResponse({"ok": True, "role": _role_spec_to_dict(effective) if effective else None})


@http_route_registry.route(
    route="config",
    endpoint="model-roles/{role_id}",
    methods=["DELETE"],
    name="api_delete_model_role",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def delete_model_role(request: Request) -> Response:
    """Revert one role's DB override to its core/plugin default."""
    from db_layer.model_role_store import get_model_role_overrides, set_model_role_override

    role_id = request.path_params["role_id"]
    stored = await get_model_role_overrides()
    if role_id not in (stored.get("roles") or {}):
        return JSONResponse({"error": "not_found"}, status_code=404)

    try:
        await set_model_role_override(role_id, None)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/config/model-roles/{role_id} DELETE")

    await _reload_model_role_overlay()

    from core_graph.model_roles.registry import get_model_role
    effective = get_model_role(role_id)
    return JSONResponse({"ok": True, "role": _role_spec_to_dict(effective) if effective else None})


# ---------------------------------------------------------------------------
# Plugin gates (core/scope_management/gates.py) — level-3 ceiling on what a
# plugin itself may reach. Separate server_settings key (plugin_gate_specs,
# via db_layer.plugin_gate_store) mirroring model-role-specs' structure: a
# DB override strictly validates on write and **fully replaces** (never
# merges with) the plugin's manifest-declared gate.
# ---------------------------------------------------------------------------

def _gate_spec_to_dict(spec) -> dict:
    """Serialize a PluginGateSpec (frozen dataclass) into a JSON-safe dict."""
    return {
        "plugin_id": spec.plugin_id,
        "core": list(spec.core),
        "access": spec.access,
        "operations": {"allow": list(spec.operations.allow), "deny": list(spec.operations.deny)},
        "endpoints": list(spec.endpoints),
        "owner": spec.owner,
    }


@http_route_registry.route(
    route="config",
    endpoint="plugin-gates",
    methods=["GET"],
    name="api_get_plugin_gates",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:read",),
)
async def get_plugin_gates(request: Request) -> Response:
    """Return every plugin's effective gate (manifest or DB override), source-tagged."""
    from core.scope_management.gates import get_plugin_gate_registry

    try:
        registry = get_plugin_gate_registry()
        gates: list[dict] = []
        for plugin_id in registry.all_gated_plugin_ids():
            spec = registry.get_gate(plugin_id)
            if spec is None:
                continue
            entry = _gate_spec_to_dict(spec)
            entry["source"] = "db" if spec.owner == "db" else "manifest"
            gates.append(entry)
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/config/plugin-gates GET")

    return JSONResponse({"gates": gates})


async def _reload_plugin_gate_overlay() -> None:
    """Best-effort: reload DB gate overrides into the registry, then bust the
    compiled-graph cache. Mirrors ``_reload_model_role_overlay`` exactly."""
    try:
        from core.scope_management.gate_overlay import apply_db_overrides
        await apply_db_overrides()
    except Exception as exc:
        logger.warning("plugin-gates overlay reload failed: %s", exc)
    try:
        from core_graph.mcp_tool import invalidate_graph
        invalidate_graph()
    except Exception as exc:
        logger.warning("invalidate_graph after plugin-gates save failed: %s", exc)


@http_route_registry.route(
    route="config",
    endpoint="plugin-gates",
    methods=["POST"],
    name="api_save_plugin_gate",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def save_plugin_gate(request: Request) -> Response:
    """Save one plugin's DB gate override (body: {plugin_id, spec}). Fully replaces the manifest gate."""
    from core.scope_management.gates import GateSpecError
    from db_layer.plugin_gate_store import set_plugin_gate_override

    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("config_routes: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    plugin_id = body.get("plugin_id")
    spec = body.get("spec")
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        return JSONResponse({"error": "invalid_json", "message": "'plugin_id' is required"}, status_code=400)
    if not isinstance(spec, dict):
        return JSONResponse({"error": "invalid_json", "message": "'spec' must be an object"}, status_code=400)

    before = None
    try:
        from core.scope_management.gates import get_plugin_gate_registry
        existing = get_plugin_gate_registry().get_gate(plugin_id)
        before = _gate_spec_to_dict(existing) if existing else None
        await set_plugin_gate_override(plugin_id, spec)
    except GateSpecError as exc:
        return JSONResponse({"error": "invalid_spec", "message": str(exc)}, status_code=400)
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/config/plugin-gates POST")

    await _reload_plugin_gate_overlay()

    from core.scope_management.gates import get_plugin_gate_registry
    effective = get_plugin_gate_registry().get_gate(plugin_id)
    after = _gate_spec_to_dict(effective) if effective else None
    logger.info("api/config/plugin-gates POST: plugin_id=%s before=%s after=%s", plugin_id, before, after)
    return JSONResponse({"ok": True, "gate": after})


@http_route_registry.route(
    route="config",
    endpoint="plugin-gates/{plugin_id}",
    methods=["DELETE"],
    name="api_delete_plugin_gate",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:write",),
)
async def delete_plugin_gate(request: Request) -> Response:
    """Revert one plugin's DB gate override to its manifest-declared default (or no ceiling)."""
    from core.scope_management.gates import get_plugin_gate_registry
    from db_layer.plugin_gate_store import get_plugin_gate_overrides, set_plugin_gate_override

    plugin_id = request.path_params["plugin_id"]
    stored = await get_plugin_gate_overrides()
    if plugin_id not in stored:
        return JSONResponse({"error": "not_found"}, status_code=404)

    # `stored[plugin_id]` is a raw dict (get_plugin_gate_overrides is
    # unparsed by contract), not a PluginGateSpec — _gate_spec_to_dict needs
    # attribute access. Read "before" from the live registry instead, same
    # as the POST route above.
    before_spec = get_plugin_gate_registry().get_gate(plugin_id)
    before = _gate_spec_to_dict(before_spec) if before_spec else None

    try:
        await set_plugin_gate_override(plugin_id, None)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/config/plugin-gates/{plugin_id} DELETE")

    await _reload_plugin_gate_overlay()

    effective = get_plugin_gate_registry().get_gate(plugin_id)
    after = _gate_spec_to_dict(effective) if effective else None
    logger.info("api/config/plugin-gates/%s DELETE: before=%s after=%s", plugin_id, before, after)
    return JSONResponse({"ok": True, "gate": after})


# ---------------------------------------------------------------------------
# CLI Agent driver registry — dynamic list + active-driver selector.
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="config",
    endpoint="cli-agents",
    methods=["GET"],
    name="api_get_cli_agents",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.config",
    required_scopes=("core:config:read",),
)
async def get_cli_agents(request: Request) -> Response:
    """Return registered CLI agent drivers with binary availability status."""
    import asyncio
    from core_graph.goap_agent.cli.registry import list_drivers, get_driver

    drivers: list[dict] = []
    for driver_name in list_drivers():
        try:
            driver = get_driver(driver_name)
            available = await asyncio.to_thread(driver.available)
            binary = driver.binary()
        except Exception as exc:
            logger.warning("get_cli_agents: driver probe failed for '%s': %s", driver_name, exc)
            available = False
            binary = ""
        drivers.append({
            "name": driver_name,
            "available": available,
            "binary": binary,
        })

    active = (os.environ.get("CLI_AGENT_PROVIDER") or "").strip().lower()
    try:
        db_override = await config_store.get_llm_settings({"cli_agent_provider"})
        active = db_override.get("cli_agent_provider", active)
    except Exception as exc:
        logger.debug("swallowed exception (non-fatal): %s", exc)

    return JSONResponse({
        "drivers": drivers,
        "active": active or None,
    })
