"""Unit tests for core_graph.model_roles.selection.select_agent_model — §8.5 precedence."""

from __future__ import annotations

from core_graph.model_roles.selection import select_agent_model


def test_stage_model_wins_over_everything():
    got = select_agent_model(
        stage_model="strongest",
        stage_effort="low",
        flow_effort="high",
        manifest_effort_overrides={"discover": "low"},
        manifest_effort="high",
        goal_class="discover",
        subgraph_model_role="portfolio_default",
    )
    assert got == "strongest"


def test_stage_effort_wins_over_flow_and_below():
    got = select_agent_model(
        stage_effort="low",
        flow_effort="high",
        manifest_effort="high",
        subgraph_model_role="x",
    )
    assert got == "effort:low"


def test_flow_effort_wins_over_manifest_and_below():
    got = select_agent_model(
        flow_effort="medium",
        manifest_effort_overrides={"discover": "low"},
        manifest_effort="high",
        goal_class="discover",
        subgraph_model_role="x",
    )
    assert got == "effort:medium"


def test_manifest_override_wins_over_manifest_default_and_below():
    got = select_agent_model(
        manifest_effort_overrides={"discover": "low"},
        manifest_effort="high",
        goal_class="discover",
        subgraph_model_role="x",
    )
    assert got == "effort:low"


def test_manifest_override_only_applies_for_matching_goal_class():
    got = select_agent_model(
        manifest_effort_overrides={"discover": "low"},
        manifest_effort="high",
        goal_class="bake_for_job",  # not "discover" -> override doesn't apply
        subgraph_model_role="x",
    )
    assert got == "effort:high"


def test_manifest_default_wins_over_subgraph_and_below():
    got = select_agent_model(manifest_effort="high", subgraph_model_role="x", subgraph_effort="low")
    assert got == "effort:high"


def test_subgraph_model_role_wins_over_subgraph_effort():
    got = select_agent_model(subgraph_model_role="portfolio_default", subgraph_effort="low")
    assert got == "role:portfolio_default"


def test_subgraph_effort_used_when_no_model_role():
    got = select_agent_model(subgraph_effort="low")
    assert got == "effort:low"


def test_all_null_defaults_to_core_specialist_role():
    assert select_agent_model() == "role:specialist"
