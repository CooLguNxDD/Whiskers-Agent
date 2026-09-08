"""Zero-paste display copy: inventory summary is seed, not page output."""

from __future__ import annotations

from unittest.mock import patch

from plugins.portfolio_plugin.compose.display_copy import (
    author_display_copy,
    body_by_slug_from_layout,
    build_display_copy_by_slug,
    get_display_copy_caps,
    is_inventory_dump,
    rewrite_layout_project_copy,
)
from plugins.portfolio_plugin.compose.fish import build_fish_specimens
from plugins.portfolio_plugin.compose.quality import _truncate_at_boundary

_FISOUL_SUMMARY = (
    "Multiplayer Co-op Apex Predator Survival & Evolution Game in Unity 6. "
    "Fisoul is an action-survival multiplayer game where players inhabit juvenile "
    "apex fish navigating perilous aquatic ecosystems. Grow through an eat-or-be-eaten "
    "loop, unlock discrete evolution tiers, mutate modular anatomical parts, and master "
    "souls-like combat mechanics with high-risk survival resource management."
)


def _fisoul() -> dict:
    return {
        "slug": "fisoul",
        "name": "Fisoul",
        "summary": _FISOUL_SUMMARY,
        "tags": ["C#", "Unity", "multiplayer"],
        "metrics": [],
        "links": [{"label": "github", "href": "https://github.com/CooLguNxDD/Fisoul"}],
        "context_sources": [
            {
                "id": "disc:github:CooLguNxDD/Fisoul",
                "ref": "CooLguNxDD/Fisoul",
                "kind": "github",
            }
        ],
    }


def test_fish_blurb_not_raw_summary_prefix():
    blurb = author_display_copy(_fisoul(), form="fish_blurb")
    assert blurb
    raw = _truncate_at_boundary(_FISOUL_SUMMARY, 200)
    assert blurb != raw
    assert blurb != _FISOUL_SUMMARY[:200]
    # Structured reframe carries name or tag cue.
    assert "Fisoul" in blurb or "Unity" in blurb or "multiplayer" in blurb.lower()
    assert len(blurb) <= 200


def test_card_body_not_raw_summary():
    body = author_display_copy(_fisoul(), form="card_body")
    assert body
    assert body != _FISOUL_SUMMARY
    assert body != _truncate_at_boundary(_FISOUL_SUMMARY, 480)
    assert "Fisoul" in body


def test_preferred_body_beats_inventory_summary():
    authored = (
        "Authored Unity 6 co-op survival with modular evolution and souls-like combat loops."
    )
    blurb = author_display_copy(
        _fisoul(), form="fish_blurb", preferred_body=authored
    )
    assert blurb
    assert "Authored Unity 6" in blurb or "modular evolution" in blurb
    assert blurb != _truncate_at_boundary(_FISOUL_SUMMARY, 200)


def test_stub_summary_without_metadata_returns_none():
    proj = {
        "slug": "empty",
        "name": "",
        "summary": "GitHub repository foo/bar",
        "tags": [],
        "metrics": [],
    }
    assert author_display_copy(proj, form="fish_blurb") is None


def test_job_tokens_bias_claim_selection():
    # With combat-focused tokens, prefer the combat claim when present.
    blurb = author_display_copy(
        _fisoul(),
        form="fish_blurb",
        job_tokens={"combat", "souls", "unity", "game"},
    )
    assert blurb
    low = blurb.lower()
    assert "unity" in low or "combat" in low or "fisoul" in low


def test_body_by_slug_from_layout():
    layout = {
        "blocks": [
            {
                "type": "card",
                "id": "card-fisoul",
                "props": {"title": "Fisoul", "body": "Rewritten card body for fish handoff."},
            },
            {"type": "hero", "id": "hero-1", "props": {"title": "x"}},
        ]
    }
    m = body_by_slug_from_layout(layout)
    assert m.get("fisoul") == "Rewritten card body for fish handoff."


