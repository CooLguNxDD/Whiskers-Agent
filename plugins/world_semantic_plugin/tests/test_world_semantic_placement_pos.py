"""Unit tests for plan_placement / get_hex center_pos enrichment (no DB)."""

from __future__ import annotations

import pytest

h3 = pytest.importorskip("h3")

from plugins.world_semantic_plugin.hexmath import (
    Projection,
    cell,
    cell_center_meters,
    kring,
    projection_from_world_row,
)


def test_cell_center_and_neighbors_shape():
    p = Projection()
    hid = cell(12.5, -8.0, p)
    cx, cz = cell_center_meters(hid, p)
    assert isinstance(cx, float) and isinstance(cz, float)
    neighbors = [h for h in kring(hid, 1) if h != hid]
    assert len(neighbors) == 6
    assert hid not in neighbors


def test_projection_from_world_row_defaults():
    p = projection_from_world_row(None)
    assert p.base_res == 10
    p2 = projection_from_world_row(
        {"anchor_lat": 1.0, "anchor_lng": 2.0, "meters_per_degree": 100000.0, "base_res": 8}
    )
    assert p2.anchor_lat == 1.0
    assert p2.anchor_lng == 2.0
    assert p2.meters_per_degree == 100000.0
    assert p2.base_res == 8


def test_candidate_payload_shape_matches_plan_contract():
    """Mirrors the dict shape plan_placement adds per candidate."""
    p = Projection()
    hid = cell(0.0, 0.0, p)
    cx, cz = cell_center_meters(hid, p)
    candidate = {
        "hex_id": hid,
        "ring": 0,
        "score": 1.0,
        "reason": "test",
        "center_pos": [cx, cz],
        "neighbor_hexes": [h for h in kring(hid, 1) if h != hid],
        "object_count": 0,
    }
    assert len(candidate["center_pos"]) == 2
    assert all(isinstance(v, float) for v in candidate["center_pos"])
    assert len(candidate["neighbor_hexes"]) == 6
