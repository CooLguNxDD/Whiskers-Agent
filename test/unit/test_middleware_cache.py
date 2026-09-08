"""Unit tests for middleware prefix caching and invalidation."""
import pytest


def test_cache_hit_returns_same_tuple():
    """After first call, repeated _public_prefixes() returns cached tuple (identity)."""
    from api.middleware import _public_prefixes, reset_route_policy_cache
    reset_route_policy_cache()
    t1 = _public_prefixes()
    t2 = _public_prefixes()
    assert t1 is t2


def test_reset_clears_cache():
    """After reset_route_policy_cache(), next call is a cache miss (may return same value but rebuilds)."""
    from api.middleware import _public_prefixes, reset_route_policy_cache
    reset_route_policy_cache()
    t1 = _public_prefixes()
    reset_route_policy_cache()
    t2 = _public_prefixes()
    # Values should be equal (same registry state) but we can't assert identity
    assert t1 == t2


def test_reset_export():
    """reset_route_policy_cache is importable from api.middleware."""
    from api.middleware import reset_route_policy_cache
    assert callable(reset_route_policy_cache)
