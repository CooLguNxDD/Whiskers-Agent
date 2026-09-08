"""Smoke tests for world_semantic route path constants and payload helpers (no DB)."""

from __future__ import annotations

from plugins.world_semantic_plugin import routes
from plugins.world_semantic_plugin.stores.world_store import _parse_guid, _pos_array


def test_route_paths():
    assert "{world_id}" in routes.INDEX_PATH
    assert routes.INDEX_PATH.endswith("/index")
    assert routes.DIFF_PATH.endswith("/diff")
    assert routes.OWNER == "world_semantic_plugin"


def test_parse_guid_uuid():
    g = _parse_guid("550e8400-e29b-41d4-a716-446655440000")
    assert g == "550e8400-e29b-41d4-a716-446655440000"


def test_parse_guid_global_object_id_stable():
    a = _parse_guid("GlobalObjectId_V1-2-abc-3-4")
    b = _parse_guid("GlobalObjectId_V1-2-abc-3-4")
    assert a == b
    assert a is not None
    # Must be UUID-shaped
    assert len(a) == 36


def test_pos_array_dict_and_list():
    assert _pos_array({"pos": {"x": 1, "y": 2, "z": 3}}) == [1.0, 2.0, 3.0]
    assert _pos_array({"position": [4, 5, 6]}) == [4.0, 5.0, 6.0]
    assert _pos_array({}) is None
