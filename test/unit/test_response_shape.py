"""Async response_shape pipeline unit tests (apply_shape_async only)."""

import pytest

from utils.response_shape import apply_shape_async, strip_base64_fields, ARTIFACT_HINT


@pytest.mark.asyncio
async def test_shape_none_returns_meta_envelope():
    res = await apply_shape_async({"a": 1}, None, tool_name="test")
    assert "_meta" in res
    assert res["data"] == {"a": 1}


@pytest.mark.asyncio
async def test_error_dict_passthrough():
    err = {"status": "error", "message": "bad"}
    res = await apply_shape_async(err, {"strip_base64": True})
    assert res == err


@pytest.mark.asyncio
async def test_async_offload_minio_false_keeps_large_leaf(monkeypatch):
    """Explicit offload_minio:false never calls extract_large_artifacts."""
    called = {"n": 0}

    async def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("should not offload")

    monkeypatch.setattr("core.artifact_store.store.extract_large_artifacts", boom)
    big = "y" * 8000
    res = await apply_shape_async(
        {"unidiffPatch": big},
        {
            "offload_minio": False,
            "normalize": False,
            "include_meta": True,
            "response_format": "json",
            "strip_base64": False,
            "strip_hex": False,
        },
        tool_name="GetActivity",
    )
    assert called["n"] == 0
    assert res["data"]["unidiffPatch"] == big


