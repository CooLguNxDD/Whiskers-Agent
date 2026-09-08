"""Unit tests for UI layout validation and response formatting."""

import copy
from typing import Any
import pytest

from utils.response_format import to_ui_layout


@pytest.fixture
def VALID_LAYOUT() -> dict[str, Any]:
    """Fixture providing a valid portfolio UI layout configuration."""
    return {
        "version": 1,
        "meta": {
            "audience": "default",
            "generatedAt": "2026-07-05T00:00:00Z"
        },
        "blocks": [
            {
                "type": "hero",
                "id": "hero-1",
                "props": {
                    "name": "Jane Doe",
                    "tagline": "Software Engineer",
                    "pitch": "I build things.",
                    "links": [
                        {"label": "GitHub", "href": "https://github.com/janedoe"}
                    ]
                }
            },
            {
                "type": "projectGrid",
                "id": "grid-1",
                "props": {
                    "projects": [
                        {
                            "id": "proj-1",
                            "name": "Project Alpha",
                            "summary": "The first project",
                            "tags": ["python", "pydantic"],
                            "metrics": [
                                {"label": "Stars", "value": "100"}
                            ],
                            "links": [
                                {"label": "Source", "href": "https://github.com/janedoe/alpha"}
                            ]
                        }
                    ]
                }
            },
            {
                "type": "statStrip",
                "id": "strip-1",
                "props": {
                    "stats": [
                        {"label": "Experience", "value": "5 years"}
                    ]
                }
            },
            {
                "type": "starStory",
                "id": "story-1",
                "props": {
                    "situation": "Messy codebase",
                    "task": "Refactor it",
                    "action": "Applied design patterns",
                    "result": "Clean code",
                    "tags": ["refactoring"]
                }
            },
            {
                "type": "archDiagram",
                "id": "diag-1",
                "props": {
                    "title": "Architecture Overview",
                    "kind": "mermaid",
                    "source": "graph TD; A-->B;"
                }
            },
            {
                "type": "codeSnippet",
                "id": "code-1",
                "props": {
                    "lang": "python",
                    "code": "print('hello')",
                    "caption": "Hello world in python"
                }
            },
            {
                "type": "prose",
                "id": "prose-1",
                "props": {
                    "markdown": "# About Me\nWelcome!"
                }
            }
        ]
    }


def test_valid_layout_passthrough_bare(VALID_LAYOUT):
    """Bare valid layout passes through successfully and returns 7 blocks."""
    res = to_ui_layout(VALID_LAYOUT, {})
    assert isinstance(res, dict)
    assert res["version"] == 1
    assert len(res["blocks"]) == 7


def test_valid_layout_unwraps_status_wrapper(VALID_LAYOUT):
    """Layout wrapped inside status and layout keys is unwrapped and validated."""
    wrapped = {"status": "ok", "layout": VALID_LAYOUT}
    res = to_ui_layout(wrapped, {})
    assert isinstance(res, dict)
    assert "status" not in res
    assert res["version"] == 1
    assert len(res["blocks"]) == 7


def test_unknown_block_dropped(VALID_LAYOUT):
    """Unknown block types are silently filtered out prior to validation."""
    layout = copy.deepcopy(VALID_LAYOUT)
    layout["blocks"].append({"type": "iframe", "id": "x", "props": {}})
    res = to_ui_layout(layout, {})
    assert isinstance(res, dict)
    assert "status" not in res
    assert len(res["blocks"]) == 7
    for block in res["blocks"]:
        assert block["type"] != "iframe"


def test_invalid_layout_error_envelope(VALID_LAYOUT):
    """Validation failure returns an error envelope with details."""
    layout = copy.deepcopy(VALID_LAYOUT)
    del layout["version"]
    res = to_ui_layout(layout, {})
    assert isinstance(res, dict)
    assert res["status"] == "error"
    assert res["error"] == "invalid_ui_layout"
    assert isinstance(res["details"], list)
    assert len(res["details"]) > 0


def test_bad_kind_rejected(VALID_LAYOUT):
    """Invalid kind in diagram props triggers validation rejection."""
    layout = copy.deepcopy(VALID_LAYOUT)
    diag_block = next(b for b in layout["blocks"] if b["type"] == "archDiagram")
    diag_block["props"]["kind"] = "script"
    res = to_ui_layout(layout, {})
    assert isinstance(res, dict)
    assert res["status"] == "error"
    assert res["error"] == "invalid_ui_layout"
    assert isinstance(res["details"], list)
    assert len(res["details"]) > 0


def test_fish_tank_round_trip(VALID_LAYOUT):
    """fishTank block with valid specimens round-trips through to_ui_layout."""
    layout = copy.deepcopy(VALID_LAYOUT)
    layout["blocks"].append(
        {
            "type": "fishTank",
            "id": "tank-1",
            "props": {
                "renderer": "webgl",
                "fish": [
                    {
                        "slug": "oct-mcp",
                        "title": "Whiskers Agent",
                        "species": "ai",
                        "size": 0.9,
                        "depth": 0.1,
                        "speed": 0.5,
                        "glow": 0.8,
                        "school": 0,
                    }
                ],
            },
        }
    )
    res = to_ui_layout(layout, {})
    assert isinstance(res, dict)
    assert res.get("status") != "error"
    types = [b["type"] for b in res["blocks"]]
    assert "fishTank" in types


