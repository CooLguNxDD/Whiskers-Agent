import pytest
from unittest.mock import MagicMock
from langgraph.graph import END

from core_graph.node.context import GraphRuntimeContext
from core_graph.graph_spec import (
    GRAPH_SPEC,
    GraphSpec,
    NodeSpec,
    StaticEdge,
    ConditionalEdge,
    build_from_spec,
    resolve_topology,
    _validate,
)


@pytest.fixture
def mock_ctx():
    """Create a mock GraphRuntimeContext."""
    ctx = MagicMock(spec=GraphRuntimeContext)
    ctx.llm = MagicMock()
    ctx.api_url = "http://mock-api"
    ctx.context_params = {}
    return ctx


@pytest.fixture
def mock_graph():
    """Create a mock StateGraph."""
    graph = MagicMock()
    # Track calls to add_node, add_edge, add_conditional_edges
    graph.nodes = {}
    graph.edges = []
    graph.conditional_edges = []

    def add_node(name, node):
        graph.nodes[name] = node

    def add_edge(start, end):
        graph.edges.append((start, end))

    def add_conditional_edges(start, router, edge_map):
        graph.conditional_edges.append((start, router, edge_map))

    graph.add_node.side_effect = add_node
    graph.add_edge.side_effect = add_edge
    graph.add_conditional_edges.side_effect = add_conditional_edges
    return graph


@pytest.fixture
def legacy_flag(monkeypatch):
    """Turn the decompose-first flag OFF (legacy embedder-first wiring)."""
    monkeypatch.setattr("utils.server_config.DECOMPOSE_FIRST", False)


def test_build_from_spec_default(mock_graph, mock_ctx):
    build_from_spec(mock_graph, mock_ctx, GRAPH_SPEC)

    # 21 nodes: classic 20 + specialist_entry
    assert len(mock_graph.nodes) == 21
    assert "decompose" in mock_graph.nodes
    assert "round_summary" in mock_graph.nodes
    assert "specialist_entry" in mock_graph.nodes

    # assert ("decompose", "embedder") in edges
    assert ("decompose", "embedder") in mock_graph.edges
    # Loop: round_summary → goap_goal; final: summary_node → END; done → summary_node
    assert ("round_summary", "goap_goal") in mock_graph.edges
    assert ("summary_node", END) in mock_graph.edges
    assert ("summary_node", "goap_goal") not in mock_graph.edges

    # 13 conditional sources (12 classic + specialist_entry)
    cond_maps = {x[0]: x[2] for x in mock_graph.conditional_edges}
    assert len(cond_maps) == 13
    assert cond_maps["triage"] == {
        "chat": "chat_node",
        "classic": "decompose",
        "specialist": "specialist_entry",
        "task": "decompose",
    }
    assert cond_maps["specialist_entry"] == {"plan": "decompose", "done": "summary_node"}
    assert cond_maps["planner"] == {"context_check": "context_check", "confirm_node": "confirm_node", "clarify_node": "clarify_node", "done": END}
    assert cond_maps["confirm_node"] == {"execute": "context_check", "done": END}
    assert cond_maps["clarify_node"] == {"replan": "decompose", "done": END}
    assert cond_maps["context_check"] == {"builder": "permission_gate", "step_resolver": "step_resolver"}
    assert cond_maps["step_resolver"] == {"builder": "permission_gate", "done": END, "goap_goal": "goap_goal"}
    assert cond_maps["builder"] == {"executor": "executor", "wait_node": "wait_node"}
    assert cond_maps["validator"] == {"next_step": "step_dispatcher", "retry": "retry_node", "replan": "decompose", "halt": END, "recover": "goap_goal"}
    assert cond_maps["retry_node"] == {"builder": "permission_gate", "step_resolver": "step_resolver"}
    assert cond_maps["step_dispatcher"] == {"context_check": "context_check", "summary": "round_summary", "done": END, "recover": "goap_goal"}
    assert cond_maps["permission_gate"] == {"builder": "builder", "done": END}
    assert cond_maps["goap_goal"] == {
        "continue": "decompose",
        "done": "summary_node",
        "retriage": "triage",
    }


