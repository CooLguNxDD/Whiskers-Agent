"""
permission_gate — DB-backed read/write policy gate (core_028) + scope gate.

Placed as a single chokepoint before the builder for all execution paths.
Classifies the step by operation (read/write) using its route metadata,
loads policy from permission_store, and either:
- passes through ({}),
- errors for explicit denials,
- or emits the same confirmation_needed envelope that the elicit loop handles.
force_execute bypasses confirmation (as before).
"""

import logging
from typing import Any

from core_graph.states import DynamicAPIState
from core_graph.node.helpers import resolve_route_candidate, operation_class
from core_graph.node.context import GraphRuntimeContext

logger = logging.getLogger("whiskers")


def make_permission_gate_node(ctx: GraphRuntimeContext):
    """Creates the permission gate node to enforce per-tool access policies during plan execution."""
    async def permission_gate_node(state: DynamicAPIState) -> dict:
        """Gate execution according to policy for the current step."""
        import os
        from core.context.transport import is_local_stdio
        if os.environ.get("DANGEROUSLY_SKIP_PERMISSIONS") == "true" and is_local_stdio():
            logger.warning("DANGEROUSLY_SKIP_PERMISSIONS is enabled (stdio only). Bypassing permission gate checks.")
            return {}

        plan = state.get("plan") or []
        idx = state.get("current_step_index", 0)
        step = plan[idx] if 0 <= idx < len(plan) else {}
        plugin_id = step.get("plugin_id") or ""
        operation_id = step.get("operation_id") or step.get("tool") or ""

        # resolve authoritative route (for method + validation)
        route = resolve_route_candidate(
            operation_id, plugin_id, state.get("candidates"), ctx.route_registry
        ) or (state.get("selected") or {})

        # determine class
        cls = operation_class(route)

        # load policy (defaults inside store)
        from db_layer.permission_store import DEFAULT_POLICY, get_permission
        try:
            policy = await get_permission(plugin_id or "", operation_id or "")
        except Exception as exc:
            # NOTE: get_permission returns DEFAULT_POLICY internally for absent rows,
            # so this branch only fires on genuine DB/import failures, never routine misses.
            logger.error("permission_gate: policy load failed, using defaults: %s", exc)
            policy = dict(DEFAULT_POLICY)

        # force_execute is explicit user approval; goal is only an agentic objective.
        # Do not treat goal as write-confirmation bypass (security: confirm chokepoint).
        force = bool(state.get("force_execute"))

        if cls == "read" and not policy.get("allow_read", True):
            return {
                "response": {
                    "status": "error",
                    "message": f"Read access denied for {operation_id} (policy).",
                    "operation_id": operation_id,
                }
            }

        if cls == "write" and not policy.get("allow_write", True):
            return {
                "response": {
                    "status": "error",
                    "message": f"Write access denied for {operation_id} (policy).",
                    "operation_id": operation_id,
                }
            }

        if cls == "write" and policy.get("require_confirmation", True) and not force:
            return {
                "response": {
                    "status": "confirmation_needed",
                    "message": f"About to run write operation '{operation_id}'. Proceed?",
                    "operation_id": operation_id,
                    "resolved_args": state.get("resolved_args") or {},
                }
            }

        # --- Scope gate via ScopeManager ---
        from core.scope_management import (
            PrincipalKind,
            ScopeGrant,
            evaluate_access,
            REASON_DENY_ANONYMOUS,
        )

        caller_scopes = state.get("caller_scopes")
        caller_role = state.get("caller_role")
        raw_kind = state.get("caller_kind")
        if isinstance(raw_kind, PrincipalKind):
            kind = raw_kind
        elif isinstance(raw_kind, str):
            try:
                kind = PrincipalKind(raw_kind)
            except ValueError:
                # Unknown/malformed kind → fail-closed ANONYMOUS unless local stdio
                logger.warning("permission_gate: unknown caller_kind %r — treating as ANONYMOUS", raw_kind)
                kind = (
                    PrincipalKind.LOCAL_CLI
                    if (caller_scopes is None and is_local_stdio())
                    else PrincipalKind.ANONYMOUS
                )
        else:
            # Default: None scopes → LOCAL_CLI (agent/stdio); empty list → API_KEY deny
            if caller_scopes is None:
                kind = PrincipalKind.LOCAL_CLI if is_local_stdio() else PrincipalKind.ANONYMOUS
            else:
                kind = PrincipalKind.API_KEY

        # Local stdio unrestricted short-circuit
        if is_local_stdio() and caller_scopes is None:
            return {}

        grant = ScopeGrant(
            scopes=list(caller_scopes) if caller_scopes is not None else None,
            role=caller_role,
            kind=kind,
        )
        tags: tuple = ()
        try:
            desc = ctx.route_registry.get(operation_id, plugin_id) if ctx.route_registry else None
            tags = desc.tags if desc else ()
        except Exception:
            tags = ()

        decision = evaluate_access(
            grant,
            plugin_id=plugin_id,
            tags=tags,
            tool_name=operation_id,
            path="graph_step",
        )
        if not decision.allowed:
            msg = (
                "authentication required"
                if decision.reason == REASON_DENY_ANONYMOUS
                else f"Scope denied for {operation_id} (requires one of {sorted(decision.required)})."
            )
            return {
                "response": {
                    "status": "error",
                    "message": msg,
                    "operation_id": operation_id,
                }
            }

        # allow
        return {}

    return permission_gate_node
