import pytest
from core.route_registry import RouteRegistry
from core.route_registry.route_descriptor import RouteDescriptor, InstanceBinding

def _mock_desc(plugin_id: str, op_id: str, is_fast=False, ref=None):
    return RouteDescriptor(
        plugin_id=plugin_id,
        operation_id=op_id,
        description="Mock description",
        method="GET",
        path_template="/test",
        is_fast_path=is_fast,
        callable_ref=ref,
        tags=()
    )

def test_contribute_adds_routes():
    reg = RouteRegistry()
    reg.contribute([_mock_desc("p1", "op1"), _mock_desc("p1", "op2")])
    assert len(reg) == 2

def test_contribute_deduplicates_same_key():
    reg = RouteRegistry()
    reg.contribute([_mock_desc("p1", "op1"), _mock_desc("p1", "op1")])
    assert len(reg) == 1

def test_contribute_skips_empty_plugin_id():
    reg = RouteRegistry()
    reg.contribute([_mock_desc("", "op1")])
    assert len(reg) == 0

def test_get_by_plugin_and_operation():
    reg = RouteRegistry()
    reg.contribute([_mock_desc("p1", "op1")])
    desc = reg.get("op1", "p1")
    assert desc is not None
    assert desc.operation_id == "op1"

def test_get_returns_none_for_missing():
    reg = RouteRegistry()
    assert reg.get("op1", "p1") is None

def test_fast_path_callable_returns_callable_ref():
    reg = RouteRegistry()
    def fn(): pass
    reg.contribute([_mock_desc("p1", "op1", is_fast=True, ref=fn)])
    
    assert reg.fast_path_callable("op1", "p1") == fn

def test_fast_path_callable_returns_none_for_non_fast_path():
    reg = RouteRegistry()
    def fn(): pass
    reg.contribute([_mock_desc("p1", "op1", is_fast=False, ref=fn)])
    
    assert reg.fast_path_callable("op1", "p1") is None

def test_fast_path_callable_prefers_binding_override():
    reg = RouteRegistry()
    def orig_fn(): pass
    def override_fn(): pass
    
    reg.contribute([_mock_desc("p1", "op1", is_fast=True, ref=orig_fn)])
    binding = InstanceBinding(plugin_id="p1", instance_id="default", callable_override=override_fn)
    reg.register_binding(binding)
    
    assert reg.fast_path_callable("op1", "p1", "default") == override_fn

def test_register_binding_roundtrip():
    reg = RouteRegistry()
    def fn(): pass
    binding = InstanceBinding(plugin_id="p1", instance_id="default", callable_override=fn)
    reg.register_binding(binding)
    
    retrieved = reg.get_binding("p1", "default")
    assert retrieved is binding

def test_remove_plugin_clears_all():
    reg = RouteRegistry()
    reg.contribute([_mock_desc("p1", "op1"), _mock_desc("p2", "op1")])
    def fn(): pass
    reg.register_binding(InstanceBinding(plugin_id="p1", instance_id="default", callable_override=fn))
    
    reg.remove_plugin("p1")
    
    assert len(reg) == 1
    assert reg.get("op1", "p1") is None
    assert reg.get_binding("p1", "default") is None

def test_all_routes_returns_copy():
    reg = RouteRegistry()
    reg.contribute([_mock_desc("p1", "op1")])
    routes = reg.all_routes()
    routes.append(_mock_desc("p1", "op2"))
    
    assert len(reg) == 1

def test_clear():
    reg = RouteRegistry()
    reg.contribute([_mock_desc("p1", "op1")])
    reg.register_binding(InstanceBinding(plugin_id="p1", instance_id="default"))
    assert len(reg) == 1
    assert reg.get_binding("p1") is not None
    
    reg.clear()
    assert len(reg) == 0
    assert reg.get_binding("p1") is None