def test_build_from_spec_legacy(mock_graph, mock_ctx, legacy_flag):
    build_from_spec(mock_graph, mock_ctx, GRAPH_SPEC)

    # 20 nodes (no "decompose", + specialist_entry)
    assert len(mock_graph.nodes) == 20
    assert "decompose" not in mock_graph.nodes
    assert "round_summary" in mock_graph.nodes
    assert "specialist_entry" in mock_graph.nodes

    # no ("decompose", "embedder") edge
    assert ("decompose", "embedder") not in mock_graph.edges

    # assert conditional edges
    cond_maps = {x[0]: x[2] for x in mock_graph.conditional_edges}
    assert len(cond_maps) == 13
    assert cond_maps["triage"] == {
        "chat": "chat_node",
        "classic": "embedder",
        "specialist": "specialist_entry",
        "task": "embedder",
    }
    assert cond_maps["planner"] == {"context_check": "context_check", "confirm_node": "confirm_node", "clarify_node": "clarify_node", "done": END}
    assert cond_maps["confirm_node"] == {"execute": "context_check", "done": END}
    assert cond_maps["clarify_node"] == {"replan": "embedder", "done": END}
    assert cond_maps["context_check"] == {"builder": "permission_gate", "step_resolver": "step_resolver"}
    assert cond_maps["step_resolver"] == {"builder": "permission_gate", "done": END, "goap_goal": "goap_goal"}
    assert cond_maps["builder"] == {"executor": "executor", "wait_node": "wait_node"}
    assert cond_maps["validator"] == {"next_step": "step_dispatcher", "retry": "retry_node", "replan": "planner", "halt": END, "recover": "goap_goal"}
    assert cond_maps["retry_node"] == {"builder": "permission_gate", "step_resolver": "step_resolver"}
    assert cond_maps["step_dispatcher"] == {"context_check": "context_check", "summary": "round_summary", "done": END, "recover": "goap_goal"}
    assert cond_maps["permission_gate"] == {"builder": "builder", "done": END}
    assert cond_maps["goap_goal"] == {
        "continue": "embedder",
        "done": "summary_node",
        "retriage": "triage",
    }
    assert cond_maps["specialist_entry"] == {"plan": "embedder", "done": "summary_node"}


def test_validate_orphan_node():
    spec = GraphSpec(
        entry="node_a",
        nodes=(
            NodeSpec("node_a", "core_graph.node.make_turn_init_node"),
        ),
        static_edges=(
            StaticEdge("node_a", "node_b"),
        ),
        conditional_edges=(),
    )
    with pytest.raises(ValueError) as excinfo:
        _validate(resolve_topology(spec))
    assert "edge references undeclared node" in str(excinfo.value)
    assert "node_b" in str(excinfo.value)


def test_validate_unreachable_node():
    spec = GraphSpec(
        entry="node_a",
        nodes=(
            NodeSpec("node_a", "core_graph.node.make_turn_init_node"),
            NodeSpec("node_b", "core_graph.node.make_triage_node"),
        ),
        static_edges=(),
        conditional_edges=(),
    )
    with pytest.raises(ValueError) as excinfo:
        _validate(resolve_topology(spec))
    assert "unreachable nodes" in str(excinfo.value)
    assert "node_b" in str(excinfo.value)


def test_grap_spec_validates_both_flag_states(monkeypatch, legacy_flag):
    # legacy_flag fixture sets DECOMPOSE_FIRST to False
    topology_off = resolve_topology(GRAPH_SPEC)
    _validate(topology_off)

    # Explicitly set to True to test other branch
    monkeypatch.setattr("utils.server_config.DECOMPOSE_FIRST", True)
    topology_on = resolve_topology(GRAPH_SPEC)
    _validate(topology_on)


def test_factory_resolution_bad_path(mock_graph, mock_ctx):
    spec = GraphSpec(
        entry="node_a",
        nodes=(
            NodeSpec("node_a", "nonexistent.module.make_thing"),
        ),
        static_edges=(),
        conditional_edges=(),
    )
    with pytest.raises(ImportError) as excinfo:
        build_from_spec(mock_graph, mock_ctx, spec)
    assert "node_a" in str(excinfo.value)
    assert "nonexistent.module.make_thing" in str(excinfo.value)
