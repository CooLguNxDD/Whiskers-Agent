"""Ask-mode router: a visitor question -> a bounded patch plan."""

import pytest

from plugins.portfolio_plugin.ask import router as ask_router
from plugins.portfolio_plugin.ask.router import AskPlan, plan_from_dict, route_ask

INDEX = [
    {"id": "h1", "type": "hero"},
    {"id": "kpi-master", "type": "kpiGrid"},
    {"id": "card-helix-devops", "type": "card"},
    {"id": "fish-tank-1", "type": "fishTank"},
    {"id": "cta-floor", "type": "quickActions"},
]

PROJECTS = [
    {
        "slug": "helix-devops",
        "name": "Helix DevOps",
        "summary": "Kubernetes CI/CD pipelines, terraform infra, observability.",
        "tags": ["devops", "kubernetes", "terraform"],
    },
    {
        "slug": "helix-ai",
        "name": "Helix AI",
        "summary": "LangGraph agent orchestration and retrieval.",
        "tags": ["ai", "langgraph"],
    },
    {
        "slug": "whiskers-agent-mcp",
        "name": "Whiskers Agent",
        "summary": (
            "Tell me about the project: a long LangGraph MCP platform writeup "
            "about the gateway, tools, and the about-page for the portfolio."
        ),
        "tags": ["mcp", "langgraph", "platform"],
    },
    {
        "slug": "fisoul",
        "name": "Fisoul",
        "summary": "Multiplayer co-op apex predator survival game in Unity 6.",
        "tags": ["game", "unity", "multiplayer"],
    },
]


@pytest.fixture(autouse=True)
def _stub_backends(monkeypatch):
    """Router must be judged on its classification, not on DB/vector wiring."""
    async def _list_projects(*_a, **_k):
        return list(PROJECTS)

    async def _rank(projects, query, **_k):
        return list(projects)

    monkeypatch.setattr("plugins.portfolio_plugin.store.list_projects", _list_projects)
    monkeypatch.setattr(
        "plugins.portfolio_plugin.compose.ranking.rank_projects_by_query", _rank
    )
    monkeypatch.setattr(ask_router, "_ask_settings", lambda: {"confidence_floor": 0.0})


async def test_empty_question_is_answer_only():
    plan = await route_ask("", block_index=INDEX)
    assert plan.intent == "answer_only"


async def test_job_posting_hands_off_to_bake():
    plan = await route_ask("bake portfolio for Platform Engineer at Acme")
    assert plan.intent == "bake"


async def test_known_fish_focuses_not_rebuilds(monkeypatch):
    plan = await route_ask(
        "tell me about the devops kubernetes work",
        view="tank",
        block_index=INDEX,
        tank_slugs=["helix-devops", "helix-ai"],
    )
    assert plan.intent == "focus_fish"
    assert plan.focus_slug == "helix-devops"
    assert plan.target_ids == ("fish-tank-1",)


async def test_grounded_project_absent_from_tank_is_added():
    plan = await route_ask(
        "any kubernetes terraform experience?",
        view="tank",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
    )
    assert plan.intent == "add_fish"
    assert plan.add_slugs == ("helix-devops",)


async def test_batch_question_adds_every_matched_project_not_just_top(monkeypatch):
    """'game?' lexically matches several rows — all of them must spawn, not
    just the single top-scored one (that was the whole tank-view add_fish
    bug: highlight_slugs listed every match but add_slugs kept only one)."""
    projects = [
        {
            "slug": "fisoul",
            "name": "Fisoul",
            "summary": "Multiplayer co-op apex predator survival game in Unity 6.",
            "tags": ["game", "unity"],
        },
        {
            "slug": "plinkrupt",
            "name": "Plinkrupt",
            "summary": "A physics-based party game about going bankrupt.",
            "tags": ["game", "physics"],
        },
        {
            "slug": "reversi-mcts",
            "name": "Reversi MCTS",
            "summary": "Reversi board game AI with Monte-Carlo tree search.",
            "tags": ["game", "ai"],
        },
    ]

    async def _list_projects(*_a, **_k):
        return list(projects)

    monkeypatch.setattr("plugins.portfolio_plugin.store.list_projects", _list_projects)

    plan = await route_ask(
        "game?",
        view="tank",
        block_index=INDEX,
        tank_slugs=[],
    )
    assert plan.intent == "add_fish"
    assert set(plan.add_slugs) == {"fisoul", "plinkrupt", "reversi-mcts"}
    assert set(plan.highlight_slugs) == {"fisoul", "plinkrupt", "reversi-mcts"}


