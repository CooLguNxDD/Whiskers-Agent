"""Phase 5 — design system packages + direction lock unit tests."""

from __future__ import annotations

from plugins.portfolio_plugin.render.design_system import (
    auto_pick_direction,
    compose_layout_system_prompt,
    load_craft_modules,
    load_design_system,
    propose_directions,
    tokens_to_theme_overrides,
)


def test_load_default_design_system():
    ds = load_design_system("default")
    assert ds["design_md"]
    assert ds["usage_md"]
    assert isinstance(ds["tokens"], dict)
    assert ds["tokens"]


def test_craft_modules_folded_into_skills():
    """craft/ was deleted; load_craft_modules is a back-compat empty stub."""
    craft = load_craft_modules()
    assert craft == {}
    # Skill body now owns grounding rules
    from plugins.portfolio_plugin.layout.skill_meta import load_skill_body

    body = load_skill_body("layout-plan-authoring")
    assert "source_refs" in body or "Grounding" in body


def test_tokens_to_theme_overrides():
    ov = tokens_to_theme_overrides(
        {"--accent": "cyan", "colors": {"domain-ai": "purple"}}
    )
    # --accent is aliased to allowlisted amber key at the producer
    assert ov.get("--accent") == "cyan" or ov.get("--amber") == "cyan" or any(
        v == "cyan" for v in ov.values()
    )
    assert any("domain-ai" in k or "accent-ai" in k for k in ov) or any(
        v == "purple" for v in ov.values()
    )


def test_propose_directions_and_auto_pick():
    dirs = propose_directions("staff platform engineer reliability architecture")
    assert len(dirs) == 3
    pick = auto_pick_direction("staff platform engineer systems architecture MCP")
    assert pick["theme"] in ("neon", "paper", "cozy", "latte", "frappe", "macchiato", "mocha")
    # architecture brief should lean neon-systems
    assert pick["id"] == "neon-systems"

    rec = auto_pick_direction("recruiter hire skim resume")
    assert rec["id"] == "paper-recruiter"


def test_compose_layout_system_prompt_includes_planes():
    prompt = compose_layout_system_prompt(
        design_system_id="default",
        craft_names=["grounding"],
        recipe={"id": "matrix-redesign", "skeleton": [{"block_type": "hero"}], "quality": {}},
        skill_body="# skill\nplan then build",
        evidence_budget="hit: Whiskers Agent",
    )
    assert "DESIGN SYSTEM" in prompt or "DESIGN" in prompt
    assert "matrix-redesign" in prompt
    assert "LayoutPlan" in prompt
