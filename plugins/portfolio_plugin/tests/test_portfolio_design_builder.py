"""
Unit tests for portfolio design-builder (compose_custom_layout + design_layout).
"""

import pytest
from unittest.mock import patch, AsyncMock

from plugins.portfolio_plugin.compose.composer import compose_custom_layout, _merge_design_spec
from plugins.portfolio_plugin.MCPTools.portfolio_tools import design_layout


@pytest.fixture
def mock_projects():
    """Minimal project list with a headline metric."""
    return [
        {
            "slug": "oct",
            "name": "Whiskers Agent",
            "summary": "MCP server with agent intelligence",
            "tags": ["python", "mcp"],
            "metrics": [
                {"label": "context reduction", "value": "~98.7%", "headline": True},
            ],
            "links": [{"label": "GitHub", "href": "https://github.com/test/oct"}],
        }
    ]


@pytest.fixture
def mock_stories():
    """One valid STAR row."""
    return [
        {
            "id": 1,
            "metadata": {
                "situation": "S",
                "task": "T",
                "action": "A",
                "result": "R",
                "tags": ["infra"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_mixed_sections_compose_valid_layout(mock_projects, mock_stories):
    """Named sections + literal prose/arch compose a schema-valid layout."""
    spec = {
        "audience": "peer",
        "theme": "neon",
        "star_query": "architecture",
        "star_k": 2,
        "max_projects": 4,
        "sections": [
            "hero",
            {
                "type": "prose",
                "id": "intro-1",
                "props": {"markdown": "Hello from the design builder."},
            },
            "statStrip",
            {
                "type": "archDiagram",
                "id": "arch-1",
                "props": {
                    "title": "OCT",
                    "kind": "mermaid",
                    "source": "graph TD; A-->B",
                },
            },
            "projectGrid",
            # portfolio_star retired — starStory needs authored S/T/A/R props.
            {
                "type": "starStory",
                "id": "star-1",
                "props": {
                    "situation": "S",
                    "task": "T",
                    "action": "A",
                    "result": "R",
                },
            },
            {
                "type": "codeSnippet",
                "id": "code-1",
                "props": {"lang": "python", "code": "print('hi')"},
            },
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = mock_stories

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert errors == []
    assert layout is not None
    assert layout["version"] == 1
    assert layout["meta"]["audience"] == "peer"
    assert layout["meta"]["theme"] == "neon"
    types = [b["type"] for b in layout["blocks"]]
    assert "hero" in types
    assert "prose" in types
    assert "statStrip" in types
    assert "archDiagram" in types
    assert "projectGrid" in types
    assert "starStory" in types
    assert "codeSnippet" in types


@pytest.mark.asyncio
async def test_invalid_block_returns_errors(mock_projects, mock_stories):
    """Malformed literal block → (None, errors) with path strings."""
    spec = {
        "audience": "default",
        "sections": [
            "hero",
            {
                "type": "prose",
                "id": "bad",
                # missing props.markdown
                "props": {},
            },
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = mock_stories

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert layout is None
    assert errors
    assert any("markdown" in e or "props" in e for e in errors)


@pytest.mark.asyncio
async def test_incomplete_project_grid_object_rehydrates_from_db(mock_projects):
    """Agent wraps projectGrid as empty object — fill projects from DB."""
    spec = {
        "audience": "peer",
        "theme": "neon",
        "sections": [
            {
                "type": "projectGrid",
                "id": "projects-1",
                "props": {},
            }
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = []

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert errors == []
    assert layout is not None
    assert layout["meta"]["theme"] == "neon"
    grids = [b for b in layout["blocks"] if b["type"] == "projectGrid"]
    assert len(grids) == 1
    assert grids[0]["id"] == "projects-1"
    assert grids[0]["props"]["projects"]
    assert grids[0]["props"]["projects"][0]["name"] == "Whiskers Agent"


@pytest.mark.asyncio
async def test_thin_arch_diagram_auto_fills_mermaid(mock_projects):
    """archDiagram with only query → title/kind/source filled (no validation fail)."""
    spec = {
        "audience": "peer",
        "theme": "neon",
        "sections": [
            {
                "type": "archDiagram",
                "id": "arch-1",
                "props": {"query": "architecture diagram"},
            },
            {
                "type": "projectGrid",
                "id": "projects-1",
                "props": {},
            },
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = []

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert errors == []
    assert layout is not None
    types = [b["type"] for b in layout["blocks"]]
    assert types == ["archDiagram", "projectGrid"]
    arch = layout["blocks"][0]
    assert arch["id"] == "arch-1"
    assert arch["props"]["title"] == "architecture diagram"
    assert arch["props"]["kind"] == "mermaid"
    assert "graph TD" in arch["props"]["source"]
    assert "Whiskers Agent" in arch["props"]["source"]
    assert layout["blocks"][1]["props"]["projects"]


@pytest.mark.asyncio
async def test_complete_arch_diagram_not_overwritten(mock_projects):
    """Fully specified archDiagram props must pass through unchanged."""
    source = "graph TD; CustomA-->CustomB"
    spec = {
        "sections": [
            {
                "type": "archDiagram",
                "id": "arch-custom",
                "props": {
                    "title": "Custom Arch",
                    "kind": "mermaid",
                    "source": source,
                },
            }
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = []

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert errors == []
    assert layout is not None
    arch = layout["blocks"][0]
    assert arch["props"]["title"] == "Custom Arch"
    assert arch["props"]["source"] == source


@pytest.mark.asyncio
async def test_thin_arch_diagram_svg_motif_dispatches_to_svg_render(mock_projects):
    """kind='svg' + props.motif -- Phase 6a -- must dispatch to svg_render
    and land real SVG markup (not a mermaid string) in props.source."""
    spec = {
        "theme": "neon",
        "sections": [
            {
                "type": "archDiagram",
                "id": "arch-1",
                "props": {"title": "Proof points", "kind": "svg", "motif": "stack"},
            }
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = []

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert errors == []
    assert layout is not None
    arch = layout["blocks"][0]
    assert arch["props"]["kind"] == "svg"
    assert arch["props"]["source"].startswith("<svg")
    assert "Whiskers Agent" in arch["props"]["source"]  # grounded in real project data
    assert "graph TD" not in arch["props"]["source"]


@pytest.mark.asyncio
async def test_thin_arch_diagram_svg_no_motif_downgrades_to_mermaid(mock_projects):
    """kind='svg' with neither a real source nor a motif must downgrade to
    kind='mermaid' rather than tag mermaid syntax as kind='svg' (which the
    frontend would render as literal text inside an <img>)."""
    spec = {
        "sections": [
            {
                "type": "archDiagram",
                "id": "arch-1",
                "props": {"title": "Arch", "kind": "svg"},
            }
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = []

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert errors == []
    arch = layout["blocks"][0]
    assert arch["props"]["kind"] == "mermaid"
    assert "graph TD" in arch["props"]["source"]


@pytest.mark.asyncio
async def test_thin_arch_diagram_svg_unknown_motif_downgrades_to_mermaid(mock_projects):
    spec = {
        "sections": [
            {
                "type": "archDiagram",
                "id": "arch-1",
                "props": {"title": "Arch", "kind": "svg", "motif": "not-a-real-motif"},
            }
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = []

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert errors == []
    arch = layout["blocks"][0]
    assert arch["props"]["kind"] == "mermaid"


@pytest.mark.asyncio
async def test_unknown_composed_section_errors(mock_projects, mock_stories):
    """Unknown string section name is rejected with a clear path."""
    spec = {"sections": ["hero", "notARealSection"]}
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = []

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert layout is None
    assert any("sections[1]" in e and "notARealSection" in e for e in errors)


def test_preset_merge_order_and_unknown_preset():
    """Spec overrides preset; unknown preset is ignored gracefully."""
    fake_settings = {
        "layout_presets": {
            "showcase": {
                "audience": "peer",
                "theme": "neon",
                "sections": ["hero", "projectGrid"],
                "max_projects": 8,
            }
        }
    }
    with patch("plugins.portfolio_plugin.compose.composer.SETTINGS", fake_settings):
        merged = _merge_design_spec(
            {"theme": "paper", "max_projects": 3},
            preset_name="showcase",
        )
        assert merged["audience"] == "peer"
        assert merged["theme"] == "paper"  # spec wins
        assert merged["max_projects"] == 3
        assert merged["sections"] == ["hero", "projectGrid"]

        ignored = _merge_design_spec({"audience": "recruiter"}, preset_name="nope")
        assert ignored["audience"] == "recruiter"
        assert "sections" not in ignored


@pytest.mark.asyncio
async def test_theme_absent_omitted_exclude_none(mock_projects, mock_stories):
    """Without theme, meta has no theme key (exclude_none dump path)."""
    spec = {"audience": "default", "sections": ["hero"]}
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = []

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert errors == []
    assert layout is not None
    assert "theme" not in layout["meta"]


@pytest.mark.asyncio
async def test_design_layout_tool_ok_and_error(mock_projects, mock_stories):
    """Tool wraps composer: ok → layout key; bad → status error + hint."""
    ok_layout = {
        "version": 1,
        "meta": {"audience": "peer", "generatedAt": "2026-01-01T00:00:00Z", "theme": "neon"},
        "blocks": [],
    }
    with patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.composer.compose_custom_layout",
        new_callable=AsyncMock,
    ) as mock_compose, patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id"
    ) as mock_tid:
        mock_tid.get.return_value = 1
        mock_compose.return_value = (ok_layout, [])
        res = await design_layout(spec={"theme": "neon"}, preset="showcase")
        assert res == {"status": "ok", "layout": ok_layout}
        mock_compose.assert_called_once_with(
            spec={"theme": "neon"},
            tenant_id=1,
            preset="showcase",
        )

        mock_compose.return_value = (None, ["blocks.0.props.markdown: Field required"])
        bad = await design_layout(spec={"sections": [{"type": "prose", "id": "x", "props": {}}]})
        assert bad["status"] == "error"
        assert bad["errors"]
        assert "hint" in bad

        missing = await design_layout(spec=None)
        assert missing["status"] == "error"


@pytest.mark.asyncio
async def test_design_layout_empty_args_has_recoverable_message():
    """Empty-args call (planner forgot spec/preset) must surface a 'message'
    containing wording that trips is_recoverable's missing/required check,
    instead of the composer being called at all."""
    with patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.composer.compose_custom_layout",
        new_callable=AsyncMock,
    ) as mock_compose:
        res = await design_layout()
        assert res["status"] == "error"
        assert "message" in res
        msg = res["message"].lower()
        assert "required" in msg or "missing" in msg
        mock_compose.assert_not_called()


@pytest.mark.asyncio
async def test_design_layout_preset_only_no_spec(mock_projects):
    """preset alone (no spec) must reach the composer, not error out early."""
    ok_layout = {
        "version": 1,
        "meta": {"audience": "peer", "generatedAt": "2026-01-01T00:00:00Z"},
        "blocks": [],
    }
    with patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.composer.compose_custom_layout",
        new_callable=AsyncMock,
    ) as mock_compose, patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id"
    ) as mock_tid:
        mock_tid.get.return_value = 1
        mock_compose.return_value = (ok_layout, [])
        res = await design_layout(preset="showcase")
        assert res == {"status": "ok", "layout": ok_layout}
        mock_compose.assert_called_once_with(spec={}, tenant_id=1, preset="showcase")


@pytest.mark.asyncio
async def test_design_layout_spec_as_json_string():
    """spec passed as a JSON-encoded string is parsed and forwarded as a dict."""
    ok_layout = {
        "version": 1,
        "meta": {"audience": "peer", "generatedAt": "2026-01-01T00:00:00Z"},
        "blocks": [],
    }
    with patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.composer.compose_custom_layout",
        new_callable=AsyncMock,
    ) as mock_compose, patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id"
    ) as mock_tid:
        mock_tid.get.return_value = 1
        mock_compose.return_value = (ok_layout, [])
        res = await design_layout(spec='{"audience": "peer"}')
        assert res == {"status": "ok", "layout": ok_layout}
        mock_compose.assert_called_once_with(
            spec={"audience": "peer"}, tenant_id=1, preset=""
        )


@pytest.mark.asyncio
async def test_design_layout_spec_invalid_json_string_errors():
    """A non-JSON string spec is rejected with a clear, recoverable message."""
    res = await design_layout(spec="not json at all")
    assert res["status"] == "error"
    assert "message" in res


@pytest.mark.asyncio
async def test_design_layout_validation_error_has_message():
    """On composer validation failure, the joined errors also land in 'message'
    so validator.py's message-only read (empty response fallback) never fires."""
    with patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.composer.compose_custom_layout",
        new_callable=AsyncMock,
    ) as mock_compose, patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id"
    ) as mock_tid:
        mock_tid.get.return_value = 1
        mock_compose.return_value = (None, ["blocks.0.props.markdown: Field required"])
        res = await design_layout(spec={"sections": [{"type": "prose", "id": "x", "props": {}}]})
        assert res["status"] == "error"
        assert "message" in res
        assert "blocks.0.props.markdown" in res["message"]


@pytest.mark.asyncio
async def test_explicit_empty_sections_rejected(mock_projects):
    """Planner collapse to design_layout(sections=[]) must not return ok empty layout."""
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = []
        layout, errors = await compose_custom_layout(
            {"audience": "peer", "theme": "neon", "sections": []},
            tenant_id=1,
        )
    assert layout is None
    assert errors
    assert any("empty" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_agentic_blocks_aggregate_meta_sources(mock_projects):
    """Literal blocks carrying build_layout_block's ``_sourceRefs`` marker get
    their citations aggregated into layout.meta.sources, and the marker key
    itself never survives into the validated block output."""
    spec = {
        "audience": "peer",
        "sections": [
            {
                "type": "prose",
                "id": "grounded-1",
                "props": {"markdown": "Grounded copy about OCT."},
                "_sourceRefs": ["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
            },
            {
                "type": "codeSnippet",
                "id": "code-1",
                "props": {"lang": "python", "code": "print('hi')"},
                "_sourceRefs": ["disc:github:CooLguNxDD/OpenCat-Mcp-Full", "disc:notion:page-1"],
            },
        ],
    }
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = []

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert errors == []
    assert layout is not None
    sources = layout["meta"].get("sources")
    assert sources is not None
    refs = {s["ref"] for s in sources}
    assert refs == {"disc:github:CooLguNxDD/OpenCat-Mcp-Full", "disc:notion:page-1"}
    # marker key must not leak into the validated block output
    for block in layout["blocks"]:
        assert "_sourceRefs" not in block


@pytest.mark.asyncio
async def test_no_agentic_blocks_omits_meta_sources(mock_projects, mock_stories):
    """Plain named-section-only specs (no _sourceRefs anywhere) never gain a
    meta.sources key — no behavior change for the pre-existing composition path."""
    spec = {"audience": "peer", "sections": ["hero", "projectGrid"]}
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.discovery.index.search_context", new_callable=AsyncMock
    ) as mock_search:
        mock_list.return_value = mock_projects
        mock_search.return_value = mock_stories

        layout, errors = await compose_custom_layout(spec, tenant_id=1)

    assert errors == []
    assert "sources" not in layout["meta"]


def test_tokens_to_theme_overrides_all_keys_allowlisted():
    """Regression: design_systems/default/tokens.json's real keys ("--accent",
    "--surface", "colors.domain-ai", ...) don't survive a bare "--" strip --
    they need the alias map. Before the fix this returned {} every time and
    layout_jury._score_voice_brand's themeOverrides bonus was unreachable."""
    from plugins.portfolio_plugin.render.design_system import (
        load_design_system,
        tokens_to_theme_overrides,
    )
    from plugins.portfolio_plugin.schema.ui_layout_schema import THEME_VAR_ALLOWLIST, sanitize_theme_overrides

    ds = load_design_system("default")
    out = tokens_to_theme_overrides(ds.get("tokens"))

    assert out, "tokens_to_theme_overrides must not be empty for the shipped default tokens"
    assert set(out) <= THEME_VAR_ALLOWLIST
    # producer-side sanitize must be idempotent -- consumer re-sanitizing is a no-op
    assert sanitize_theme_overrides(out) == out


def test_tokens_to_theme_overrides_unknown_and_empty_input():
    from plugins.portfolio_plugin.render.design_system import tokens_to_theme_overrides

    assert tokens_to_theme_overrides(None) == {}
    assert tokens_to_theme_overrides({}) == {}
    # keys with no alias and not already allowlisted are dropped, not raised
    assert tokens_to_theme_overrides({"--totally-unknown-token": "red"}) == {}


def test_system_prompt_has_composite_plane():
    """The layout agent's system prompt must document the composite DSL
    (kinds, caps, worked example) -- without this plane nothing in the
    pipeline ever emits a composite (Phase 2.2)."""
    from plugins.portfolio_plugin.render.design_system import compose_layout_system_prompt

    prompt = compose_layout_system_prompt(design_system_id="default")
    assert "COMPOSITE DSL" in prompt
    for kind in ("grid", "stack", "split", "cards"):
        assert kind in prompt
    for kind in ("metric", "text", "quote", "badgeCloud"):
        assert kind in prompt
    assert "depth <= 3" in prompt
    assert "40 total nodes" in prompt
    assert "http(s)://" in prompt
    assert "band" in prompt  # Phase 2.1 OUTPUT plane addition
    assert "VISUALS" in prompt  # Phase 6d
    for motif in ("timeline", "stack", "metric_ring", "topology"):
        assert motif in prompt


@pytest.mark.asyncio
async def test_design_layout_patch_path(mock_projects, mock_stories):
    """design_layout with base_layout returns patched layout + patched_block_ids."""
    base = {
        "version": 1,
        "meta": {
            "audience": "peer",
            "theme": "neon",
            "generatedAt": "2026-01-01T00:00:00Z",
        },
        "blocks": [
            {
                "type": "hero",
                "id": "h1",
                "props": {"name": "A", "tagline": "t", "pitch": "p", "links": []},
            },
            {
                "type": "quickActions",
                "id": "qa1",
                "props": {"actions": [{"label": "Ask", "prompt": "hi"}]},
            },
        ],
    }
    from plugins.portfolio_plugin.compose.composer import _stamp_dag_from_blocks

    base["meta"]["dag"] = _stamp_dag_from_blocks(base["blocks"])
    prose = {
        "type": "prose",
        "id": "prose-1",
        "props": {"markdown": "Patched in."},
    }
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects", new_callable=AsyncMock
    ) as mock_list, patch(
        "plugins.portfolio_plugin.compose.composer.rank_projects_by_query",
        new_callable=AsyncMock,
        side_effect=lambda projects, *a, **k: projects,
    ), patch(
        "plugins.portfolio_plugin.discovery.index.search_context",
        new_callable=AsyncMock,
    ) as mock_search, patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id"
    ) as mock_tid:
        mock_list.return_value = mock_projects
        mock_search.return_value = mock_stories
        mock_tid.get.return_value = 1
        out = await design_layout(
            spec={"base_layout": base, "sections": [prose], "audience": "peer"}
        )
    assert out["status"] == "ok", out
    assert "prose-1" in out.get("patched_block_ids", [])
    ids = [b["id"] for b in out["layout"]["blocks"]]
    assert "h1" in ids and "prose-1" in ids
    assert out["layout"]["meta"]["mode"] == "patched"


@pytest.mark.asyncio
async def test_design_layout_patch_error_shape():
    """Empty sections on a patch must return structured error."""
    with patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id"
    ) as mock_tid:
        mock_tid.get.return_value = 1
        out = await design_layout(
            spec={
                "base_layout": {"version": 1, "meta": {}, "blocks": []},
                "sections": [],
            }
        )
    assert out["status"] == "error"
    assert out.get("errors")