def test_fish_specimens_use_authored_blurb_not_summary_slice():
    fish = build_fish_specimens([_fisoul()])
    assert fish
    blurb = fish[0].get("blurb")
    assert blurb
    assert blurb != _truncate_at_boundary(_FISOUL_SUMMARY, 200)
    assert blurb != _FISOUL_SUMMARY[:200]


def test_fish_specimens_prefer_body_by_slug():
    fish = build_fish_specimens(
        [_fisoul()],
        body_by_slug={
            "fisoul": (
                "Job-framed Unity multiplayer survival — modular evolution tiers "
                "and high-risk combat resource loops."
            )
        },
    )
    assert fish
    blurb = fish[0].get("blurb") or ""
    assert "Job-framed" in blurb or "modular evolution" in blurb
    assert blurb != _truncate_at_boundary(_FISOUL_SUMMARY, 200)


def test_markdown_inventory_dump_preferred_body_is_ignored():
    """Agent paste of summary with markdown chrome must not become the blurb."""
    dump = (
        "> **Multiplayer Co-op Apex Predator Survival & Evolution Game in Unity 6** "
        "**Fisoul** is an action-survival multiplayer game where players inhabit "
        "juvenile apex fish navigating perilous aquatic ecosystems."
    )
    assert is_inventory_dump(dump, _fisoul())
    blurb = author_display_copy(_fisoul(), form="fish_blurb", preferred_body=dump)
    assert blurb
    assert blurb != _truncate_at_boundary(_FISOUL_SUMMARY, 200)
    assert not blurb.startswith("Multiplayer Co-op Apex Predator")
    assert "Fisoul" in blurb or "Unity" in blurb or "C#" in blurb
    # Structured reframe uses em dash cue when tags exist.
    assert " — " in blurb or len(blurb) < 160


def test_rewrite_layout_rewrites_dumped_card_bodies():
    dump = (
        "> **Multiplayer Co-op Apex Predator Survival & Evolution Game in Unity 6** "
        f"**Fisoul** is an action-survival multiplayer game where players inhabit "
        "juvenile apex fish navigating perilous aquatic ecosystems."
    )
    layout = {
        "version": 1,
        "blocks": [
            {
                "type": "card",
                "id": "card-fisoul",
                "props": {"title": "CooLguNxDD/Fisoul", "body": dump},
            }
        ],
    }
    out, bodies = rewrite_layout_project_copy(layout, [_fisoul()])
    body = (out["blocks"][0]["props"].get("body") or "")
    assert body
    assert body != dump
    assert not is_inventory_dump(body, _fisoul())
    assert bodies.get("fisoul") == body


def test_rewrite_layout_keeps_distinct_agent_prose():
    distinct = (
        "Fisoul — Unity multiplayer. Built souls-like combat loops and modular "
        "evolution tiers for a co-op survival game."
    )
    layout = {
        "blocks": [
            {
                "type": "card",
                "id": "card-fisoul",
                "props": {"title": "Fisoul", "body": distinct},
            }
        ],
    }
    out, bodies = rewrite_layout_project_copy(layout, [_fisoul()])
    body = out["blocks"][0]["props"]["body"]
    assert "souls-like combat" in body or "modular evolution" in body
    assert bodies.get("fisoul")


def test_display_copy_caps_from_settings():
    with patch(
        "plugins.portfolio_plugin.plugin_config.SETTINGS",
        {
            "display_copy": {
                "fish_blurb_max_chars": 100,
                "card_body_max_chars": 480,
                "description_max_chars": 600,
            }
        },
    ):
        caps = get_display_copy_caps()
        assert caps["fish_blurb"] == 100
        blurb = author_display_copy(_fisoul(), form="fish_blurb")
        assert blurb
        assert len(blurb) <= 100


def test_build_display_copy_by_slug_has_dual_granularity():
    blobs = build_display_copy_by_slug([_fisoul()])
    assert "fisoul" in blobs
    assert "fish_blurb" in blobs["fisoul"]
    assert "card_body" in blobs["fisoul"]
    assert not is_inventory_dump(blobs["fisoul"]["fish_blurb"], _fisoul())