async def test_tank_view_without_a_tank_block_degrades_to_answer():
    plan = await route_ask(
        "kubernetes terraform",
        view="tank",
        block_index=[{"id": "h1", "type": "hero"}],
        tank_slugs=[],
    )
    assert plan.intent == "recommend"


async def test_below_confidence_floor_never_invents_a_fish(monkeypatch):
    monkeypatch.setattr(ask_router, "_ask_settings", lambda: {"confidence_floor": 99.0})
    plan = await route_ask(
        "do you have quantum photonics work?",
        view="tank",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
    )
    assert plan.intent == "recommend"
    assert plan.add_slugs == ()


async def test_below_floor_with_discovery_enabled_starts_discovery(monkeypatch):
    monkeypatch.setattr(
        ask_router,
        "_ask_settings",
        lambda: {"confidence_floor": 99.0, "discovery_fallback": True},
    )
    plan = await route_ask("quantum photonics?", view="tank", block_index=INDEX)
    assert plan.intent == "discover"


def test_ask_tokens_drop_visitor_glue():
    assert ask_router._ask_tokens("tell me about the game projects") == {"game"}
    assert ask_router._ask_tokens("can you describe your fisoul work please") == {"fisoul"}
    assert "about" not in ask_router._ask_tokens("what did you build")


async def test_fisoul_name_beats_dense_tunnel_summary(monkeypatch):
    """A named project must win even when a longer row contains the glue words."""
    monkeypatch.setattr(ask_router, "_ask_settings", lambda: {"confidence_floor": 0.35})
    plan = await route_ask(
        "tell me about the fisoul?",
        view="text",
        block_index=INDEX,
        tank_slugs=["whiskers-agent-mcp"],
    )
    assert plan.focus_slug == "fisoul"
    assert plan.intent != "discover"


async def test_game_question_picks_fisoul_not_tunnel(monkeypatch):
    monkeypatch.setattr(ask_router, "_ask_settings", lambda: {"confidence_floor": 0.35})
    plan = await route_ask(
        "tell me about the game projects",
        view="text",
        block_index=INDEX,
        tank_slugs=["whiskers-agent-mcp"],
    )
    assert plan.focus_slug == "fisoul"


async def test_ai_project_text_ask_picks_inventory_not_discover(monkeypatch):
    """'ai' is a 2-letter token; bake ranking would prefer the first row."""
    monkeypatch.setattr(ask_router, "_ask_settings", lambda: {"confidence_floor": 0.35})
    plan = await route_ask(
        "tell me about your ai project",
        view="text",
        block_index=INDEX,
        tank_slugs=[],
    )
    assert plan.intent != "discover"
    assert plan.focus_slug == "helix-ai"
    assert plan.add_slugs == ("helix-ai",)


async def test_text_question_targets_the_project_card_only():
    plan = await route_ask(
        "what did you build with kubernetes terraform",
        view="text",
        block_index=INDEX,
    )
    assert plan.intent == "patch_blocks"
    assert plan.target_ids == ("card-helix-devops",)
    assert all(s["block_type"] == "card" for s in plan.block_steps)


async def test_star_shaped_question_adds_a_star_block():
    plan = await route_ask(
        "walk me through a kubernetes terraform challenge",
        view="text",
        block_index=INDEX,
    )
    types = [s["block_type"] for s in plan.block_steps]
    assert "card" in types and "starStory" in types


async def test_arch_question_never_inserts_an_ungrounded_diagram():
    """No existing archDiagram means no insert — that would be invented content."""
    plan = await route_ask(
        "how does the kubernetes terraform architecture work",
        view="text",
        block_index=INDEX,
    )
    assert "archDiagram" not in [s["block_type"] for s in plan.block_steps]


async def test_sacred_blocks_are_never_planned():
    plan = await route_ask(
        "what did you build with kubernetes terraform",
        view="text",
        block_index=INDEX,
    )
    assert "h1" not in plan.target_ids
    assert "kpi-master" not in plan.target_ids
    assert "cta-floor" not in plan.target_ids


async def test_patch_steps_respect_max_patch_blocks(monkeypatch):
    monkeypatch.setattr(
        ask_router,
        "_ask_settings",
        lambda: {"confidence_floor": 0.0, "max_patch_blocks": 1},
    )
    plan = await route_ask(
        "walk me through a kubernetes terraform challenge",
        view="text",
        block_index=INDEX,
    )
    assert len(plan.block_steps) == 1


