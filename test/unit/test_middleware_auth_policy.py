"""MTU 3 contract: api/middleware.py derives its public/gated path sets from the
HttpRouteRegistry, with the hardcoded tuples retained ONLY as a fallback.

Security invariant: an empty/unavailable registry must fall back to the baseline
tuples — never collapse to "nothing public / nothing gated".
"""

import pytest
import core.http_route_registry as hrr_mod
from api.middleware import (
    _public_prefixes,
    _gated_prefixes,
    _gated_exact,
    _FALLBACK_PUBLIC_PREFIXES,
    _FALLBACK_GATED_PREFIXES,
    _FALLBACK_GATED_EXACT,
    reset_route_policy_cache,
)


@pytest.fixture(autouse=True)
def clean_middleware_cache():
    reset_route_policy_cache()
    yield
    reset_route_policy_cache()



def test_fallback_constants_preserve_current_policy():
    assert "/.well-known/" in _FALLBACK_PUBLIC_PREFIXES
    assert "/admin/login" not in _FALLBACK_PUBLIC_PREFIXES
    assert "/api/" not in _FALLBACK_GATED_PREFIXES
    assert "/" in _FALLBACK_GATED_EXACT


def test_resolvers_read_from_seeded_registry():
    # core.context seeded the live singleton with the baseline policies.
    assert "/.well-known/" in _public_prefixes()
    assert "/plugins" in _gated_prefixes()
    assert "/" in _gated_exact()


class _EmptyReg:
    def public_prefixes(self):
        return ()

    def gated_prefixes(self):
        return ()

    def gated_exact(self):
        return ()


def test_empty_registry_falls_back_not_collapse(monkeypatch):
    monkeypatch.setattr(hrr_mod, "get_http_route_registry", lambda: _EmptyReg())
    assert _public_prefixes() == _FALLBACK_PUBLIC_PREFIXES
    assert _gated_prefixes() == _FALLBACK_GATED_PREFIXES
    assert _gated_exact() == _FALLBACK_GATED_EXACT


def test_registry_unavailable_falls_back(monkeypatch):
    def _boom():
        raise RuntimeError("not initialized")

    monkeypatch.setattr(hrr_mod, "get_http_route_registry", _boom)
    assert _public_prefixes() == _FALLBACK_PUBLIC_PREFIXES
    assert _gated_prefixes() == _FALLBACK_GATED_PREFIXES
    assert _gated_exact() == _FALLBACK_GATED_EXACT
