"""Unit tests for world_semantic_plugin.hexmath (no DB)."""

from __future__ import annotations

import math

import pytest

h3 = pytest.importorskip("h3")

from plugins.world_semantic_plugin.hexmath import (
    Projection,
    ancestors,
    bearing_northish,
    cell,
    cell_center_meters,
    is_north_of,
    kring,
    parent,
    polyfill_rect,
)


def test_meters_latlng_roundtrip_origin():
    p = Projection()
    lat, lng = p.meters_to_latlng(0.0, 0.0)
    assert abs(lat - p.anchor_lat) < 1e-9
    assert abs(lng - p.anchor_lng) < 1e-9
    x, z = p.latlng_to_meters(lat, lng)
    assert abs(x) < 1e-6
    assert abs(z) < 1e-6


def test_meters_latlng_roundtrip_offset():
    p = Projection()
    x0, z0 = 250.0, -180.0
    lat, lng = p.meters_to_latlng(x0, z0)
    x1, z1 = p.latlng_to_meters(lat, lng)
    assert abs(x1 - x0) < 1e-3
    assert abs(z1 - z0) < 1e-3


def test_cell_stable_for_same_point():
    p = Projection(base_res=9)
    a = cell(10.0, 20.0, p)
    b = cell(10.0, 20.0, p)
    assert a == b
    assert h3.is_valid_cell(a)


def test_kring_includes_center():
    h = cell(0.0, 0.0, Projection(base_res=9))
    disk = kring(h, 1)
    assert h in disk
    assert len(disk) == 1 + 6  # center + 6 neighbors at k=1


def test_parent_coarser():
    h = cell(5.0, 5.0, Projection(base_res=9))
    par = parent(h)
    assert h3.get_resolution(par) == h3.get_resolution(h) - 1
    assert h3.cell_to_parent(h, h3.get_resolution(par)) == par


def test_ancestors_chain():
    h = cell(0.0, 0.0, Projection(base_res=5))
    chain = ancestors(h, stop_res=0)
    assert chain[0] == h
    assert h3.get_resolution(chain[-1]) == 0
    for i in range(len(chain) - 1):
        assert h3.get_resolution(chain[i]) == h3.get_resolution(chain[i + 1]) + 1


def test_cell_center_near_input():
    p = Projection(base_res=10)
    x0, z0 = 40.0, -25.0
    h = cell(x0, z0, p)
    cx, cz = cell_center_meters(h, p)
    # Center should be within roughly one edge length of the point.
    assert math.hypot(cx - x0, cz - z0) < 200.0


def test_north_bearing():
    p = Projection(base_res=9)
    # Same x, larger z → northish
    south = cell(0.0, 0.0, p)
    north = cell(0.0, 500.0, p)
    if south == north:
        pytest.skip("points fell in same cell at this res")
    b = bearing_northish(south, north, p)
    assert b <= 45 or b >= 315
    assert is_north_of(south, north, proj=p)


def test_polyfill_rect_nonempty():
    p = Projection(base_res=9)
    cells = polyfill_rect(-50, -50, 50, 50, p)
    assert len(cells) >= 1
    assert all(h3.is_valid_cell(c) for c in cells)
