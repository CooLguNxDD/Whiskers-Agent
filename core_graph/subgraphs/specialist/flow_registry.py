"""Process-wide registry of plugin-declared FlowSpecs.

Data replacement for the pattern in ``plugins/portfolio_plugin/plugin_config.py``
(``portfolio_domain_claims`` + the LIFO ``_runners`` list in
``core_graph.subgraphs.specialist.registry``): flows are declared as JSON,
loaded by ``core.plugin_loader.plugin_registry`` via ``contribute_flow_spec``,
and selected here by ``FlowSpec.claims`` instead of a hand-written predicate.

``select_flow`` is consulted by ``specialist_entry`` *before* the legacy
``_runners`` LIFO and the generic ``run_specialist_pipeline`` fallback, so
adopting flows is additive — a plugin with no registered flows behaves exactly
as before.
"""

from __future__ import annotations

import logging

from core_graph.subgraphs.specialist.flow_spec import FlowSpec

logger = logging.getLogger("whiskers.core_graph.specialist.flow_registry")

# Synthetic catalog owner for one OperationDescriptor per registered flow, so
# a GOAP plan step can dispatch a specialist flow the same way it dispatches
# any other operation: execute_operation("specialist", flow_id, {...}).
_CATALOG_OWNER = "specialist"

_DEFAULT_FLOW_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"goal": {"type": "string"}},
    "required": ["goal"],
}


async def _dispatch_flow(flow_id: str, *, goal: str, **_extra) -> dict:
    """Catalog ``callable_ref`` shim: resolve tenant/scopes from context, run the flow.

    Threaded via ``core_graph.runtime.caller_scopes.resolve_caller_scopes``
    (no state to consult here — this is a synthetic GOAP op dispatch, not the
    ``specialist_entry`` triage path): the request-principal contextvar,
    stdio-unrestricted, or fail-closed. Extra kwargs beyond ``goal`` (any
    other ``inputs_schema`` properties) are folded onto the flow's blackboard
    as initial inputs.
    """
    from core_graph.subgraphs.specialist.flow_runner import run_flow

    flow = get_flow_registry().get(flow_id)
    if flow is None:
        return {"status": "error", "error": "flow_not_found", "flow_id": flow_id}

    try:
        from core.context import current_tenant_id

        raw_tid = current_tenant_id.get()
        tenant_id = int(raw_tid) if raw_tid is not None else 1
    except Exception:
        tenant_id = 1
    if tenant_id <= 0:
        tenant_id = 1

    from core_graph.runtime.caller_scopes import resolve_caller_scopes

    caller_scopes = resolve_caller_scopes()

    return await run_flow(
        flow, goal, tenant_id=tenant_id, caller_scopes=caller_scopes, inputs=_extra or None
    )


def _flow_to_operation(flow: FlowSpec):
    """Build the synthetic ``specialist/<flow_id>`` OperationDescriptor for ``flow``."""
    import functools

    from core.route_registry.operation_descriptor import AccessClass, OperationDescriptor, Visibility

    tags = tuple(f"goap_requires:{r}" for r in flow.requires) + tuple(
        f"goap_provides:{p}" for p in flow.provides
    ) + ("specialist_flow", f"owner:{flow.owner}")

    return OperationDescriptor(
        plugin_id=_CATALOG_OWNER,
        operation_id=flow.flow_id,
        description=flow.name or flow.flow_id,
        input_schema=flow.inputs_schema or dict(_DEFAULT_FLOW_INPUT_SCHEMA),
        access=AccessClass.WRITE,
        required_scopes=(),
        visibility=Visibility.AUTHENTICATED,
        tags=tags,
        is_fast_path=True,
        callable_ref=functools.partial(_dispatch_flow, flow.flow_id),
    )