async def test_patch_plus_tank_add_never_exceeds_budget(monkeypatch):
    """Regression: text-block steps used to spend the whole max_patch_blocks
    budget, and a tank add (when the project isn't already swimming) could
    tack on one more block on top — max+1 blocks in a single overlay. The
    tank-add slot must be reserved before text steps are sized."""
    monkeypatch.setattr(
        ask_router,
        "_ask_settings",
        lambda: {"confidence_floor": 0.0, "max_patch_blocks": 2},
    )
    plan = await route_ask(
        "walk me through a kubernetes terraform challenge",
        view="text",
        block_index=INDEX,
        tank_slugs=[],  # top project not already in the tank -> add_fish path
    )
    total_blocks_touched = len(plan.block_steps) + len(plan.add_slugs)
    assert total_blocks_touched <= 2


def test_plan_round_trips_through_dict():
    plan = AskPlan(
        intent="patch_blocks",
        focus_slug="helix-ai",
        block_steps=({"block_type": "card", "block_id": "card-helix-ai"},),
        target_ids=("card-helix-ai",),
        confidence=0.9,
    )
    assert plan_from_dict(plan.to_dict()) == plan


def test_unknown_intent_degrades_to_answer_only():
    assert plan_from_dict({"intent": "nuke_the_page"}).intent == "answer_only"


async def test_below_floor_with_discovery_disabled_recommends(monkeypatch):
    monkeypatch.setattr(
        ask_router,
        "_ask_settings",
        lambda: {"confidence_floor": 99.0, "discovery_fallback": False},
    )
    plan = await route_ask(
        "quantum photonics?",
        view="tank",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
    )
    assert plan.intent == "recommend"
    assert plan.add_slugs == ()
    assert "helix-ai" in plan.recommend_slugs
    assert len(plan.pool_slugs) > 0


async def test_recommend_split_respects_tank_presence(monkeypatch):
    monkeypatch.setattr(
        ask_router,
        "_ask_settings",
        lambda: {"confidence_floor": 99.0, "discovery_fallback": False},
    )
    plan = await route_ask(
        "tell me about ungrounded topic",
        view="tank",
        block_index=INDEX,
        tank_slugs=["helix-devops", "fisoul"],
    )
    assert plan.intent == "recommend"
    for slug in plan.recommend_slugs:
        assert slug in {"helix-devops", "fisoul"}
    for slug in plan.pool_slugs:
        assert slug not in {"helix-devops", "fisoul"}


def test_ask_plan_dict_roundtrip_includes_recommend_fields():
    plan = AskPlan(
        intent="recommend",
        confidence=0.12,
        recommend_slugs=("helix-ai",),
        pool_slugs=("fisoul", "helix-devops"),
        reason="no project above confidence floor",
    )
    d = plan.to_dict()
    assert d["recommend_slugs"] == ["helix-ai"]
    assert d["pool_slugs"] == ["fisoul", "helix-devops"]
    roundtripped = plan_from_dict(d)
    assert roundtripped == plan
    assert roundtripped.recommend_slugs == ("helix-ai",)
    assert roundtripped.pool_slugs == ("fisoul", "helix-devops")


def test_recommend_intent_survives_plan_from_dict():
    plan = plan_from_dict({"intent": "recommend"})
    assert plan.intent == "recommend"


async def test_tank_view_without_tank_block_returns_recommend():
    plan = await route_ask(
        "kubernetes terraform",
        view="tank",
        block_index=[{"id": "h1", "type": "hero"}],
        tank_slugs=["helix-ai"],
    )
    assert plan.intent == "recommend"
    assert plan.focus_slug == "helix-devops"
    assert "helix-ai" in plan.recommend_slugs or "helix-devops" in plan.pool_slugs


async def test_empty_inventory_below_floor_falls_back_to_answer_only(monkeypatch):
    async def _empty_list(*_a, **_k):
        return []

    monkeypatch.setattr("plugins.portfolio_plugin.store.list_projects", _empty_list)
    monkeypatch.setattr(
        ask_router,
        "_ask_settings",
        lambda: {"confidence_floor": 99.0, "discovery_fallback": False},
    )
    plan = await route_ask("quantum photonics?", view="tank", block_index=INDEX)
    assert plan.intent == "answer_only"
    assert "no project above confidence floor" in plan.reason
    assert plan.recommend_slugs == ()
    assert plan.pool_slugs == ()

