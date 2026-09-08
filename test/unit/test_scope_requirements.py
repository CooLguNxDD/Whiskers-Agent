"""Unit tests for core.scope_management.requirements — provider union + adapter parity."""

import pytest

from core.route_registry.operation_descriptor import AccessClass
from core.scope_management.request import AccessRequest
from core.scope_management.requirements import (
    RequirementResolver,
    resolve_requirements,
)
from core.scope_management.vocabulary import required_scopes_for_route


LEGACY_CASES = [
    ("jules_plugin", []),
    ("jules_plugin", ["sessions"]),
    ("jules_plugin", ["sessions", "activity"]),
    ("", []),
    ("", ["ignored"]),
]


@pytest.mark.parametrize("plugin_id,tags", LEGACY_CASES)
def test_vocabulary_adapter_matches_legacy_formula(plugin_id, tags):
    """required_scopes_for_route must stay byte-for-byte identical to the
    pre-Stage-1 hardcoded formula: {plugin:<id>} ∪ {group:<id>:<tag>}."""
    if not plugin_id:
        legacy = set()
    else:
        legacy = {f"plugin:{plugin_id}"} | {f"group:{plugin_id}:{t}" for t in tags}
    assert required_scopes_for_route(plugin_id, tags) == legacy


def test_plugin_provider_default_access_none_adds_no_extra_token():
    request = AccessRequest(plugin_id="p", tags=("t",))
    out = resolve_requirements(request)
    assert out == {"plugin:p", "group:p:t"}


def test_plugin_provider_with_access_adds_finer_token():
    request = AccessRequest(plugin_id="p", access=AccessClass.WRITE)
    out = resolve_requirements(request)
    assert "plugin:p" in out
    assert "plugin:p:write" in out


def test_operation_provider():
    request = AccessRequest(plugin_id="p", operation_id="do_thing")
    out = resolve_requirements(request)
    assert "op:p:do_thing" in out
    assert "plugin:p" in out


def test_core_domain_provider_defaults_to_read():
    request = AccessRequest(core_domain="whiskers.proxy")
    out = resolve_requirements(request)
    assert out == {"core:whiskers.proxy:read"}


def test_core_domain_provider_honours_access():
    request = AccessRequest(core_domain="whiskers.proxy", access=AccessClass.WRITE)
    out = resolve_requirements(request)
    assert out == {"core:whiskers.proxy:write"}


def test_empty_request_resolves_empty():
    assert resolve_requirements(AccessRequest()) == frozenset()


def test_register_provider_unions_with_builtins():
    resolver = RequirementResolver()
    resolver.register_provider("extra", lambda req: frozenset({"custom:token"}))
    request = AccessRequest(plugin_id="p")
    out = resolver.resolve(request)
    assert "plugin:p" in out
    assert "custom:token" in out


def test_register_provider_rejects_non_callable():
    resolver = RequirementResolver()
    with pytest.raises(TypeError):
        resolver.register_provider("bad", "not-callable")  # type: ignore[arg-type]
