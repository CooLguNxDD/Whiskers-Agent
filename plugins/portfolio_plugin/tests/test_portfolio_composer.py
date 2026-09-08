"""
Unit tests for Portfolio Layout Composer.
"""

import pytest
from unittest.mock import patch, AsyncMock

from plugins.portfolio_plugin.compose.composer import compose_layout


@pytest.fixture
def mock_projects():
    """Mock projects list including a headline metric and an invalid link.

    Summaries/metrics must clear ``filter_projects_for_layout`` (min prose or
    metrics) so both cards render on the matrix path.
    """
    return [
        {
            "slug": "project-1",
            "name": "Project One",
            "summary": (
                "First project summary with enough substance for portfolio "
                "layout quality gates and card body rendering."
            ),
            "tags": ["python", "mcp"],
            "metrics": [
                {"label": "context reduction", "value": "~98.7%", "headline": True},
                {"label": "plain metric", "value": "100", "headline": False},
            ],
            "links": [
                {"label": "GitHub", "href": "https://github.com/test/one"}
            ],
        },
        {
            "slug": "project-2",
            "name": "Project Two",
            "summary": (
                "Second project summary covering frontend architecture, "
                "delivery, and measurable product impact for recruiters."
            ),
            "tags": ["react"],
            "metrics": [
                {"label": "Lighthouse", "value": "98"},
            ],
            "links": [
                {"label": "Valid", "href": "https://example.com/two"},
                {"label": "Invalid", "href": "notaurl"},
            ],
        },
    ]


@pytest.fixture
def mock_stories():
    """Mock stories list containing one valid and one malformed metadata."""
    return [
        {
            "id": 1,
            "metadata": {
                "situation": "Situation 1",
                "task": "Task 1",
                "action": "Action 1",
                "result": "Result 1",
                "tags": ["tag1"],
            },
        },
        {
            "id": 2,
            "metadata": {
                "situation": "Situation 2",
                "task": "Task 2",
                "action": "Action 2",
            },
        },
    ]


@pytest.mark.asyncio
async def test_all_audiences_validate(mock_projects, mock_stories):
    """Verify that all four audience configurations generate valid layout structures."""
    from plugins.portfolio_plugin.schema.ui_layout_schema import UILayout
    _ = mock_stories  # portfolio_star retired — template path no longer loads stars

    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=mock_projects,
    ):
        for aud in ["default", "recruiter", "hiring-manager", "peer"]:
            result = await compose_layout(audience=aud, tenant_id=1, _skip_scoped=True)

            # result validates via UILayout.model_validate
            model = UILayout.model_validate(result)

            # meta.audience == input
            assert model.meta.audience == aud

            # blocks non-empty
            assert len(model.blocks) > 0

            # all block types are in the expected list (matrix + legacy)
            allowed_types = {
                "hero",
                "projectGrid",
                "statStrip",
                "starStory",
                "kpiGrid",
                "card",
                "archDiagram",
                "codeSnippet",
                "prose",
            }
            for b in model.blocks:
                assert b.type in allowed_types
            # Matrix default stamps level-row dag
            assert model.meta.dag is not None
            assert model.meta.dag.levels


@pytest.mark.asyncio
async def test_section_order_matches_template(mock_projects, mock_stories):
    """Hiring-manager matrix order: hero → kpiGrid → cards (starStory omitted without store)."""
    _ = mock_stories
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=mock_projects,
    ):
        result = await compose_layout(
            audience="hiring-manager", tenant_id=1, _skip_scoped=True
        )
        block_types = [b["type"] for b in result["blocks"]]

        assert block_types[0] == "hero"
        assert block_types[1] == "kpiGrid"
        # portfolio_star retired — empty stories skip starStory section
        assert "starStory" not in block_types
        assert block_types[2:] == ["card", "card"]
        assert result["meta"]["dag"]["levels"][0]["nodes"] == ["h1"]


