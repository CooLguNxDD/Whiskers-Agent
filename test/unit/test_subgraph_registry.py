"""Unit tests for core_graph.subgraphs SubgraphRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core_graph.subgraphs.registry import (
    SubgraphSpec,
    clear_subgraphs,
    coerce_subgraph_spec,
    ensure_default_subgraphs,
    get_subgraph,
    get_subgraph_registry,
    list_subgraphs,
    register_subgraph,
    unregister_subgraph,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Isolate registry state per test."""
    clear_subgraphs()
    yield
    clear_subgraphs()


def test_register_get_list_unregister():
    spec = SubgraphSpec(id="custom_a", name="Custom A", description="test")
    register_subgraph(spec)
    assert get_subgraph("custom_a") is not None
    assert get_subgraph("custom_a").name == "Custom A"
    assert get_subgraph_registry().has("custom_a")
    ids = [s.id for s in list_subgraphs()]
    assert "custom_a" in ids
    assert unregister_subgraph("custom_a") is True
    assert get_subgraph("custom_a") is None
    assert unregister_subgraph("custom_a") is False


def test_register_replace_same_id():
    register_subgraph(SubgraphSpec(id="x", name="One"))
    register_subgraph(SubgraphSpec(id="x", name="Two"))
    assert get_subgraph("x").name == "Two"
    assert len([s for s in list_subgraphs() if s.id == "x"]) == 1


def test_register_requires_id():
    with pytest.raises(ValueError):
        register_subgraph(SubgraphSpec(id="", name="nope"))
    with pytest.raises(TypeError):
        get_subgraph_registry().register({"id": "not_a_spec"})  # type: ignore[arg-type]


def test_clear_subgraphs():
    register_subgraph(SubgraphSpec(id="z", name="Z"))
    clear_subgraphs()
    assert list_subgraphs() == []
    assert get_subgraph("z") is None


def test_ensure_default_subgraphs():
    ensure_default_subgraphs()
    ids = {s.id for s in list_subgraphs()}
    assert {
        "root",
        "triage",
        "classic_goap",
        "specialist",
        "oneshot_cli",
    }.issubset(ids)

    root = get_subgraph("root")
    assert root is not None
    assert root.graph_spec is not None
    assert root.entry == "turn_init"
    assert root.nodes is not None
    assert any(n.name == "specialist_entry" for n in root.nodes)

    # Idempotent re-seed keeps plugin extras
    register_subgraph(SubgraphSpec(id="plugin_extra", name="Extra"))
    ensure_default_subgraphs()
    assert get_subgraph("plugin_extra") is not None
    assert get_subgraph("root") is not None


def test_coerce_subgraph_spec_dict_and_plugin_id():
    spec = coerce_subgraph_spec(
        {"id": "from_dict", "name": "From Dict", "description": "d"},
        plugin_id="demo_plugin",
    )
    assert isinstance(spec, SubgraphSpec)
    assert spec.id == "from_dict"
    assert spec.metadata.get("plugin_id") == "demo_plugin"

    existing = SubgraphSpec(id="e", name="E", metadata={"k": 1})
    stamped = coerce_subgraph_spec(existing, plugin_id="p2")
    assert stamped.metadata.get("plugin_id") == "p2"
    assert stamped.metadata.get("k") == 1


def test_plugin_context_contribute_subgraph():
    from core.plugin_loader.plugin_context import PluginContext
    from core.plugin_loader.plugin_event_bus import PluginEventBus
    from core.plugin_loader.plugin_registry import PluginRegistry

    # Minimal registry with event wiring (no full FastMCP needed for contribute path)
    app = MagicMock()
    reg = PluginRegistry(app)
    ctx = PluginContext(app, reg.events, {}, plugin_id="portfolio_plugin", registry=reg)

    ensure_default_subgraphs()
    ctx.contribute_subgraph(
        {
            "id": "portfolio_specialist",
            "name": "Portfolio Specialist Domain Agent",
            "description": "test contribute",
            "metadata": {"domain": "portfolio"},
        }
    )
    sg = get_subgraph("portfolio_specialist")
    assert sg is not None
    assert sg.name == "Portfolio Specialist Domain Agent"
    assert sg.metadata.get("plugin_id") == "portfolio_plugin"
    assert sg.metadata.get("domain") == "portfolio"


def test_gen_topology_uses_registry_root():
    """Topology generator resolves GraphSpec from get_subgraph('root')."""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from gen_graph_topology import build_forced_on_topology, render_ts

    ensure_default_subgraphs()
    topo = build_forced_on_topology()
    assert "turn_init" in topo.nodes
    assert "specialist_entry" in topo.nodes
    assert topo.entry == "turn_init"

    ts = render_ts(topo)
    assert "AUTO-GENERATED" in ts
    assert "specialist_entry" in ts
    assert "GRAPH_ENTRY" in ts
