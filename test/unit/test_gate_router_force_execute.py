"""Unit tests for gate_router force_execute short-circuit."""

from core_graph.node.routers import gate_router


def test_gate_router_force_execute_skips_confirm():
    """force_execute=True bypasses confidence confirm even when gate_decision is confirm."""
    state = {
        "force_execute": True,
        "gate_decision": "confirm",
        "response": None,
    }
    assert gate_router(state) == "context_check"


def test_gate_router_force_false_routes_to_confirm():
    """Without force_execute, confirm decision goes to confirm_node."""
    state = {
        "force_execute": False,
        "gate_decision": "confirm",
        "response": None,
    }
    assert gate_router(state) == "confirm_node"


def test_gate_router_execute_decision():
    """gate_decision execute goes to context_check when not forced."""
    state = {
        "force_execute": False,
        "gate_decision": "execute",
        "response": None,
    }
    assert gate_router(state) == "context_check"


def test_gate_router_existing_response_done():
    """Pre-set response ends the turn before force routing."""
    state = {
        "force_execute": True,
        "gate_decision": "confirm",
        "response": {"status": "ok"},
    }
    assert gate_router(state) == "done"