@pytest.mark.asyncio
async def test_kpi_and_cards_default(mock_projects, mock_stories):
    """Default audience: kpiGrid + domain cards; metrics never carry headline keys."""
    _ = mock_stories
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=mock_projects,
    ):
        result = await compose_layout(audience="default", tenant_id=1, _skip_scoped=True)

        kpi_blocks = [b for b in result["blocks"] if b["type"] == "kpiGrid"]
        assert len(kpi_blocks) == 1
        items = kpi_blocks[0]["props"]["items"]
        assert any(it["label"] == "context reduction" for it in items)

        cards = [b for b in result["blocks"] if b["type"] == "card"]
        assert len(cards) == 2
        for c in cards:
            for m in c["props"].get("metrics") or []:
                assert "headline" not in m
        assert result["meta"].get("theme") == "cozy"  # template path stamps matrix theme


@pytest.mark.asyncio
async def test_star_failure_degrades(mock_projects):
    """Template path omits starStory when portfolio_star is retired (empty stories)."""
    from plugins.portfolio_plugin.schema.ui_layout_schema import UILayout

    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=mock_projects,
    ):
        result = await compose_layout(audience="default", tenant_id=1, _skip_scoped=True)

        # layout still validates
        model = UILayout.model_validate(result)

        # zero starStory blocks
        star_story_blocks = [b for b in model.blocks if b.type == "starStory"]
        assert len(star_story_blocks) == 0


@pytest.mark.asyncio
async def test_empty_projects_omit_blocks(mock_stories):
    """Verify cards/kpi are omitted if project list is empty."""
    _ = mock_stories
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await compose_layout(audience="default", tenant_id=1, _skip_scoped=True)
        block_types = {b["type"] for b in result["blocks"]}

        assert "projectGrid" not in block_types
        assert "card" not in block_types
        assert "kpiGrid" not in block_types
        assert "statStrip" not in block_types
        assert "hero" in block_types


@pytest.mark.asyncio
async def test_invalid_href_dropped(mock_projects, mock_stories):
    """Verify invalid link URLs are filtered out on domain cards."""
    _ = mock_stories
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=mock_projects,
    ):
        result = await compose_layout(audience="default", tenant_id=1, _skip_scoped=True)
        cards = [b for b in result["blocks"] if b["type"] == "card"]
        assert len(cards) == 2
        p2 = next(c for c in cards if c["id"] == "card-project-2")
        assert len(p2["props"]["links"]) == 1
        assert p2["props"]["links"][0]["href"] == "https://example.com/two"


@pytest.mark.asyncio
async def test_unknown_audience_coerced(mock_projects, mock_stories):
    """Verify unknown audience identifier is coerced to default."""
    _ = mock_stories
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=mock_projects,
    ):
        result = await compose_layout(audience="alien", tenant_id=1, _skip_scoped=True)
        assert result["meta"]["audience"] == "default"


