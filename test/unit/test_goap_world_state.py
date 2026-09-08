"""
Unit tests for the GOAP WorldState primitive.
"""
from core_graph.goap.world_state import WorldState


def test_empty_world_state():
    """
    Test that an empty WorldState behaves correctly.
    """
    ws = WorldState()
    assert ws.satisfies([]) is True
    assert ws.satisfies(set()) is True
    assert ws.has("x") is False
    assert ws.facts == frozenset()


def test_satisfies_and_missing():
    """
    Test that satisfies() and missing() return correct values.
    """
    ws = WorldState(["have:record_id", "have:message_body"])

    # Fully satisfied
    assert ws.satisfies(["have:record_id"]) is True
    assert ws.satisfies(["have:record_id", "have:message_body"]) is True
    assert ws.satisfies([]) is True

    # Not satisfied
    assert ws.satisfies(["have:record_id", "have:token"]) is False
    assert ws.satisfies(["have:token"]) is False

    # Missing subset
    assert ws.missing(["have:record_id"]) == set()
    assert ws.missing(["have:record_id", "have:token"]) == {"have:token"}
    assert ws.missing(["have:token", "have:other"]) == {"have:token", "have:other"}
    assert ws.missing([]) == set()


def test_apply_immutability():
    """
    Test that apply() is non-mutating and returns a correct new instance.
    """
    ws1 = WorldState(["have:record_id"])
    ws2 = ws1.apply(["have:message_body"])

    # Check identity and immutability
    assert ws1 is not ws2
    assert ws1.facts == frozenset(["have:record_id"])

    # Check new state facts
    assert ws2.facts == frozenset(["have:record_id", "have:message_body"])

    # Applying empty effects
    ws3 = ws1.apply([])
    assert ws3 is not ws1
    assert ws3.facts == ws1.facts


def test_equality_and_hash():
    """
    Test equality, hashing, and deduplication behavior.
    """
    ws1 = WorldState(["have:record_id", "have:message_body"])
    ws2 = WorldState(["have:message_body", "have:record_id"])
    ws3 = WorldState(["have:record_id"])

    # Equal states
    assert ws1 == ws2
    assert hash(ws1) == hash(ws2)

    # Unequal states
    assert ws1 != ws3
    assert ws1 != "not_a_world_state"

    # Set deduplication
    states_set = {ws1, ws2, ws3}
    assert len(states_set) == 2
    assert ws1 in states_set
    assert ws3 in states_set


def test_facts_type():
    """
    Test that facts property returns a frozenset.
    """
    ws = WorldState(["have:record_id"])
    assert isinstance(ws.facts, frozenset)
