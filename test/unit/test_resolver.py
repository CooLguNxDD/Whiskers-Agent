import pytest
from pathlib import Path
from unittest.mock import patch

from core.plugin_loader.resolver import (
    PluginSpec,
    ResolvedPlan,
    _normalize_plugin_name,
    toposort,
    build_dag,
    filter_by_tier,
    reachable_from_roots,
    filter_missing_dependencies,
    resolve
)

def test_normalize_plugin_name():
    assert _normalize_plugin_name("plugins.fake_plugin") == "fake_plugin"
    assert _normalize_plugin_name("fake_plugin") == "fake_plugin"
    assert _normalize_plugin_name("records@>=1.0.0") == "records"
    assert _normalize_plugin_name("plugins.records@>=1.0.0") == "records"
    assert _normalize_plugin_name("") == ""


def test_toposort_diamond():
    # A -> B, A -> C
    # B -> D, C -> D
    # Order should load A, then (B, C) in some order, then D.
    dag = {
        "A": {"B", "C"},
        "B": {"D"},
        "C": {"D"},
        "D": set()
    }
    order = toposort(dag)
    assert order == ["A", "B", "C", "D"]


def test_toposort_cycle_raises_naming_nodes():
    # A -> B -> C -> A
    dag = {
        "A": {"B"},
        "B": {"C"},
        "C": {"A"}
    }
    with pytest.raises(ValueError) as excinfo:
        toposort(dag)
    assert "Circular plugin dependency detected among: A, B, C" in str(excinfo.value)


def test_filter_by_tier():
    specs = [
        PluginSpec("p1", "p1", 1, (), (), Path("p1"), {}),
        PluginSpec("p2", "p2", 100, (), (), Path("p2"), {}),
        PluginSpec("p3", "p3", 500, (), (), Path("p3"), {})
    ]
    
    # Under free tier (1)
    allowed, skipped = filter_by_tier(specs, 1)
    assert len(allowed) == 1
    assert allowed[0].package == "p1"
    assert len(skipped) == 2
    assert skipped[0] == ("p2", "Requires tier 100, system is 1")
    assert skipped[1] == ("p3", "Requires tier 500, system is 1")


def test_reachable_from_roots():
    specs = [
        PluginSpec("p1", "p1", 1, ("p2",), (), Path("p1"), {}),
        PluginSpec("p2", "p2", 1, (), (), Path("p2"), {}),
        PluginSpec("p3", "p3", 1, (), (), Path("p3"), {})
    ]
    
    # Root is p1, should pull p2 but skip p3
    allowed, skipped = reachable_from_roots(specs, {"p1"})
    allowed_names = {s.package for s in allowed}
    assert allowed_names == {"p1", "p2"}
    assert len(skipped) == 1
    assert skipped[0] == ("p3", "Not reachable from loading roots")


def test_filter_missing_dependencies_transitive():
    # p1 requires p2. p2 requires p3. p3 is missing.
    # Both p1 and p2 should be skipped transitively.
    specs = [
        PluginSpec("p1", "p1", 1, ("p2",), (), Path("p1"), {}),
        PluginSpec("p2", "p2", 1, ("p3",), (), Path("p3"), {})
    ]
    
    allowed, skipped = filter_missing_dependencies(specs)
    assert len(allowed) == 0
    assert len(skipped) == 2
    # Order in which they are skipped depends on iteration, but both must be in skipped list
    skipped_pkgs = {s[0] for s in skipped}
    assert skipped_pkgs == {"p1", "p2"}


def test_resolve_composition_diamond():
    # Build a full mock for resolve
    mock_specs = [
        PluginSpec("plugins.core", "core", 1, (), (), Path("core"), {}),
        PluginSpec("plugins.b", "b", 1, ("core",), (), Path("b"), {}),
        PluginSpec("plugins.c", "c", 1, ("core",), (), Path("c"), {}),
        PluginSpec("plugins.d", "d", 1, ("b", "c"), (), Path("d"), {})
    ]
    
    with patch("core.plugin_loader.resolver.build_specs") as mock_build:
        mock_build.return_value = (mock_specs, [])
        plan = resolve(["plugins.core", "plugins.b", "plugins.c", "plugins.d"], system_tier=1)
        
        assert len(plan.skipped) == 0
        order_names = [s.name for s in plan.order]
        assert order_names == ["core", "b", "c", "d"]
