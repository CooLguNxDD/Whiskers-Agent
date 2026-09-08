"""ROOT_SPEC composition from triage + classic + specialist fragments."""

from __future__ import annotations

from core_graph.graph_spec import resolve_topology
from core_graph.subgraphs.root.graph_spec import ROOT_SPEC


def test_root_spec_includes_specialist_entry():
    names = {n.name for n in ROOT_SPEC.nodes}
    assert "turn_init" in names
    assert "triage" in names
    assert "specialist_entry" in names
    assert "goap_goal" in names


def test_root_topology_reachable():
    topo = resolve_topology(ROOT_SPEC)
    assert topo.entry == "turn_init"
    assert "specialist_entry" in topo.nodes
    assert "goap_goal" in topo.nodes
