"""P1/P2 guards: ask layouts carry a tank, and highlightSlugs survives validation."""

from plugins.portfolio_plugin.compose import compose_scoped
from plugins.portfolio_plugin.compose.compose_scoped import _DEFAULT_PLAN, default_block_plan
from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout


def _types(plan):
    return [s["block_type"] for s in plan]


def test_base_plan_unchanged():
    """The hardcoded plan stays a 6-step page; the tank is additive."""
    assert "fishTank" not in _types(_DEFAULT_PLAN)


def test_tank_step_added_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "plugins.portfolio_plugin.plugin_config.SETTINGS",
        {"fish_tank_enabled": True},
    )
    types = _types(default_block_plan())
    assert "fishTank" in types
    # Must land before the CTA — quickActions is always the trailing block.
    assert types.index("fishTank") < types.index("quickActions")


def test_tank_step_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "plugins.portfolio_plugin.plugin_config.SETTINGS",
        {"fish_tank_enabled": False},
    )
    assert "fishTank" not in _types(default_block_plan())


def test_default_plan_is_not_mutated_across_calls(monkeypatch):
    monkeypatch.setattr(
        "plugins.portfolio_plugin.plugin_config.SETTINGS",
        {"fish_tank_enabled": True},
    )
    default_block_plan()
    default_block_plan()
    assert len(_DEFAULT_PLAN) == 6
    assert compose_scoped._DEFAULT_PLAN is _DEFAULT_PLAN


async def test_floor_tank_is_opt_in(monkeypatch):
    """Floor stays the default-safe text snapshot; the tank is explicit."""
    from unittest.mock import AsyncMock, patch

    from plugins.portfolio_plugin.compose.floor import build_floor_layout

    projects = [
        {
            "slug": "helix-ai",
            "name": "Helix AI",
            "summary": "Agentic workflow automation with real metrics.",
            "tags": ["ai", "primary"],
            "metrics": [{"label": "latency", "value": "p95 -40%"}],
            "sort_order": 0,
            "links": [],
            "context_sources": [{"id": "x", "ref": "github:x"}],
        },
        {
            "slug": "helix-platform",
            "name": "Helix Platform",
            "summary": "Multi-tenant care platform backbone.",
            "tags": ["platform"],
            "metrics": [{"label": "tenants", "value": "12"}],
            "sort_order": 1,
            "links": [],
            "context_sources": [],
        },
    ]

    async def _build(**kwargs):
        with patch(
            "plugins.portfolio_plugin.store.list_projects",
            new=AsyncMock(return_value=projects),
        ), patch(
            "plugins.portfolio_plugin.compose.composer.rank_projects_by_query",
            new=AsyncMock(side_effect=lambda ps, *a, **k: ps),
        ), patch(
            "db_layer.search_content_vectors_store.search_content_vectors",
            new=AsyncMock(return_value=[]),
        ):
            layout = await build_floor_layout("platform engineer", tenant_id=1, **kwargs)
        return {b.get("type") for b in (layout.get("blocks") or []) if isinstance(b, dict)}

    assert "fishTank" not in await _build()
    assert "fishTank" in await _build(include_fish_tank=True)


def test_highlight_slugs_survive_validate_layout():
    """P2: an undeclared meta key is dropped by model_dump on every round-trip."""
    layout = {
        "version": 1,
        "meta": {
            "audience": "default",
            "generatedAt": "2026-01-01T00:00:00Z",
            "highlightSlugs": ["helix-ai", "helix-devops"],
        },
        "blocks": [
            {"type": "prose", "id": "p1", "props": {"markdown": "hello"}},
        ],
    }
    validated, errors = validate_layout(layout)
    assert not errors, errors
    assert validated["meta"]["highlightSlugs"] == ["helix-ai", "helix-devops"]
