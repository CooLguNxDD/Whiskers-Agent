"""Route scope vocabulary — required tokens and valid-scope union.

Reads plugin/proxy-registered tokens from the ``PermissionRegistry``
(``core.scope_management.registration``) — the dynamic front door for
scope vocab. ``core.plugin_loader.scope_registry`` is a back-compat shim
over the same registry.
"""


def get_valid_scopes() -> list[str]:
    """Static OAUTH_VALID_SCOPES floor ∪ dynamically registered scope tokens."""
    from utils.server_config import OAUTH_VALID_SCOPES
    from core.scope_management.registration import get_permission_registry

    return sorted(set(OAUTH_VALID_SCOPES) | set(get_permission_registry().all_tokens()))


def required_scopes_for_route(plugin_id: str, tags) -> set[str]:
    """Scope tokens that satisfy a step on this plugin/tag combination.

    A caller holding ANY of these tokens may run the step: the coarse
    ``plugin:<id>`` toggle, or any ``group:<id>:<tag>`` matching one of the
    route's tags.

    Thin adapter over ``requirements.py``'s ``RequirementResolver`` — kept as
    a function here (rather than inlined at call sites) so
    ``execute.py``/``operation_catalog.py``/``agent_loop/runner.py``/
    ``permission_gate.py`` do not churn. ``access`` is left unset on the
    built ``AccessRequest`` so the finer ``plugin:<id>:<access>`` token is
    never added here — byte-for-byte parity with the legacy formula.
    """
    from core.scope_management.request import AccessRequest
    from core.scope_management.requirements import resolve_requirements

    if not plugin_id:
        return set()
    request = AccessRequest(plugin_id=plugin_id, tags=tuple(tags or ()))
    return set(resolve_requirements(request))
