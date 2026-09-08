"""Boot-time scope health self-test."""

from __future__ import annotations

import logging

logger = logging.getLogger("whiskers")


def run_boot_scope_health() -> list[str]:
    """Run C01–C05 self-tests + vocabulary non-empty check.

    Returns a list of warning strings (empty = healthy).
    """
    warnings: list[str] = []
    try:
        from core.scope_management import (
            ScopeGrant,
            Scope,
            evaluate_access,
            get_valid_scopes,
            PrincipalKind,
        )
        from core.scope_management.contracts import ACCESS_PATH_CONTRACTS
        from core.scope_management.policy import get_enforcement_mode, MODE_ENFORCE

        valid = get_valid_scopes()
        if not valid:
            warnings.append("get_valid_scopes() is empty at boot")

        mode = get_enforcement_mode()
        if mode != MODE_ENFORCE:
            warnings.append(f"scope_enforcement_mode is {mode!r} (expected enforce)")

        for row in ACCESS_PATH_CONTRACTS:
            if row["id"] not in ("C01", "C02", "C03", "C04", "C05"):
                continue
            p = row["principal"]
            a = row["action"]
            grant = ScopeGrant(
                scopes=p["scopes"],
                role=p.get("role"),
                kind=p["kind"],
            )
            decision = evaluate_access(
                grant,
                plugin_id=a.get("plugin_id") or "",
                tags=a.get("tags"),
                tool_name=a.get("tool_name") or "",
                path=a.get("path") or "",
            )
            want_allow = row["expected"] == "allow"
            if decision.allowed != want_allow:
                warnings.append(
                    f"contract {row['id']} expected allowed={want_allow} "
                    f"got allowed={decision.allowed} reason={decision.reason}"
                )

        # Explicit admin + all bypass smoke
        admin_d = evaluate_access(
            ScopeGrant(scopes=[Scope.ADMIN], role=None, kind=PrincipalKind.API_KEY),
            plugin_id="any_plugin",
            tags=["x"],
        )
        if not admin_d.allowed:
            warnings.append("admin bypass not honored at boot")
        all_d = evaluate_access(
            ScopeGrant(scopes=[Scope.ALL], role=None, kind=PrincipalKind.API_KEY),
            plugin_id="any_plugin",
            tags=["x"],
        )
        if not all_d.allowed:
            warnings.append("all/* bypass not honored at boot")

        # C13-C20 addition: one core-scope contract (level-1 vocab seeded +
        # reachable) and one gate contract (ceiling denies a scoped
        # principal, absent-gate stays unconstrained).
        from core.scope_management.request import AccessRequest

        if "core:graph:read" not in valid:
            warnings.append("core:graph:read missing from vocabulary at boot (core_scopes.json not seeded)")

        core_d = evaluate_access(
            ScopeGrant(scopes=["core:graph:read"], role=None, kind=PrincipalKind.API_KEY),
            request=AccessRequest(core_domain="graph"),
            required={"core:graph:read"},
        )
        if not core_d.allowed:
            warnings.append(f"core:graph:read self-grant denied at boot (reason={core_d.reason})")

        from core.scope_management.gates import get_plugin_gate_registry

        unconstrained_d = evaluate_access(
            ScopeGrant(scopes=["plugin:__health_check_plugin__"], role=None, kind=PrincipalKind.API_KEY),
            request=AccessRequest(plugin_id="__health_check_plugin__", operation_id="anything"),
            required={"plugin:__health_check_plugin__"},
        )
        if not unconstrained_d.allowed:
            warnings.append("absent-gate plugin request denied at boot (should be unconstrained)")
        if get_plugin_gate_registry().get_gate("__health_check_plugin__") is not None:
            warnings.append("health-check sentinel plugin unexpectedly has a registered gate")

        from core.plugin_loader.scope_registry import synthetic_scope_plugin_ids

        synthetic_ids = synthetic_scope_plugin_ids()
        if synthetic_ids:
            warnings.append(
                "scopes_synthetic: plugin(s) running on the fallback scope "
                f"floor (no manifest 'scopes' declared): {', '.join(synthetic_ids)}"
            )

    except Exception as exc:
        warnings.append(f"scope health self-test error: {exc}")

    if warnings:
        for w in warnings:
            logger.warning("phase_scope_health: %s", w)
    else:
        logger.info("phase_scope_health: C01–C05 + bypass checks OK")
    return warnings
