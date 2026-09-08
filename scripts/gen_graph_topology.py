#!/usr/bin/env python3
"""
Code generator to export the backend graph topology (nodes, entry, edges, routers)
into a TypeScript definition for frontend visualization.

Resolves topology from the SubgraphRegistry root entry after seeding defaults.
"""

import sys
from pathlib import Path

# Add repo root to sys.path to support importing core_graph/utils when run directly on host
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def _root_graph_spec():
    """Seed default subgraphs and return the registered root GraphSpec."""
    from core_graph.subgraphs.registry import ensure_default_subgraphs, get_subgraph

    ensure_default_subgraphs()
    root = get_subgraph("root")
    if root is None or root.graph_spec is None:
        # Back-compat fallback if registry was cleared mid-run
        from core_graph.subgraphs.root.graph_spec import ROOT_SPEC

        return ROOT_SPEC
    return root.graph_spec


def build_forced_on_topology():
    """
    Force DECOMPOSE_FIRST flag ON in utils.server_config and resolve the graph topology.
    """
    import utils.server_config
    utils.server_config.DECOMPOSE_FIRST = True

    from core_graph.graph_spec import resolve_topology

    return resolve_topology(_root_graph_spec())


def render_ts(topology) -> str:
    """
    Pure function rendering TypeScript code from a ResolvedTopology object.
    """
    graph_spec = _root_graph_spec()

    # 1. Gather active node IDs in graph_spec.nodes declaration order
    active_nodes = [
        node.name for node in graph_spec.nodes
        if node.name in topology.nodes
    ]

    node_id_lines = []
    for name in active_nodes:
        node_id_lines.append(f'  | "{name}"')
    node_id_union = "\n".join(node_id_lines) + ";"

    # 2. Map routers (nodes that appear as a source in conditional_edges)
    router_nodes = {ce.source for ce in graph_spec.conditional_edges}

    graph_nodes_lines = []
    for name in active_nodes:
        is_router = name in router_nodes
        router_str = "true" if is_router else "false"
        graph_nodes_lines.append(f'  {{ id: "{name}", router: {router_str} }},')
    graph_nodes_content = "\n".join(graph_nodes_lines)

    # 3. Map edges (static first, then conditional)
    graph_edges_lines = []
    for source, target, conditional in topology.edges:
        cond_str = "true" if conditional else "false"
        graph_edges_lines.append(
            f'  {{ source: "{source}", target: "{target}", conditional: {cond_str} }},'
        )
    graph_edges_content = "\n".join(graph_edges_lines)

    ts_code = f"""// AUTO-GENERATED from core_graph subgraph registry (root GraphSpec) — DO NOT EDIT.
// Regenerate: python scripts/gen_graph_topology.py
export type NodeId =
{node_id_union}

export const GRAPH_ENTRY: NodeId = "turn_init";

export const GRAPH_NODES: {{ id: NodeId; router: boolean }}[] = [
{graph_nodes_content}
];

export const GRAPH_EDGES: {{ source: NodeId; target: NodeId; conditional: boolean }}[] = [
{graph_edges_content}
];
"""
    return ts_code


def _ts_string_literal(s: str) -> str:
    """Escape a Python string into a double-quoted TypeScript string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_model_roles_ts() -> str:
    """
    Pure-ish function rendering modelRoles.gen.ts: the core role_id union,
    effort/alias vocabulary, and the declared node->role bindings
    (core_graph.model_roles.node_bindings.NODE_ROLES). Imports NodeId from the
    sibling graphTopology.gen.ts so a NODE_ROLES key that isn't a real node id
    is a TypeScript compile error, not a silent drift.
    """
    from core_graph.model_roles.builtin import load_builtin_roles
    from core_graph.model_roles.node_bindings import NODE_ROLES
    from core_graph.model_roles.registry import _reset_model_roles_for_tests, list_model_roles
    from core_graph.model_roles.resolver import _ALIASES_NON_CORE
    from core_graph.model_roles.role_spec import _EFFORTS

    # Fresh, core-only registry (no DB overlay / plugin contributions) so the
    # generated file reflects the shipped defaults, not this process's state.
    _reset_model_roles_for_tests()
    load_builtin_roles()
    roles = list_model_roles()

    role_id_union = "\n".join(f"  | {_ts_string_literal(r.role_id)}" for r in roles) + ";"

    core_roles_lines = "\n".join(
        f'  {{ id: {_ts_string_literal(r.role_id)}, description: {_ts_string_literal(r.description)} }},'
        for r in roles
    )

    effort_levels = ", ".join(_ts_string_literal(e) for e in sorted(_EFFORTS))
    selector_aliases = ", ".join(
        _ts_string_literal(a) for a in (["core"] + sorted(_ALIASES_NON_CORE))
    )

    node_roles_lines = "\n".join(
        f"  {_ts_string_literal(node_id)}: [{', '.join(_ts_string_literal(r) for r in role_ids)}],"
        for node_id, role_ids in NODE_ROLES.items()
    )

    return f"""// AUTO-GENERATED from core_graph/model_roles/ — DO NOT EDIT.
// Regenerate: python scripts/gen_graph_topology.py
import type {{ NodeId }} from "./graphTopology.gen";

export type RoleId =
{role_id_union}

export const EFFORT_LEVELS = [{effort_levels}] as const;
export const SELECTOR_ALIASES = [{selector_aliases}] as const;

export const CORE_ROLES: {{ id: RoleId; description: string }}[] = [
{core_roles_lines}
];

export const NODE_ROLES: Partial<Record<NodeId, RoleId[]>> = {{
{node_roles_lines}
}};
"""


def main():
    """
    Generates and writes the TypeScript topology + model-role files.
    """
    topology = build_forced_on_topology()
    ts_content = render_ts(topology)
    model_roles_ts = render_model_roles_ts()

    out_dir = project_root / "frontend" / "cat-admin-frontend" / "src" / "components" / "goap"
    out_path = out_dir / "graphTopology.gen.ts"
    model_roles_path = out_dir / "modelRoles.gen.ts"
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", newline="\n", encoding="utf-8") as f:
        f.write(ts_content)
    with open(model_roles_path, "w", newline="\n", encoding="utf-8") as f:
        f.write(model_roles_ts)

    print(f"Successfully generated graph topology to: {out_path}")
    print(f"Active nodes: {len(topology.nodes)}")
    print(f"Total edges: {len(topology.edges)}")
    print(f"Successfully generated model roles to: {model_roles_path}")


if __name__ == "__main__":
    main()
