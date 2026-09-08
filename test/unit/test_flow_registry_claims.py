"""Unit tests for flow_registry selection semantics.

Table-driven against the same keyword set portfolio_domain_claims uses today
(``plugins/portfolio_plugin/plugin_config.py::_PORTFOLIO_GOAL_KEYS``), so the
declarative ``claims`` rule is provably equivalent for the load-bearing decline
case: a bare "discover"/"layout" ask must NOT be claimed without an explicit
portfolio signal.
"""

from __future__ import annotations

import pytest

from core_graph.subgraphs.specialist.flow_registry import (
    clear_flows,
    get_flow_registry,
    register_flow,
    select_flow,
    unregister_owner,
)
from core_graph.subgraphs.specialist.flow_spec import parse_flow_spec

PORTFOLIO_FLOW = {
    "flow_id": "portfolio_bake_v1",
    "name": "Portfolio Bake",
    "claims": {
        "goal_classes": ["bake_for_job", "redesign"],
        "any_keywords": [
            "portfolio", "portfolio layout", "genui", "redesign", "bake",
            "short_id", "star story", "project grid", "catportfolio",
            "resume site", "discover portfolio", "reindex portfolio", "reindex",
        ],
    },
    "stages": [{"id": "s1", "kind": "deterministic", "op": "portfolio_plugin/bake"}],
}


@pytest.fixture(autouse=True)
def _clean():
    clear_flows()
    yield
    clear_flows()


def test_register_and_get():
    spec = parse_flow_spec(PORTFOLIO_FLOW, owner="portfolio_plugin")
    register_flow(spec)
    assert get_flow_registry().get("portfolio_bake_v1") is not None


def test_select_flow_claims_bake_goal_class():
    register_flow(parse_flow_spec(PORTFOLIO_FLOW, owner="portfolio_plugin"))
    flow = select_flow("do the thing", goal_class="bake_for_job")
    assert flow is not None
    assert flow.flow_id == "portfolio_bake_v1"


def test_select_flow_claims_keyword_match():
    register_flow(parse_flow_spec(PORTFOLIO_FLOW, owner="portfolio_plugin"))
    flow = select_flow("please redesign my portfolio", goal_class=None)
    assert flow is not None


@pytest.mark.parametrize(
    "goal",
    ["discover", "discover something", "layout", "what is a layout"],
)
def test_bare_discover_and_layout_not_claimed(goal):
    """Load-bearing decline: no goal_class + no explicit portfolio signal -> None."""
    register_flow(parse_flow_spec(PORTFOLIO_FLOW, owner="portfolio_plugin"))
    assert select_flow(goal, goal_class=None) is None


def test_discover_with_portfolio_signal_claimed():
    register_flow(parse_flow_spec(PORTFOLIO_FLOW, owner="portfolio_plugin"))
    assert select_flow("discover portfolio context", goal_class=None) is not None


def test_unrelated_goal_not_claimed():
    register_flow(parse_flow_spec(PORTFOLIO_FLOW, owner="portfolio_plugin"))
    assert select_flow("send an email to bob", goal_class=None) is None


def test_unregister_owner_removes_all_its_flows():
    register_flow(parse_flow_spec(PORTFOLIO_FLOW, owner="portfolio_plugin"))
    register_flow(parse_flow_spec({**PORTFOLIO_FLOW, "flow_id": "portfolio_redesign_v1"}, owner="portfolio_plugin"))
    register_flow(parse_flow_spec({**PORTFOLIO_FLOW, "flow_id": "other_flow", "claims": {}}, owner="other_plugin"))
    assert unregister_owner("portfolio_plugin") == 2
    assert get_flow_registry().get("portfolio_bake_v1") is None
    assert get_flow_registry().get("other_flow") is not None


def test_empty_goal_selects_nothing():
    register_flow(parse_flow_spec(PORTFOLIO_FLOW, owner="portfolio_plugin"))
    assert select_flow("", goal_class="bake_for_job") is None