def test_fish_tank_size_out_of_range_rejected(VALID_LAYOUT):
    layout = copy.deepcopy(VALID_LAYOUT)
    layout["blocks"].append(
        {
            "type": "fishTank",
            "id": "tank-1",
            "props": {
                "fish": [
                    {
                        "slug": "x",
                        "title": "X",
                        "species": "ai",
                        "size": 1.5,
                    }
                ],
            },
        }
    )
    res = to_ui_layout(layout, {})
    assert res["status"] == "error"
    assert res["error"] == "invalid_ui_layout"
    blob = str(res.get("details") or [])
    assert "size" in blob or "fish" in blob


@pytest.mark.asyncio
async def test_apply_shape_end_to_end(VALID_LAYOUT):
    """End-to-end async shape application properly handles layout response format."""
    from utils.response_shape import apply_shape_async
    res = await apply_shape_async(
        {"status": "ok", "layout": VALID_LAYOUT},
        {
            "offload_minio": False,
            "normalize": False,
            "strip_base64": False,
            "strip_hex": False,
            "strip_empty": False,
            "response_format": "ui_layout",
        },
        tool_name="emit_layout",
    )
    assert isinstance(res, dict)
    assert "status" not in res
    assert res["version"] == 1
    assert len(res["blocks"]) == 7


def test_optional_absent_not_null(VALID_LAYOUT):
    """Optional fields that are absent in input are omitted from output dict."""
    layout = copy.deepcopy(VALID_LAYOUT)
    # Pitch is optional under hero props
    del layout["blocks"][0]["props"]["pitch"]
    res = to_ui_layout(layout, {})
    assert isinstance(res, dict)
    assert "status" not in res
    assert "pitch" not in res["blocks"][0]["props"]


# ── cross-repo mirror parity (Phase 5) ──────────────────────────────────────
#
# CatPortfolio/design/mirror-manifest.json is canonical and is itself asserted
# against CatPortfolio/src/content/schema.ts's own exported constants by
# CatPortfolio/scripts/__tests__/mirror-drift.test.ts. OCT's CI can't read
# that repo's path, so test/fixtures/mirror-manifest.json is a human-synced
# vendored copy -- these tests catch drift between THIS repo's Python source
# of truth (plugins/portfolio_plugin/schema/ui_layout_schema.py) and that vendored snapshot. They do
# NOT catch drift between the vendored snapshot and the real CatPortfolio
# manifest -- that's caught on the CatPortfolio side, and by code review when
# either file changes without the other.

import json
from pathlib import Path


def _vendored_mirror_manifest() -> dict:
    # Vendored mirror lives alongside this test (plugin-local fixture).
    fixture = Path(__file__).resolve().parent / "fixtures" / "mirror-manifest.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_theme_allowlist_matches_vendored_manifest():
    """plugins.portfolio_plugin.schema.ui_layout_schema.THEME_VAR_ALLOWLIST must match the vendored
    CatPortfolio mirror-manifest.json's themeVarAllowlist exactly. If this
    fails, either this repo's allowlist changed and the vendored copy (and
    the real CatPortfolio/design/mirror-manifest.json) needs updating, or
    the vendored copy drifted from CatPortfolio and needs re-syncing."""
    from plugins.portfolio_plugin.schema.ui_layout_schema import THEME_VAR_ALLOWLIST

    manifest = _vendored_mirror_manifest()
    declared = set(manifest.get("themeVarAllowlist") or [])
    assert declared == THEME_VAR_ALLOWLIST, (
        "THEME_VAR_ALLOWLIST has drifted from the vendored CatPortfolio "
        "mirror manifest (test/fixtures/mirror-manifest.json). Update both "
        "this file and CatPortfolio/design/mirror-manifest.json + "
        "src/content/schema.ts's THEME_VAR_ALLOWLIST in the same PR."
    )


def test_composite_kinds_match_vendored_manifest():
    """plugins.portfolio_plugin.schema.ui_layout_schema's COMPOSITE_LEAF_KINDS / COMPOSITE_CONTAINER_KINDS
    / COMPOSITE_MAX_DEPTH / COMPOSITE_MAX_NODES must match the vendored
    CatPortfolio mirror-manifest.json exactly -- these are the server-side
    validation caps; drift here means the Python side accepts (or rejects)
    composites the frontend disagrees with."""
    from plugins.portfolio_plugin.schema.ui_layout_schema import (
        COMPOSITE_CONTAINER_KINDS,
        COMPOSITE_LEAF_KINDS,
        COMPOSITE_MAX_DEPTH,
        COMPOSITE_MAX_NODES,
    )

    manifest = _vendored_mirror_manifest()
    assert set(manifest.get("compositeLeafKinds") or []) == COMPOSITE_LEAF_KINDS
    assert set(manifest.get("compositeContainerKinds") or []) == COMPOSITE_CONTAINER_KINDS
    assert manifest.get("compositeMaxDepth") == COMPOSITE_MAX_DEPTH
    assert manifest.get("compositeMaxNodes") == COMPOSITE_MAX_NODES


def test_vendored_manifest_block_types_match_ui_layout_schema():
    """The vendored manifest's pythonMirrorBlockTypes must match this repo's
    actual BLOCK_TYPES -- catches the case where a new Python block type
    ships without ever syncing the CatPortfolio-side manifest."""
    from plugins.portfolio_plugin.schema.ui_layout_schema import BLOCK_TYPES

    manifest = _vendored_mirror_manifest()
    declared = set(manifest.get("pythonMirrorBlockTypes") or [])
    assert declared == set(BLOCK_TYPES)
