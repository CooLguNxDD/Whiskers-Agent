"""Skill meta frontmatter routing (replaces recipe_registry)."""
from plugins.portfolio_plugin.layout.skill_meta import (
    clear_skill_meta_cache,
    list_skill_meta,
    match_skill_for_goal,
    get_skill_meta,
    load_skill_body,
)


def setup_function():
    clear_skill_meta_cache()


def test_list_skill_meta_nonempty():
    metas = list_skill_meta()
    names = {m.name for m in metas}
    assert "layout-plan-authoring" in names
    assert "context-discovery" in names


def test_match_skill_bake_default():
    m = match_skill_for_goal("bake_for_job", "")
    assert m is not None
    assert m.name == "layout-plan-authoring"
    assert m.quality.get("min_blocks", 0) >= 5


def test_match_skill_triggers():
    m = match_skill_for_goal("redesign", "Please redesign my full layout for a staff role")
    assert m is not None
    assert m.name == "layout-plan-authoring"


def test_load_skill_body():
    body = load_skill_body("layout-plan-authoring")
    assert "LayoutPlan" in body or "layout" in body.lower()
    assert not body.startswith("---")


def test_get_skill_meta():
    m = get_skill_meta("layout-plan-authoring")
    assert m is not None
    pub = m.to_public()
    assert pub["id"] == "layout-plan-authoring"
