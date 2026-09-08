"""Unit tests for incremental layout patch merge (compose/patch.py)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from plugins.portfolio_plugin.compose.patch import (
    merge_layout_blocks,
    merge_layout_meta,
    merge_dag_bands,
    compose_patch_layout,
    insert_before_cta,
)


def _hero(hid="h1"):
    return {
        "type": "hero",
        "id": hid,
        "props": {"name": "A", "tagline": "t", "pitch": "p", "links": []},
    }


def _qa(qid="qa1"):
    return {
        "type": "quickActions",
        "id": qid,
        "props": {"actions": [{"label": "Ask", "prompt": "hi"}]},
    }


def _prose(pid="prose-1", md="Hello"):
    return {"type": "prose", "id": pid, "props": {"markdown": md}}


def test_merge_by_id_preserves_position():
    base = [_hero(), {"type": "card", "id": "card-a", "props": {"title": "A"}}, _qa()]
    patch = [{"type": "card", "id": "card-a", "props": {"title": "A2"}}]
    blocks, patched, warnings = merge_layout_blocks(base, patch)
    assert patched == ["card-a"]
    assert blocks[1]["props"]["title"] == "A2"
    assert blocks[0]["id"] == "h1"
    assert blocks[-1]["type"] == "quickActions"
    assert not warnings


def test_append_lands_before_quick_actions():
    base = [_hero(), _qa()]
    patch = [_prose()]
    blocks, patched, _ = merge_layout_blocks(base, patch)
    assert patched == ["prose-1"]
    assert [b["id"] for b in blocks] == ["h1", "prose-1", "qa1"]


def test_update_only_drops_unknown():
    base = [_hero()]
    patch = [_prose()]
    blocks, patched, warnings = merge_layout_blocks(base, patch, patch_mode="update_only")
    assert patched == []
    assert len(blocks) == 1
    assert any("update_only" in w for w in warnings)


def test_layout_hint_key_merge_keeps_span():
    base = [
        {
            "type": "card",
            "id": "c1",
            "props": {"title": "T"},
            "layout": {"span": 6, "order": 1},
        }
    ]
    patch = [{"type": "card", "id": "c1", "props": {"title": "T2"}, "layout": {"order": 3}}]
    blocks, _, _ = merge_layout_blocks(base, patch)
    assert blocks[0]["layout"]["span"] == 6
    assert blocks[0]["layout"]["order"] == 3
    assert blocks[0]["props"]["title"] == "T2"


def test_meta_sources_union_dedupe():
    base_meta = {"theme": "neon", "audience": "peer", "sources": [{"ref": "a"}, {"ref": "b"}]}
    patch_meta = {"sources": [{"ref": "b"}, {"ref": "c"}], "theme": ""}
    out = merge_layout_meta(base_meta, patch_meta, patched_ids=["x"])
    assert out["theme"] == "neon"
    assert out["audience"] == "peer"
    assert out["mode"] == "patched"
    assert out["patchedBlockIds"] == ["x"]
    refs = [s["ref"] for s in out["sources"]]
    assert refs == ["a", "b", "c"]


def test_merge_dag_bands_preserves_base_and_files_new():
    base_blocks = [
        _hero(),
        {"type": "card", "id": "card-a", "props": {"title": "A"}},
        _qa(),
    ]
    from plugins.portfolio_plugin.compose.composer import _stamp_dag_from_blocks

    base_dag = _stamp_dag_from_blocks(base_blocks)
    assert base_dag is not None
    # Preserve original level order (hero then projects then CTA)
    orig_levels = [lvl["level"] for lvl in base_dag["levels"]]

    new_blocks = base_blocks[:2] + [_prose("prose-deep")] + [base_blocks[2]]
    dag = merge_dag_bands(base_dag, new_blocks)
    assert dag is not None
    levels = {lvl["level"]: lvl for lvl in dag["levels"]}
    # Prose is deep-dive band 6
    assert "prose-deep" in levels[6]["nodes"]
    # Hero still in intro
    assert "h1" in levels[0]["nodes"]
    # Existing band order for original levels preserved as subset
    for lv in orig_levels:
        assert lv in levels


def test_cap_at_20():
    base = [{"type": "prose", "id": f"p{i}", "props": {"markdown": "x"}} for i in range(19)]
    base.append(_qa())
    patch = [_prose("p-new")]
    blocks, patched, warnings = merge_layout_blocks(base, patch, max_total=20)
    assert len(blocks) <= 20
    # 19 prose + qa = 20 already; append should warn/drop
    assert any("cap" in w for w in warnings) or "p-new" not in patched or len(blocks) == 20


def test_insert_before_cta():
    blocks = [_hero(), _qa()]
    out = insert_before_cta(blocks, _prose())
    assert [b["id"] for b in out] == ["h1", "prose-1", "qa1"]


@pytest.mark.asyncio
async def test_soft_sections_drops_bad_and_reports():
    """compose_patch_layout with one bad section still merges good ones."""
    base = {
        "version": 1,
        "meta": {"audience": "default", "generatedAt": "2026-01-01T00:00:00Z", "dag": None},
        "blocks": [_hero(), _qa()],
    }
    # Stamp a real dag
    from plugins.portfolio_plugin.compose.composer import _stamp_dag_from_blocks

    base["meta"]["dag"] = _stamp_dag_from_blocks(base["blocks"])

    good = _prose("prose-ok", "Grounded text.")
    bad_name = "not_a_real_section_xyz"

    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "plugins.portfolio_plugin.compose.composer.rank_projects_by_query",
        new_callable=AsyncMock,
        side_effect=lambda projects, *a, **k: projects,
    ), patch(
        "plugins.portfolio_plugin.discovery.index.search_context",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await compose_patch_layout(
            {"sections": [good, bad_name], "audience": "default"},
            tenant_id=1,
            base_layout=base,
        )
    assert result["status"] == "ok", result
    ids = [b["id"] for b in result["layout"]["blocks"]]
    assert "prose-ok" in ids
    assert "h1" in ids
    assert any("not_a_real_section" in e for e in (result.get("section_errors") or []))


@pytest.mark.asyncio
async def test_compose_patch_layout_happy_path():
    base = {
        "version": 1,
        "meta": {
            "audience": "default",
            "theme": "neon",
            "generatedAt": "2026-01-01T00:00:00Z",
        },
        "blocks": [_hero(), _qa()],
    }
    from plugins.portfolio_plugin.compose.composer import _stamp_dag_from_blocks

    base["meta"]["dag"] = _stamp_dag_from_blocks(base["blocks"])

    prose = _prose("prose-star", "A STAR story about scaling.")
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "plugins.portfolio_plugin.compose.composer.rank_projects_by_query",
        new_callable=AsyncMock,
        side_effect=lambda projects, *a, **k: projects,
    ), patch(
        "plugins.portfolio_plugin.discovery.index.search_context",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await compose_patch_layout(
            {"sections": [prose], "audience": "default"},
            tenant_id=1,
            base_layout=base,
        )
    assert result["status"] == "ok", result
    ids = [b["id"] for b in result["layout"]["blocks"]]
    assert "h1" in ids
    assert "prose-star" in ids
    assert "qa1" in ids
    assert result["layout"]["meta"]["mode"] == "patched"
    assert result["layout"]["meta"]["theme"] == "neon"
    assert "prose-star" in result["patched_block_ids"]