@pytest.mark.asyncio
async def test_hero_absent_pitch_not_null():
    """Verify that absent pitch in hero settings is omitted (not null) in the dumped layout."""
    custom_settings = {
        "hero": {
            "name": "No Pitch Cat",
            "tagline": "No pitch here",
            "links": [],
        }
    }
    with patch("plugins.portfolio_plugin.compose.composer.SETTINGS", custom_settings):
        with patch(
            "plugins.portfolio_plugin.compose.composer.list_projects",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await compose_layout(
                audience="default", tenant_id=1, _skip_scoped=True
            )

            def assert_no_none_recursively(d):
                if isinstance(d, dict):
                    for k, v in d.items():
                        assert v is not None, f"Found None for key {k}"
                        assert_no_none_recursively(v)
                elif isinstance(d, list):
                    for item in d:
                        assert_no_none_recursively(item)

            assert_no_none_recursively(result)


@pytest.mark.asyncio
async def test_hero_non_string_href_dropped():
    """Verify _hero() silently drops links where href or label is not a string (Gemini finding #2)."""
    from plugins.portfolio_plugin.compose.composer import _hero

    custom_settings = {
        "name": "Test Cat",
        "tagline": "Testing",
        "links": [
            {"href": 12345, "label": "IntHref"},      # non-string href
            {"href": "https://ok.com", "label": None}, # non-string label
            {"href": "https://valid.com", "label": "Valid"},  # should pass
        ],
    }
    block = _hero(custom_settings)
    # Only the fully-valid link should survive
    assert block.props.links is not None
    assert len(block.props.links) == 1
    assert block.props.links[0].href == "https://valid.com"


def test_kpi_grid_keeps_zero_valued_metric():
    """A legitimate zero-valued KPI ("0 downtime") must not be dropped by truthiness."""
    from plugins.portfolio_plugin.compose.composer import _kpi_grid

    projects = [
        {
            "metrics": [
                {"label": "downtime", "value": 0},
                {"label": "incidents", "value": "0"},
            ],
        }
    ]
    result = _kpi_grid(projects)
    assert result is not None
    labels = {it["label"]: it["value"] for it in result["props"]["items"]}
    assert labels["downtime"] == "0"
    assert labels["incidents"] == "0"


def test_kpi_grid_skips_non_dict_project_rows():
    """A malformed (non-dict) project row must not raise AttributeError."""
    from plugins.portfolio_plugin.compose.composer import _kpi_grid

    projects = [None, "garbage", {"metrics": [{"label": "ok", "value": "1"}]}]
    result = _kpi_grid(projects)
    assert result is not None
    assert result["props"]["items"] == [{"label": "ok", "value": "1"}]


def test_stamp_dag_single_level_at_zero():
    """A single-level DAG should stamp at=0.0, not divide-by-zero or skip the field."""
    from plugins.portfolio_plugin.compose.composer import _stamp_dag_from_blocks

    blocks = [{"type": "hero", "id": "h1"}]
    dag = _stamp_dag_from_blocks(blocks)
    assert dag is not None
    assert dag["levels"][0]["at"] == 0.0


def test_stamp_dag_missing_id_logs_and_skips(caplog):
    """Blocks without an id are skipped (rendered as orphans downstream) and logged."""
    from plugins.portfolio_plugin.compose.composer import _stamp_dag_from_blocks

    blocks = [{"type": "hero", "id": "h1"}, {"type": "card"}]
    with caplog.at_level("WARNING", logger="whiskers.plugins"):
        dag = _stamp_dag_from_blocks(blocks)
    assert dag is not None
    all_nodes = [n for lvl in dag["levels"] for n in lvl["nodes"]]
    assert all_nodes == ["h1"]
    assert any("missing 'id'" in r.message for r in caplog.records)


def test_every_block_type_has_a_dag_band():
    """Drift guard: a new schema block type must be given an explicit DAG band.

    Unmapped types silently fall through to the (9, "More") catch-all and render
    below the L7 "Ask" CTA — which is how mcpSandbox/costSim regressed.
    """
    from plugins.portfolio_plugin.compose.composer import _DAG_LEVEL_BY_TYPE
    from plugins.portfolio_plugin.schema.ui_layout_schema import BLOCK_TYPES

    assert BLOCK_TYPES - set(_DAG_LEVEL_BY_TYPE) == set()


@pytest.mark.parametrize(
    "btype,level",
    [("mcpSandbox", 3), ("costSim", 4)],
)
def test_interactive_blocks_band(btype, level):
    """mcpSandbox/costSim band at L3/L4 per their schema docstrings, not the L9 fallback."""
    from plugins.portfolio_plugin.compose.composer import _stamp_dag_from_blocks

    dag = _stamp_dag_from_blocks([{"type": btype, "id": "b1"}])
    assert dag is not None
    assert dag["levels"][0]["level"] == level


def test_stamp_dag_deep_dive_band_is_single_column():
    """L6 deep dive must carry cols=1 so the renderer gives each block its own row."""
    from plugins.portfolio_plugin.compose.composer import _stamp_dag_from_blocks

    blocks = [
        {"type": "hero", "id": "h1"},
        {"type": "composite", "id": "c1"},
        {"type": "prose", "id": "p1"},
    ]
    dag = _stamp_dag_from_blocks(blocks)
    assert dag is not None
    bands = {lvl["level"]: lvl for lvl in dag["levels"]}
    assert bands[6]["cols"] == 1
    assert bands[6]["nodes"] == ["c1", "p1"]
    # Non-deep-dive bands stay unconstrained (renderer default).
    assert "cols" not in bands[0]
