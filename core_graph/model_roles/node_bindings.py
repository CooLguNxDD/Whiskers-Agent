"""Declared graph-node -> model-role bindings — the frontend contract.

Which role a node resolves through is otherwise only knowable by reading the
string literal passed to ``run_role_ladder``/``ctx.llm_for_role`` inside that
node's module. That is fine for Python call sites but leaves the frontend
(``scripts/gen_graph_topology.py`` -> ``modelRoles.gen.ts``, the GOAP
playground's audit overlay) with nothing to bind a node id to a role id.

``NODE_ROLES`` is that binding, declared once. ``test/unit/test_node_role_bindings.py``
regex-scans the real call sites and asserts they match this map, so a future
cutover that adds/renames a role and forgets to update this file fails CI
instead of silently desyncing the generated frontend contract.

One documented exception: ``specialist_entry`` does not call
``run_role_ladder``/``llm_for_role`` at all. The specialist agent loop is
multi-step and stateful, so (per ``core_graph.agent_loop.spec.AgentSpec.model``
and ``core_graph.model_roles.selection.select_agent_model``) it resolves only
the *first eligible rung* of the ``specialist`` role via
``resolve_role_first_rung`` — a full ladder retry would re-execute
side-effecting tool calls. It is still declared here (so the frontend/config
UI can show it), but excluded from the strict call-site scan in the test.
"""

from __future__ import annotations

# Graph node id -> role_ids that node resolves through the model-role layer.
# Node ids match core_graph/subgraphs/root/graph_spec.py (and the generated
# frontend/cat-admin-frontend/.../graphTopology.gen.ts NodeId union).
NODE_ROLES: dict[str, tuple[str, ...]] = {
    "triage": ("triage",),
    "chat_node": ("chat",),
    "decompose": ("decompose",),
    "planner": ("planner_goal", "planner_linear"),
    "goap_goal": ("goal_verifier",),
    "summary_node": ("summary",),
    "embedder": ("reranker",),  # via core_graph/goap/reranker.py::llm_rerank
    "specialist_entry": ("specialist",),  # first-rung only — see module docstring
}

# Nodes whose binding above is not reachable via a scanned
# run_role_ladder(...)/llm_for_role(...) call-site literal (see module
# docstring for why). Kept out of the strict scan-equality assertion in
# test_node_role_bindings.py.
NON_LADDER_NODES: frozenset[str] = frozenset({"specialist_entry"})


def roles_for_node(node_id: str) -> tuple[str, ...]:
    """Role ids the given graph node resolves through, or an empty tuple."""
    return NODE_ROLES.get(node_id, ())


def nodes_for_role(role_id: str) -> tuple[str, ...]:
    """Graph node ids that resolve the given role id, in declaration order."""
    return tuple(node for node, roles in NODE_ROLES.items() if role_id in roles)
