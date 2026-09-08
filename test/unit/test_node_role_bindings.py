"""Anti-rot guard for core_graph.model_roles.node_bindings.NODE_ROLES.

The frontend's generated modelRoles.gen.ts (scripts/gen_graph_topology.py)
derives node->role chips purely from NODE_ROLES. If a future cutover adds a
new run_role_ladder(...)/llm_for_role(...) call site (or renames a role_id)
without updating NODE_ROLES, the frontend contract silently desyncs from
reality. This test makes that a CI failure instead.
"""

from __future__ import annotations

import re
from pathlib import Path

from core_graph.model_roles.node_bindings import (
    NODE_ROLES,
    NON_LADDER_NODES,
    nodes_for_role,
    roles_for_node,
)
from core_graph.model_roles.registry import get_model_role

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_PATTERN = re.compile(r'(?:run_role_ladder|llm_for_role)\(\s*(?:role\s*=\s*)?"([a-zA-Z_]+)"')


def _scan_role_literals() -> set[str]:
    files = list((_REPO_ROOT / "core_graph" / "node").glob("*.py"))
    files.append(_REPO_ROOT / "core_graph" / "goap" / "reranker.py")
    found: set[str] = set()
    for f in files:
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        found.update(_SCAN_PATTERN.findall(text))
    return found


def test_every_declared_role_resolves():
    for roles in NODE_ROLES.values():
        for role_id in roles:
            assert get_model_role(role_id) is not None, f"undeclared role_id: {role_id}"


def test_every_declared_node_is_a_real_graph_node():
    from core_graph.subgraphs.root.graph_spec import ROOT_SPEC

    real_node_ids = {n.name for n in ROOT_SPEC.nodes}
    for node_id in NODE_ROLES:
        assert node_id in real_node_ids, f"NODE_ROLES has a node_id not in ROOT_SPEC: {node_id}"


def test_scanned_ladder_call_sites_match_declared_bindings():
    scanned = _scan_role_literals()
    declared = {
        role_id
        for node_id, roles in NODE_ROLES.items()
        if node_id not in NON_LADDER_NODES
        for role_id in roles
    }
    assert scanned == declared, (
        f"NODE_ROLES out of sync with real run_role_ladder/llm_for_role call sites.\n"
        f"scanned only: {scanned - declared}\ndeclared only: {declared - scanned}"
    )


def test_roles_for_node_and_nodes_for_role_round_trip():
    assert roles_for_node("triage") == ("triage",)
    assert roles_for_node("nonexistent_node") == ()

    assert "triage" in nodes_for_role("triage")
    assert nodes_for_role("planner_goal") == ("planner",)
    assert nodes_for_role("nonexistent_role") == ()


def test_non_ladder_nodes_are_a_subset_of_node_roles():
    assert NON_LADDER_NODES <= set(NODE_ROLES.keys())
