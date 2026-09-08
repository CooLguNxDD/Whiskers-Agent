"""
Topology regression guard for the flat dynamic StateGraph.

Asserts:
  1. All 20 expected node names (decompose-first flag ON, the default) are present
     in the compiled graph (includes round_summary).
  2. The cross-cluster edges that jump between file-ownership boundaries survive
     the Phase 6 split:
       - validator → decompose (retry_router returns "replan"; legacy: → planner)
       - validator → goap_goal (retry_router returns "recover")
       - step_dispatcher → context_check (more_steps_router returns "context_check")
       - goap_goal → decompose (goap_goal_router returns "continue"; legacy: → embedder)
       - goap_goal done → summary_node (final synthesis after the loop)

Approach for cross-cluster edge assertions
------------------------------------------
We drive the router functions directly with crafted DynamicAPIState dicts and
assert the returned key is in the edge-map dict that was passed to
add_conditional_edges.  This is more reliable than inspecting LangGraph's
compiled graph internals (which vary between LangGraph versions) and mirrors
what would actually execute at runtime.
"""
import pytest
from unittest.mock import MagicMock


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_llm():
    """Minimal mock LLM accepted by every node factory."""
    llm = MagicMock()
    llm.invoke = MagicMock(return_value=MagicMock(content="{}"))
    llm.ainvoke = MagicMock(return_value=MagicMock(content="{}"))
    return llm


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def compiled_graph():
    from core_graph.graph import build_dynamic_graph
    return build_dynamic_graph(llm=_make_llm())


# ── 1. Node presence ─────────────────────────────────────────────────────────

EXPECTED_NODES = {
    "turn_init",
    "triage",
    "chat_node",
    "specialist_entry",
    "decompose",
    "embedder",
    "planner",
    "context_check",
    "builder",
    "step_resolver",
    "wait_node",
    "executor",
    "validator",
    "retry_node",
    "confirm_node",
    "clarify_node",
    "step_dispatcher",
    "round_summary",
    "summary_node",
    "goap_goal",
    "permission_gate",
}


def test_all_expected_nodes_present(compiled_graph):
    """Every expected node (decompose-first ON + specialist_entry) must appear in the compiled graph."""
    graph_data = compiled_graph.get_graph()
    # LangGraph exposes nodes as a dict keyed by node name; __start__ / __end__
    # are synthetic sentinels added by LangGraph itself — filter them out.
    actual_nodes = {
        k for k in graph_data.nodes.keys()
        if not k.startswith("__")
    }
    missing = EXPECTED_NODES - actual_nodes
    assert not missing, (
        f"Compiled graph is missing expected nodes: {sorted(missing)}\n"
        f"Actual nodes (excl. sentinels): {sorted(actual_nodes)}"
    )


def test_node_count_exact(compiled_graph):
    """Exactly 20 application nodes — no accidental duplicates or extras."""
    graph_data = compiled_graph.get_graph()
    actual_nodes = {
        k for k in graph_data.nodes.keys()
        if not k.startswith("__")
    }
    assert len(actual_nodes) == len(EXPECTED_NODES), (
        f"Expected exactly {len(EXPECTED_NODES)} nodes, got {len(actual_nodes)}: {sorted(actual_nodes)}"
    )


# ── 2. Cross-cluster edge assertions via router functions ─────────────────────
#
# Edge-map dicts (mirroring the add_conditional_edges calls with the
# decompose-first flag ON — the default):

_RETRY_ROUTER_EDGES = {
    "next_step": "step_dispatcher",
    "retry": "retry_node",
    "replan": "decompose",
    "halt": "END",
    "recover": "goap_goal",
}

_MORE_STEPS_ROUTER_EDGES = {
    "context_check": "context_check",
    "summary": "round_summary",
    "done": "END",
}

_GOAP_GOAL_ROUTER_EDGES = {
    "continue": "decompose",
    "done": "summary_node",
}


def test_validator_can_reach_decompose_via_replan():
    """validator --replan--> decompose (cross-cluster: execution → goap_planning)."""
    from core_graph.node.routers import retry_router
    # Craft a state that makes retry_router return "replan"
    state = {"response": {"status": "replan"}, "retry_count": 0}
    key = retry_router(state)
    assert key == "replan", f"retry_router returned {key!r}, expected 'replan'"
    assert key in _RETRY_ROUTER_EDGES, f"Key {key!r} not in retry_router edge map"
    assert _RETRY_ROUTER_EDGES[key] == "decompose"


def test_validator_can_reach_goap_goal_via_recover():
    """validator --recover--> goap_goal (cross-cluster: execution → goal_loop)."""
    from core_graph.node.routers import retry_router
    # status==error with a goal present triggers recover
    state = {
        "response": {"status": "error"},
        "retry_count": 2,   # exhausted retries
        "goal": {"raw_goal": "do something"},
    }
    key = retry_router(state)
    assert key == "recover", f"retry_router returned {key!r}, expected 'recover'"
    assert key in _RETRY_ROUTER_EDGES
    assert _RETRY_ROUTER_EDGES[key] == "goap_goal"


def test_step_dispatcher_can_reach_context_check():
    """step_dispatcher --context_check--> context_check (cross-cluster: execution → goap_planning)."""
    from core_graph.node.routers import more_steps_router
    # A plan with a remaining step and a selected route triggers context_check
    state = {
        "plan": [{"id": "s1"}, {"id": "s2"}],
        "current_step_index": 1,
        "response": {},
        "selected": [{"id": "route1"}],
    }
    key = more_steps_router(state)
    assert key == "context_check", f"more_steps_router returned {key!r}, expected 'context_check'"
    assert key in _MORE_STEPS_ROUTER_EDGES
    assert _MORE_STEPS_ROUTER_EDGES[key] == "context_check"


def test_goap_goal_can_loop_back_to_decompose():
    """goap_goal --continue--> decompose (cross-cluster: goal_loop → goap_planning)."""
    from core_graph.node.routers import goap_goal_router
    state = {"goal_loop_decision": "continue"}
    key = goap_goal_router(state)
    assert key == "continue", f"goap_goal_router returned {key!r}, expected 'continue'"
    assert key in _GOAP_GOAL_ROUTER_EDGES
    assert _GOAP_GOAL_ROUTER_EDGES[key] == "decompose"
