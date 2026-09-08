"""Coverage for the portfolio_ask_v1 FlowSpec — the surgical-patch ask path.

Verifies:
1. The manifest-declared flow loads through the real plugin-loader path.
2. It claims ``scoped_ask`` without stealing bake goals from portfolio_bake_v1.
3. Its agentic stage cannot reach any whole-page compose/bake tool — the tool
   globs, not a prompt, are what stop ask turns from rebuilding the page.
4. The ask tools stay out of the GOAP candidate pool.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.plugin_loader.lifecycle_manager import PluginLifecycleManager
from core_graph.subgraphs.specialist.flow_registry import (
    clear_flows,
    get_flow_registry,
    select_flow,
)

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_FLOW_PATH = _PLUGIN_DIR / "flow_specs" / "portfolio_ask_v1.json"

# Tools that would reintroduce the full rebuild this flow exists to replace.
_FORBIDDEN = (
    "bake_portfolio_for_job",
    "compose_scoped_layout",
    "emit_layout",
    "design_layout",
    "generate_layout_for_query",
    "patch_job_layout",
)


@pytest.fixture(autouse=True)
def _clean():
    clear_flows()
    yield
    clear_flows()


def _load_flows():
    spec = SimpleNamespace(name="portfolio_plugin", manifest_path=_PLUGIN_DIR / "manifest.json")
    PluginLifecycleManager._load_flow_specs(
        spec,
        ["flow_specs/portfolio_bake_v1.json", "flow_specs/portfolio_ask_v1.json"],
    )


def test_flow_spec_file_is_valid_json():
    data = json.loads(_FLOW_PATH.read_text(encoding="utf-8"))
    assert data["flow_id"] == "portfolio_ask_v1"
    assert data["claims"]["goal_classes"] == ["scoped_ask"]


def test_manifest_declares_the_ask_flow():
    manifest = json.loads((_PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    flows = manifest["settings"]["specialist_agent"]["flow_specs"]
    assert "flow_specs/portfolio_ask_v1.json" in flows


def test_flow_registers_through_the_plugin_loader():
    _load_flows()
    assert get_flow_registry().get("portfolio_ask_v1") is not None


def test_ask_goal_selects_the_ask_flow_not_the_bake_flow():
    _load_flows()
    flow = select_flow("tell me about the devops work", goal_class="scoped_ask")
    assert flow is not None and flow.flow_id == "portfolio_ask_v1"


def test_bake_goal_still_selects_the_bake_flow():
    """Ask must not swallow bakes — a real bake is the one allowed rebuild."""
    _load_flows()
    flow = select_flow(
        "bake portfolio for Platform Engineer at Acme", goal_class="bake_for_job"
    )
    assert flow is not None and flow.flow_id == "portfolio_bake_v1"


def test_agentic_stage_cannot_reach_a_whole_page_compose_tool():
    _load_flows()
    flow = get_flow_registry().get("portfolio_ask_v1")
    agentic = [s for s in flow.stages if s.kind == "agentic"]
    assert agentic, "ask flow must author its prose in an agentic stage"
    for stage in agentic:
        assert stage.tool_globs, "an unbounded agentic stage can compose the page"
        for glob in stage.tool_globs:
            assert "*" not in glob, f"blanket glob {glob!r} defeats the clamp"
            assert not any(bad in glob for bad in _FORBIDDEN)


def test_deterministic_stages_own_the_routing_decision():
    """Classification is a server function, not a prompt the agent may ignore."""
    _load_flows()
    flow = get_flow_registry().get("portfolio_ask_v1")
    route = next(s for s in flow.stages if s.id == "route")
    build = next(s for s in flow.stages if s.id == "build")
    assert route.kind == "deterministic" and route.on_fail == "fail_closed"
    assert build.kind == "deterministic" and build.on_fail == "fail_closed"
    fallback = next(s for s in flow.stages if s.id == "answer_fallback")
    assert fallback.kind == "deterministic" and fallback.on_fail == "continue"
    answer = next(s for s in flow.stages if s.id == "answer")
    assert answer.on_fail == "continue"
    assert "recommendations" in build.writes
    assert "add_slugs" in route.reads
    assert "recommendations" in fallback.reads


def test_ask_tools_are_goap_denylisted():
    from db_layer.embeddings.embeddings_routes import GOAP_CANDIDATE_DENYLIST
    from plugins.portfolio_plugin.plugin_config import _GOAP_HIDDEN_OPS

    for op in (
        "portfolio_plugin__route_portfolio_ask",
        "portfolio_plugin__build_ask_overlay",
        "portfolio_plugin__start_context_discovery",
        "portfolio_plugin__get_context_discovery",
        "portfolio_plugin__await_context_discovery",
        "portfolio_plugin__ensure_ask_answer",
    ):
        assert ("portfolio_plugin", op) in GOAP_CANDIDATE_DENYLIST
        assert op in _GOAP_HIDDEN_OPS


def test_ask_scope_grants_a_full_ask_turn_and_nothing_else():
    """A narrow ``group:portfolio_plugin:ask`` key must satisfy every op a
    real ask turn dispatches (route, overlay/spawn, and the answer stage's
    two context-read tools — the ``ask`` tag added to those two is what
    closes the gap) while still being denied write-tagged operator ops."""
    from core.scope_management import is_allowed, required_scopes_for_route

    grant = ["group:portfolio_plugin:ask"]

    # Ops a full ask turn touches, with their real tags (kept in sync with
    # MCPTools/ask_tools.py, context_search_tools.py, context_tools.py).
    ask_turn_ops = {
        "route_portfolio_ask": {"portfolio_plugin", "read", "ask"},
        "build_ask_overlay": {"portfolio_plugin", "read", "ask"},
        "ensure_ask_answer": {"portfolio_plugin", "read", "ask"},
        "search_portfolio_context": {"portfolio_plugin", "read", "ask"},
        "get_project_context": {"portfolio_plugin", "read", "ask"},
    }
    for op_id, tags in ask_turn_ops.items():
        required = required_scopes_for_route("portfolio_plugin", tags)
        assert is_allowed(grant, required), f"ask scope must reach {op_id}"

    # Operator write ops must stay out of reach.
    write_ops = {
        "bake_portfolio_for_job": {"portfolio_plugin", "write"},
        "ingest_portfolio_context": {"portfolio_plugin", "write"},
    }
    for op_id, tags in write_ops.items():
        required = required_scopes_for_route("portfolio_plugin", tags)
        assert not is_allowed(grant, required), f"ask scope must NOT reach {op_id}"


def test_scoped_ask_is_not_an_agentic_layout_goal_class():
    """agentic_goal_classes routes to run_layout_agent — the full rebuild."""
    manifest = json.loads((_PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    classes = manifest["settings"]["portfolio_layout"]["agentic_goal_classes"]
    assert "scoped_ask" not in classes
