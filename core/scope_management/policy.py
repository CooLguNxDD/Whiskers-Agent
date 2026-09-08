"""Scope enforcement enablement and mode overlay (enforce | audit | off)."""

from __future__ import annotations

import logging
import os

from core.scope_management.principal import AccessDecision

_logger = logging.getLogger("whiskers")
_scope_off_logged = False
_mode_warn_logged = False

# Valid mode strings
MODE_ENFORCE = "enforce"
MODE_AUDIT = "audit"
MODE_OFF = "off"


def scope_enforcement_enabled() -> bool:
    """Return False when plugin/group/terminal scope gating is disabled.

    Legacy boolean path: env ``SCOPE_ENFORCEMENT_OFF`` or config
    ``security.scope_enforcement_enabled=false``.

    Mode path (Phase 2): ``scope_enforcement_mode=off`` requires BOTH config
    value ``off`` AND env ``SCOPE_ENFORCEMENT_OFF=1`` (double-key).
    """
    global _scope_off_logged
    import utils.server_config

    # Double-key off: config mode "off" alone is not enough.
    mode = _raw_mode()
    if mode == MODE_OFF:
        if os.environ.get("SCOPE_ENFORCEMENT_OFF", "").strip().lower() in ("1", "true", "yes"):
            if not _scope_off_logged:
                _logger.warning(
                    "SCOPE_ENFORCEMENT_OFF is set with scope_enforcement_mode=off — "
                    "OAuth/API-key scope checks are disabled."
                )
                _scope_off_logged = True
            return False
        # Config says off but env missing → still enforce
        return True

    if os.environ.get("SCOPE_ENFORCEMENT_OFF", "").strip().lower() in ("1", "true", "yes"):
        if not _scope_off_logged:
            _logger.warning(
                "SCOPE_ENFORCEMENT_OFF is set — OAuth/API-key scope checks are disabled."
            )
            _scope_off_logged = True
        return False

    enabled = bool(getattr(utils.server_config, "SCOPE_ENFORCEMENT_ENABLED", True))
    if not enabled and not _scope_off_logged:
        _logger.warning(
            "security.scope_enforcement_enabled=false — OAuth/API-key scope checks are disabled."
        )
        _scope_off_logged = True
    return enabled


def _raw_mode() -> str:
    """Read configured enforcement mode (default enforce)."""
    import utils.server_config
    global _mode_warn_logged

    mode = getattr(utils.server_config, "SCOPE_ENFORCEMENT_MODE", None)
    if mode is None:
        # Fallback: read from SECURITY_CONFIG if constant not yet wired
        sec = getattr(utils.server_config, "SECURITY_CONFIG", {}) or {}
        mode = sec.get("scope_enforcement_mode", MODE_ENFORCE)
    mode = str(mode or MODE_ENFORCE).strip().lower()
    if mode not in (MODE_ENFORCE, MODE_AUDIT, MODE_OFF):
        if not _mode_warn_logged:
            _logger.warning("Invalid scope_enforcement_mode: %r — falling back to 'enforce'", mode)
            _mode_warn_logged = True
        return MODE_ENFORCE
    return mode


def get_enforcement_mode() -> str:
    """Resolved effective mode: enforce | audit | off (off only when double-keyed)."""
    mode = _raw_mode()
    if mode == MODE_OFF:
        if os.environ.get("SCOPE_ENFORCEMENT_OFF", "").strip().lower() in ("1", "true", "yes"):
            return MODE_OFF
        return MODE_ENFORCE
    return mode


def apply_mode(decision: AccessDecision) -> AccessDecision:
    """Overlay enforcement mode on a raw rule decision.

    - enforce: return decision as-is
    - audit: denials become allowed with reason audit_allow (logged)
    - off: always allow (handled earlier via scope_enforcement_enabled, but
      also coerce here for safety)
    """
    global _mode_warn_logged
    mode = get_enforcement_mode()

    if mode == MODE_OFF:
        if not decision.allowed:
            return AccessDecision(True, "bypass_enforcement_off", decision.required)
        return decision

    if mode == MODE_AUDIT and not decision.allowed:
        _logger.warning(
            "scope audit_allow: would deny reason=%s required=%s",
            decision.reason,
            sorted(decision.required),
        )
        return AccessDecision(True, "audit_allow", decision.required)

    return decision