class FlowRegistry:
    """In-memory map of flow_id -> FlowSpec, with owner-scoped bulk unregister.

    Every mutation republishes the full flow set into the ``OperationCatalog``
    under the synthetic ``specialist`` owner, so GOAP's planner and executor
    see registered flows as ordinary dispatchable operations
    (``specialist/<flow_id>``) without any change to ``execute_step`` or the
    planner's action-generation path.
    """

    def __init__(self) -> None:
        self._flows: dict[str, FlowSpec] = {}

    def _sync_catalog(self) -> None:
        try:
            from core.route_registry.operation_catalog import get_operation_catalog

            ops = [_flow_to_operation(f) for f in self._flows.values()]
            get_operation_catalog().publish_owner(_CATALOG_OWNER, ops)
        except Exception as exc:
            logger.warning("flow_registry: catalog sync failed: %s", exc)

    def register(self, spec: FlowSpec) -> FlowSpec:
        """Add or replace a flow by id. Later registration wins (hot-reload)."""
        if not isinstance(spec, FlowSpec):
            raise TypeError("Only FlowSpec instances can be registered.")
        if not spec.flow_id:
            raise ValueError("FlowSpec.flow_id is required")
        self._flows[spec.flow_id] = spec
        logger.info("flow registered: %s (owner=%s)", spec.flow_id, spec.owner)
        self._sync_catalog()
        return spec

    def get(self, flow_id: str) -> FlowSpec | None:
        return self._flows.get(flow_id)

    def list_flows(self, *, owner: str | None = None) -> list[FlowSpec]:
        """All registered flows, optionally filtered by owning plugin_id."""
        flows = sorted(self._flows.values(), key=lambda f: f.flow_id)
        if owner is None:
            return flows
        return [f for f in flows if f.owner == owner]

    def unregister(self, flow_id: str) -> bool:
        removed = self._flows.pop(flow_id, None) is not None
        if removed:
            self._sync_catalog()
        return removed

    def unregister_owner(self, owner: str) -> int:
        """Remove every flow contributed by ``owner`` (plugin unload/hot-swap)."""
        dead = [fid for fid, f in self._flows.items() if f.owner == owner]
        for fid in dead:
            self._flows.pop(fid, None)
        if dead:
            self._sync_catalog()
        return len(dead)

    def clear(self) -> None:
        """Remove all registrations (tests only)."""
        self._flows.clear()
        self._sync_catalog()

    def select_flow(self, goal: str, *, goal_class: str | None = None) -> FlowSpec | None:
        """Return the first registered flow whose ``claims`` matches.

        Deterministic order (sorted by flow_id) so selection is reproducible
        across runs — unlike the legacy LIFO domain-runner list, ordering here
        is not registration-order-dependent. Callers needing priority between
        overlapping claims should scope ``any_keywords``/``deny_keywords``
        narrowly rather than relying on registration order.
        """
        q = (goal or "").strip()
        if not q:
            return None
        for flow in self.list_flows():
            if flow.claims.matches(goal=q, goal_class=goal_class):
                return flow
        return None


_REGISTRY: FlowRegistry | None = None


def get_flow_registry() -> FlowRegistry:
    """Process-wide FlowRegistry singleton."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = FlowRegistry()
    return _REGISTRY


def register_flow(spec: FlowSpec) -> FlowSpec:
    """Register or replace a flow on the global registry."""
    return get_flow_registry().register(spec)


def get_flow(flow_id: str) -> FlowSpec | None:
    """Look up a flow by id on the global registry."""
    return get_flow_registry().get(flow_id)


def list_flows(*, owner: str | None = None) -> list[FlowSpec]:
    """List all registered flows (sorted by flow_id), optionally by owner."""
    return get_flow_registry().list_flows(owner=owner)


def unregister_flow(flow_id: str) -> bool:
    """Unregister a flow by id from the global registry."""
    return get_flow_registry().unregister(flow_id)


def unregister_owner(owner: str) -> int:
    """Unregister every flow contributed by ``owner``. Returns count removed."""
    return get_flow_registry().unregister_owner(owner)


def clear_flows() -> None:
    """Clear the global registry (tests only)."""
    get_flow_registry().clear()


def select_flow(goal: str, *, goal_class: str | None = None) -> FlowSpec | None:
    """Select the first registered flow that claims ``goal`` — see ``FlowRegistry.select_flow``."""
    return get_flow_registry().select_flow(goal, goal_class=goal_class)
