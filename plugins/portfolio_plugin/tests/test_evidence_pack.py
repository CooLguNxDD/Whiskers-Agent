"""Evidence pack + free-structure helpers for context-first layout planning."""

from __future__ import annotations

import pytest

from plugins.portfolio_plugin.layout.evidence_pack import (
    block_catalog_entries,
    format_block_catalog,
    format_evidence_budget,
    is_skeleton_clone,
    plan_type_fingerprint,
    recipe_type_fingerprint,
)


def test_block_catalog_covers_schema_types():
    from plugins.portfolio_plugin.schema.ui_layout_schema import BLOCK_TYPES

    entries = block_catalog_entries()
    types = {e["type"] for e in entries}
    assert types == set(BLOCK_TYPES)
    for e in entries:
        assert e["band"]["level"] is not None
        assert e["band"]["label"]
        assert e["grounding"] in ("db", "authored", "widget", "dual")
        assert e["source_refs"] in ("required", "not_required", "required_if_authored")
        assert 1 <= e["default_span"] <= 12
    text = format_block_catalog()
    assert "hero" in text
    assert "card" in text
    assert "grounding" in text.lower() or "widget" in text


def test_block_catalog_derives_from_dispatch_maps():
    """band/grounding must come from the real dispatch maps, not re-typed data."""
    from plugins.portfolio_plugin.compose.composer import _DAG_LEVEL_BY_TYPE
    from plugins.portfolio_plugin.schema.block_catalog import get_block_catalog

    catalog = get_block_catalog()
    for btype, (level, label) in _DAG_LEVEL_BY_TYPE.items():
        feat = catalog[btype]
        assert feat.band_level == level
        assert feat.band_label == label
    # widget types cost nothing to include
    assert catalog["mcpSandbox"].source_refs == "not_required"
    assert catalog["costSim"].source_refs == "not_required"
    # authored types must cite sources
    assert catalog["prose"].source_refs == "required"
    assert catalog["comparison"].source_refs == "required"
    # dual dispatch types
    assert catalog["chart"].grounding == "dual"
    assert catalog["archDiagram"].grounding == "dual"


def test_format_evidence_budget_includes_projects_and_docs():
    pack = {
        "pack_hash": "abc",
        "projects": [
            {
                "slug": "helix-ai-platform",
                "name": "Helix AI",
                "summary": "MCP gateway",
                "tags": ["primary", "AI"],
                "metrics": [{"label": "commits", "value": "395"}],
                "sort_order": 0,
            }
        ],
        "virtual_projects": [
            {
                "slug": "discovered-repo",
                "name": "Discovered Repo",
                "summary": "From index",
                "tags": ["mcp"],
            }
        ],
        "docs": [
            {
                "ref": "contrib:helix-ai-platform",
                "title": "AI report",
                "slug_hint": "helix-ai-platform",
                "excerpt": "Built MCP server…",
                "score": 0.9,
                "kind": "readme",
            }
        ],
        "inventory": {
            "project_count": 4,
            "project_slugs": ["helix-ai", "helix-devops"],
            "context_collection": "portfolio_plugin__context",
            "context_index_count": 61,
            "context_kinds": {"readme": 20, "repo": 10},
            "virtual_project_count": 1,
        },
        "web": [],
        "context_only": True,
    }
    text = format_evidence_budget(pack)
    assert "helix-ai-platform" in text
    assert "contrib:helix-ai-platform" in text
    assert "pack_hash=abc" in text
    assert "CONTEXT ONLY" in text
    assert "discovered-repo" in text
    assert "portfolio_star" not in text
    assert "STAR stories" not in text
    assert "61" in text or "context_index" in text


@pytest.mark.asyncio
async def test_build_evidence_pack_uses_all_projects_not_fixed_shortlist():
    """Evidence pack must surface the full DB inventory + index, not top-4 only."""
    from unittest.mock import AsyncMock, patch

    from plugins.portfolio_plugin.layout.evidence_pack import build_evidence_pack

    fake_projects = [
        {"slug": f"proj-{i}", "name": f"P{i}", "summary": f"summary {i}" * 20, "tags": [], "metrics": []}
        for i in range(6)
    ]

    async def _rank(projects, query, tenant_id=1, top_k=12):
        return list(projects)[:top_k]

    with patch(
        "plugins.portfolio_plugin.store.list_projects",
        new_callable=AsyncMock,
        return_value=fake_projects,
    ), patch(
        "plugins.portfolio_plugin.compose.composer.rank_projects_by_query",
        new_callable=AsyncMock,
        side_effect=_rank,
    ), patch(
        "plugins.portfolio_plugin.discovery.index.search_context",
        new_callable=AsyncMock,
        return_value=[
            {
                "content_text": "indexed readme about agents",
                "metadata": {
                    "ref": "github:org/new-agent-tool",
                    "title": "new-agent-tool",
                    "slug_hint": "new-agent-tool",
                    "kind": "readme",
                },
                "similarity": 0.9,
            }
        ],
    ), patch(
        "db_layer.search_content_vectors_store.list_search_content_vectors",
        new_callable=AsyncMock,
        return_value=[{"metadata": {"kind": "readme"}}] * 5,
    ), patch(
        "plugins.portfolio_plugin.compose.block_builder._virtual_project_from_hit",
        return_value={
            "slug": "new-agent-tool",
            "name": "new-agent-tool",
            "summary": (
                "Agent tooling repo: multi-step orchestration over MCP tools "
                "with LangGraph planners and production routing for AI systems."
            ),
            "tags": ["mcp", "agents"],
            "metrics": [],
            "virtual": True,
        },
    ):
        pack = await build_evidence_pack(
            "AI engineer agent systems",
            tenant_id=1,
            top_k_docs=12,
            top_k_projects=20,
            web_enrich=False,
        )

    assert pack["status"] == "ok"
    assert len(pack["projects"]) == 6  # full inventory, not a fixed 4
    slugs = {p["slug"] for p in pack["projects"]}
    assert "proj-0" in slugs and "proj-5" in slugs
    assert pack.get("docs")
    assert pack["inventory"].get("project_count") == 6
    assert any(v.get("slug") == "new-agent-tool" for v in (pack.get("virtual_projects") or []))