@pytest.mark.asyncio
async def test_async_offload_minio_rewrites_and_bakes_meta(monkeypatch):
    """Large leaf → marker + _meta.artifacts + event emit + strip still runs after."""
    big = "z" * 9000
    events = []

    class _Bus:
        def emit(self, name, payload):
            events.append((name, payload))

    class _Reg:
        events = _Bus()

    async def fake_extract(payload, **kwargs):
        slim = {
            "unidiffPatch": (
                f"[offloaded: data.unidiffPatch short_id=art_diff_abc "
                f"({len(big)} bytes) — call fetch_artifact ONLY if the preview "
                f"below is insufficient]\n--- preview ---\nzzzz\n--- end preview ---"
            ),
            "note": "ok",
        }
        refs = [
            {
                "kind": "diff",
                "short_id": "art_diff_abc",
                "bytes": len(big),
                "content_type": "text/markdown",
                "session_id": kwargs.get("session_id"),
                "path": "data.unidiffPatch",
                "console_path": "/api/artifacts/session_gated/art_diff_abc",
            }
        ]
        return slim, refs

    monkeypatch.setattr("core.artifact_store.store.extract_large_artifacts", fake_extract)
    monkeypatch.setattr(
        "core.plugin_loader.plugin_registry.get_registry", lambda: _Reg()
    )
    monkeypatch.setattr("utils.response_shape._minio_offload_default", lambda: True)

    res = await apply_shape_async(
        {"unidiffPatch": big, "note": "ok", "queueId": "drop-me"},
        {
            "offload_minio": True,
            "normalize": False,
            "include_meta": True,
            "response_format": "json",
            "strip_base64": False,
            "strip_hex": False,
            "strip_keys": ["queueId"],
        },
        tool_name="GetActivity",
        session_id="sess-1",
    )

    assert res["data"]["unidiffPatch"].startswith("[offloaded:")
    assert "art_diff_abc" in res["data"]["unidiffPatch"]
    assert "queueId" not in res["data"]  # strip after offload
    assert res["_meta"]["artifacts"][0]["short_id"] == "art_diff_abc"
    assert res["_meta"]["artifact_hint"] == ARTIFACT_HINT
    assert res["_meta"]["applied_shape"].get("offload_minio") is True
    assert events and events[0][0] == "artifacts.offloaded"
    assert events[0][1]["artifacts"][0]["short_id"] == "art_diff_abc"
    assert events[0][1]["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_async_offload_default_off_when_minio_unavailable(monkeypatch):
    """Default skips offload when MinIO probe is false."""
    called = {"n": 0}

    async def count(*a, **k):
        called["n"] += 1
        return a[0], []

    monkeypatch.setattr("utils.response_shape._minio_offload_default", lambda: False)
    monkeypatch.setattr("core.artifact_store.store.extract_large_artifacts", count)
    big = "w" * 9000
    res = await apply_shape_async(
        {"unidiffPatch": big},
        {
            "normalize": False,
            "include_meta": True,
            "response_format": "json",
            "strip_base64": False,
            "strip_hex": False,
        },
        tool_name="GetActivity",
    )
    assert called["n"] == 0
    assert res["data"]["unidiffPatch"] == big


def test_strip_base64_long_string_replaced():
    long_str = "a" * 200
    res = strip_base64_fields({"key": long_str})
    assert res["key"] == "[base64 stripped]"


def test_strip_base64_data_uri_replaced():
    data_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA..."
    res = strip_base64_fields({"image": data_uri})
    assert res["image"] == "[base64 stripped]"


def test_strip_base64_short_string_kept():
    short_str = "a" * 64
    res = strip_base64_fields({"key": short_str})
    assert res["key"] == short_str


def test_strip_base64_self_referencing_dict_does_not_recurse_forever():
    """A dict that contains itself must not blow the stack (RecursionError repro)."""
    d = {"key": "value"}
    d["self"] = d
    res = strip_base64_fields(d)
    assert res["key"] == "value"
    assert res["self"] == "[circular reference]"


def test_strip_base64_self_referencing_list_does_not_recurse_forever():
    lst = ["a"]
    lst.append(lst)
    res = strip_base64_fields(lst)
    assert res[0] == "a"
    assert res[1] == "[circular reference]"


def test_strip_base64_deep_nesting_bails_out_past_max_depth():
    """Pathologically deep-but-acyclic structure hits the depth cap, not a RecursionError."""
    root: dict = {}
    node = root
    for _ in range(200):
        node["child"] = {}
        node = node["child"]
    node["key"] = "leaf"
    res = strip_base64_fields(root)  # must not raise
    assert isinstance(res, dict)


def test_strip_base64_dag_shared_subobject_not_marked_circular():
    """Same sub-dict referenced from two sibling branches is a DAG, not a cycle."""
    shared = {"key": "value"}
    root = {"a": shared, "b": shared}
    res = strip_base64_fields(root)
    assert res["a"] == {"key": "value"}
    assert res["b"] == {"key": "value"}


def _shape(**kwargs):
    """Default shape for unit tests: no MinIO, json, include_meta."""
    base = {
        "offload_minio": False,
        "normalize": False,
        "include_meta": True,
        "response_format": "json",
        "strip_base64": False,
        "strip_hex": False,
        "strip_empty": False,
    }
    base.update(kwargs)
    return base


@pytest.mark.asyncio
async def test_strip_hex_40plus_replaced():
    hex_str = "a" * 40
    res = await apply_shape_async(
        {"hash": hex_str},
        _shape(strip_hex=True),
        tool_name="test",
    )
    assert res["data"]["hash"] == "[hash]"


@pytest.mark.asyncio
async def test_strip_hex_short_kept():
    hex_str = "deadbeef"
    res = await apply_shape_async(
        {"hash": hex_str},
        _shape(strip_hex=True),
        tool_name="test",
    )
    assert res["data"]["hash"] == hex_str


@pytest.mark.asyncio
async def test_strip_keys_removes_blacklisted():
    res = await apply_shape_async(
        {"queueId": "x", "id": 1},
        _shape(strip_keys=["queueId"]),
        tool_name="test",
    )
    assert "queueId" not in res["data"]
    assert res["data"]["id"] == 1


@pytest.mark.asyncio
async def test_strip_keys_recurses_into_lists():
    data = {"items": [{"queueId": "x", "id": 1}, {"queueId": "y", "id": 2}]}
    res = await apply_shape_async(
        data,
        _shape(strip_keys=["queueId"]),
        tool_name="test",
    )
    for item in res["data"]["items"]:
        assert "queueId" not in item
        assert "id" in item


@pytest.mark.asyncio
async def test_strip_empty_removes_none_empty_list_dict():
    data = {"a": None, "b": [], "c": {}, "d": "", "e": 1}
    res = await apply_shape_async(
        data,
        _shape(strip_empty=True),
        tool_name="test",
    )
    assert "a" not in res["data"]
    assert "b" not in res["data"]
    assert "c" not in res["data"]
    assert "d" not in res["data"]


@pytest.mark.asyncio
async def test_project_whitelists_keys():
    data = {"a": 1, "b": 2, "c": 3}
    res = await apply_shape_async(
        data,
        _shape(project={"a": True, "b": True}),
        tool_name="test",
    )
    assert "c" not in res["data"]
    assert "a" in res["data"]


@pytest.mark.asyncio
async def test_project_recurses_nested():
    data = {"nested": {"a": 1, "b": 2}}
    res = await apply_shape_async(
        data,
        _shape(project={"nested": {"a": True}}),
        tool_name="test",
    )
    assert "b" not in res["data"]["nested"]
    assert "a" in res["data"]["nested"]


@pytest.mark.asyncio
async def test_limit_truncates_and_appends_sentinel():
    data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    res = await apply_shape_async(
        data,
        _shape(limit=3),
        tool_name="test",
    )
    assert len(res["data"]) == 4
    assert res["data"][-1] == "… 7 more items hidden, try use pagination or increase limit to see more."


@pytest.mark.asyncio
async def test_dedupe_collapses_repeated_nested_objects():
    obj = {"id": 1, "name": "test"}
    data = [{"a": obj}, {"a": obj}]
    res = await apply_shape_async(
        data,
        _shape(dedupe=["a"]),
        tool_name="test",
    )
    assert res["data"][0]["a"] == obj
    assert res["data"][1]["a"] == {"$ref": 1}


@pytest.mark.asyncio
async def test_normalize_extracts_primary_list():
    data = {"items": [1, 2, 3], "meta": {}}
    res = await apply_shape_async(
        data,
        _shape(normalize=True),
        tool_name="test",
    )
    assert isinstance(res["data"], list)


@pytest.mark.asyncio
async def test_include_meta_returns_envelope_with_data_key():
    data = {"a": 1}
    res = await apply_shape_async(
        data,
        {"offload_minio": False, "include_meta": True, "response_format": "json"},
        tool_name="test",
    )
    assert "data" in res
    assert "_meta" in res


@pytest.mark.asyncio
async def test_pagination_inference_has_more_flag():
    data = {"has_more": True, "items": [1, 2]}
    res = await apply_shape_async(data, {}, tool_name="test")
    assert res["_meta"]["pagination"]["has_more"] is True


@pytest.mark.asyncio
async def test_pagination_saturation_heuristic():
    data = [1] * 20
    res = await apply_shape_async(
        data, {}, tool_name="test", request_params={"pageSize": 20}
    )
    assert res["_meta"]["pagination"]["has_more"] is True

def test_strip_base64_fields_subclasses():
    from utils.response_shape import strip_base64_fields, _BASE64_MIN_LEN
    from collections import OrderedDict, defaultdict

    long_b64 = "a" * _BASE64_MIN_LEN

    # OrderedDict
    od = OrderedDict([("key", long_b64)])
    res_od = strip_base64_fields(od)
    assert isinstance(res_od, dict)
    assert res_od == {"key": "[base64 stripped]"}

    # defaultdict
    dd = defaultdict(list)
    dd["key"].append(long_b64)
    res_dd = strip_base64_fields(dd)
    assert isinstance(res_dd, dict)
    assert res_dd == {"key": ["[base64 stripped]"]}

    # tuple
    tup = (1, 2, long_b64)
    res_tup = strip_base64_fields(tup)
    assert isinstance(res_tup, list)
    assert res_tup == [1, 2, "[base64 stripped]"]
