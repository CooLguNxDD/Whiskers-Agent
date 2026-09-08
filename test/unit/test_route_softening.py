import pytest
from core_graph.node.helpers import resolve_route_candidate
from core.route_registry.route_descriptor import RouteDescriptor


class FakeRouteRegistry:
    """A minimal mock for RouteRegistry supporting get()."""

    def __init__(self, descriptor=None):
        """Initialize with a predefined RouteDescriptor to return."""
        self.descriptor = descriptor
        self.last_op = None
        self.last_plugin = None

    def get(self, operation_id: str, plugin_id: str | None = None) -> RouteDescriptor | None:
        """Mock registry lookup returning the preconfigured descriptor."""
        self.last_op = operation_id
        self.last_plugin = plugin_id
        return self.descriptor


def test_prefers_candidates():
    """Verify that helper prefers turn's candidates and doesn't query registry."""
    candidate = {
        "operation_id": "test_op",
        "plugin_id": "test_plugin",
        "description": "test desc",
        "parameters": {},
        "method": "GET",
        "path_template": "test",
        "path": "test",
        "is_fast_path": False,
        "score": 0.95,
    }
    candidates = [candidate]

    res = resolve_route_candidate("test_op", "test_plugin", candidates, None)
    assert res == candidate


def test_registry_fallback():
    """Verify that helper falls back to RouteRegistry when op is missing from candidates."""
    desc = RouteDescriptor(
        plugin_id="proxy_X",
        operation_id="proxy_X__notion-fetch",
        description="fetch",
        parameters={"id": {"required": True}},
        method="CALL",
        path_template="notion-fetch",
        is_fast_path=True,
    )
    registry = FakeRouteRegistry(desc)

    res = resolve_route_candidate("proxy_X__notion-fetch", "proxy_X", [], registry)

    assert res is not None
    assert res["operation_id"] == "proxy_X__notion-fetch"
    assert res["plugin_id"] == "proxy_X"
    assert res["description"] == "fetch"
    assert res["parameters"] == {"id": {"required": True}}
    assert res["method"] == "CALL"
    assert res["path_template"] == "notion-fetch"
    assert res["path"] == "notion-fetch"
    assert res["is_fast_path"] is True
    assert res["score"] == 0.0

    assert registry.last_op == "proxy_X__notion-fetch"
    assert registry.last_plugin == "proxy_X"


def test_registry_miss_returns_none():
    """Verify that helper returns None when op is not in candidates and registry lookup misses."""
    registry = FakeRouteRegistry(None)
    res = resolve_route_candidate("missing_op", "plugin_X", [], registry)
    assert res is None
    assert registry.last_op == "missing_op"
    assert registry.last_plugin == "plugin_X"


def test_plugin_mismatch_returns_none():
    """Verify that helper returns None if registry returns a descriptor for a different plugin."""
    desc = RouteDescriptor(
        plugin_id="different_plugin",
        operation_id="proxy_X__notion-fetch",
        description="fetch",
        parameters={"id": {"required": True}},
        method="CALL",
        path_template="notion-fetch",
        is_fast_path=True,
    )
    registry = FakeRouteRegistry(desc)
    res = resolve_route_candidate("proxy_X__notion-fetch", "requested_plugin", [], registry)
    assert res is None