def test_skeleton_clone_detects_job_bake_order():
    recipe = {
        "skeleton": [
            {"block_type": "hero"},
            {"block_type": "kpiGrid"},
            {"block_type": "card"},
            {"block_type": "flowAnim"},
            {"block_type": "mcpSandbox"},
            {"block_type": "chart"},
            {"block_type": "costSim"},
            {"block_type": "starStory"},
            {"block_type": "timeline"},
            {"block_type": "archDiagram"},
            {"block_type": "quickActions"},
        ]
    }
    layout_clone = {
        "blocks": [
            {"type": "hero"},
            {"type": "kpiGrid"},
            {"type": "card"},
            {"type": "card"},
            {"type": "card"},
            {"type": "card"},
            {"type": "flowAnim"},
            {"type": "mcpSandbox"},
            {"type": "chart"},
            {"type": "costSim"},
            {"type": "starStory"},
            {"type": "timeline"},
            {"type": "archDiagram"},
            {"type": "quickActions"},
        ]
    }
    layout_free = {
        "blocks": [
            {"type": "hero"},
            {"type": "card"},
            {"type": "card"},
            {"type": "prose"},
            {"type": "timeline"},
            {"type": "starStory"},
            {"type": "quickActions"},
        ]
    }
    assert is_skeleton_clone(layout_clone, recipe) is True
    assert is_skeleton_clone(layout_free, recipe) is False
    assert recipe_type_fingerprint(recipe)[0] == "hero"
    assert plan_type_fingerprint(layout_free) == [
        "hero", "card", "card", "prose", "timeline", "starStory", "quickActions"
    ]


@pytest.mark.asyncio
async def test_build_evidence_pack_fail_open(monkeypatch):
    from plugins.portfolio_plugin.layout import evidence_pack as ep

    async def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "plugins.portfolio_plugin.store.list_projects",
        boom,
        raising=False,
    )

    # Patch at use site via module-level import inside function — monkeypatch store
    import plugins.portfolio_plugin.store as store

    monkeypatch.setattr(store, "list_projects", boom)

    pack = await ep.build_evidence_pack("test query", tenant_id=1, web_enrich=False)
    assert pack["status"] == "ok"
    assert pack["projects"] == []
    assert isinstance(pack.get("pack_hash"), str)


def test_compose_prompt_free_mode_omits_skeleton_list():
    from plugins.portfolio_plugin.render.design_system import compose_layout_system_prompt

    recipe = {
        "id": "job-bake",
        "default_theme": "neon",
        "skeleton": [{"block_type": "hero"}, {"block_type": "kpiGrid"}],
        "quality": {"min_blocks": 5},
    }
    free = compose_layout_system_prompt(
        recipe=recipe,
        structure_mode="free",
        block_catalog="- hero: open\n- card: tiles",
        evidence_budget="slug=`helix-ai-platform` summary=AI layer",
    )
    assert "STRUCTURE MODE: FREE" in free
    assert "BLOCK CATALOG" in free
    assert "quality + theme only" in free
    assert "skeleton=[{'block_type': 'hero'" not in free
    assert "helix-ai-platform" in free

    legacy = compose_layout_system_prompt(
        recipe=recipe,
        structure_mode="recipe_seed",
    )
    assert "SELECTED RECIPE" in legacy
    assert "skeleton=" in legacy


def test_use_free_structure_defaults():
    from plugins.portfolio_plugin.layout.layout_config import use_free_structure

    assert use_free_structure("bake_for_job", {"structure_mode": "free"}) is True
    assert use_free_structure("scoped_ask", {"structure_mode": "free"}) is False
    assert use_free_structure("bake_for_job", {"structure_mode": "recipe_seed"}) is False
