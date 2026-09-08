"""Unit tests for live catalog HTTP routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from core.route_registry.host_catalog import clear_host_catalog_index
from core.route_registry.operation_catalog import (
    get_operation_catalog,
    _reset_operation_catalog_for_tests,
)
from core.route_registry.operation_descriptor import (
    AccessClass,
    HttpExposure,
    OperationDescriptor,
    UiContribution,
    Visibility,
)
from core.route_registry.route_descriptor import RouteDescriptor
from core.route_registry import RouteRegistry

# Import handlers at module load so host-mirror side-effects run before fixtures clear them.
from api.catalog_routes import (  # noqa: E402
    api_catalog_list,
    api_catalog_openapi,
    api_catalog_execute,
)


def _make_request(
    method: str = "GET",
    path: str = "/api/catalog/session_gated",
    *,
    headers: dict | None = None,
    cookies: dict | None = None,
    query: str = "",
    json_body: dict | None = None,
) -> Request:
    import json as _json

    headers = dict(headers or {})
    headers.setdefault("host", "test")
    if cookies:
        headers["cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    body = b""
    if json_body is not None:
        body = _json.dumps(json_body).encode("utf-8")
        headers["content-type"] = "application/json"
        headers["content-length"] = str(len(body))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.fixture(autouse=True)
def _reset_catalog():
    # Drop host-mirrored routes (import side-effects) so unit tests own the catalog.
    clear_host_catalog_index()
    _reset_operation_catalog_for_tests()
    yield
    clear_host_catalog_index()
    _reset_operation_catalog_for_tests()


@pytest.mark.asyncio
async def test_catalog_list_returns_revision_and_ops() -> None:
    cat = get_operation_catalog()
    cat.publish_owner(
        "p1",
        [
            OperationDescriptor(
                plugin_id="p1",
                operation_id="p1__op",
                description="d",
                access=AccessClass.READ,
                tags=("p1",),
            )
        ],
    )

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["admin"])):
        req = _make_request()
        res = await api_catalog_list(req)

    assert res.status_code == 200
    import json
    data = json.loads(res.body)
    assert data["revision"] >= 1
    assert "etag" in data
    assert len(data["operations"]) == 1
    assert data["operations"][0]["operation_id"] == "p1__op"
    assert res.headers.get("etag") or res.headers.get("ETag")
    # no-store (not "private, no-cache") — a browser-level silent revalidation
    # collapses the FE's live snapshot into a phantom 304 with no cached body
    # (see api/catalog.ts::getCatalog / api/catalogRuntime.ts::ensureCatalogClient).
    assert res.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_catalog_list_304_on_etag_match() -> None:
    cat = get_operation_catalog()
    cat.publish_owner(
        "p1",
        [OperationDescriptor(plugin_id="p1", operation_id="op", description="d")],
    )

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["admin"])):
        first = await api_catalog_list(_make_request())
        assert first.status_code == 200
        etag = first.headers.get("etag") or first.headers.get("ETag")
        assert etag
        # Global catalog hash is not the HTTP validator for a filtered view.
        assert etag != cat.etag

        res = await api_catalog_list(_make_request(headers={"if-none-match": etag}))

    assert res.status_code == 304
    assert (res.headers.get("etag") or res.headers.get("ETag")) == etag
    assert res.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_catalog_list_does_not_304_on_global_catalog_etag() -> None:
    cat = get_operation_catalog()
    cat.publish_owner(
        "p1",
        [OperationDescriptor(plugin_id="p1", operation_id="op", description="d")],
    )

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["admin"])):
        res = await api_catalog_list(_make_request(headers={"if-none-match": cat.etag}))

    assert res.status_code == 200
    assert res.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_catalog_list_plugin_filter_has_distinct_etag() -> None:
    cat = get_operation_catalog()
    cat.publish_owner(
        "p1",
        [OperationDescriptor(plugin_id="p1", operation_id="p1__a", description="a")],
    )
    cat.publish_owner(
        "p2",
        [OperationDescriptor(plugin_id="p2", operation_id="p2__b", description="b")],
    )

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["admin"])):
        unfiltered = await api_catalog_list(_make_request())
        import json
        all_etag = unfiltered.headers.get("etag") or unfiltered.headers.get("ETag")
        assert unfiltered.status_code == 200
        assert len(json.loads(unfiltered.body)["operations"]) == 2

        filtered = await api_catalog_list(
            _make_request(query="plugin_id=p1", headers={"if-none-match": all_etag}),
        )
        assert filtered.status_code == 200
        data = json.loads(filtered.body)
        assert [o["plugin_id"] for o in data["operations"]] == ["p1"]
        p1_etag = filtered.headers.get("etag") or filtered.headers.get("ETag")
        assert p1_etag != all_etag

        again = await api_catalog_list(
            _make_request(query="plugin_id=p1", headers={"if-none-match": p1_etag}),
        )
        assert again.status_code == 304
        assert again.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_catalog_list_scopes_have_distinct_etag() -> None:
    cat = get_operation_catalog()
    cat.publish_owner(
        "p1",
        [
            OperationDescriptor(
                plugin_id="p1",
                operation_id="alpha",
                description="a",
                required_scopes=("plugin:alpha",),
            ),
            OperationDescriptor(
                plugin_id="p1",
                operation_id="beta",
                description="b",
                required_scopes=("plugin:beta",),
            ),
        ],
    )

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["plugin:alpha"])):
        alpha = await api_catalog_list(_make_request())
    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["plugin:beta"])):
        beta = await api_catalog_list(_make_request())

    import json
    assert {o["operation_id"] for o in json.loads(alpha.body)["operations"]} == {"alpha"}
    assert {o["operation_id"] for o in json.loads(beta.body)["operations"]} == {"beta"}
    alpha_etag = alpha.headers.get("etag") or alpha.headers.get("ETag")
    beta_etag = beta.headers.get("etag") or beta.headers.get("ETag")
    assert alpha_etag != beta_etag

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["plugin:beta"])):
        collapsed = await api_catalog_list(_make_request(headers={"if-none-match": alpha_etag}))
    assert collapsed.status_code == 200
    assert {o["operation_id"] for o in json.loads(collapsed.body)["operations"]} == {"beta"}


@pytest.mark.asyncio
async def test_catalog_list_slot_filter_has_distinct_etag() -> None:
    cat = get_operation_catalog()
    cat.publish_owner(
        "p1",
        [
            OperationDescriptor(
                plugin_id="p1",
                operation_id="act",
                description="a",
                ui=UiContribution(slot="plugin.detail.actions", renderer_kind="form"),
            ),
            OperationDescriptor(
                plugin_id="p1",
                operation_id="other",
                description="b",
            ),
        ],
    )

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["admin"])):
        unfiltered = await api_catalog_list(_make_request())
        all_etag = unfiltered.headers.get("etag") or unfiltered.headers.get("ETag")
        slotted = await api_catalog_list(
            _make_request(
                query="slot=plugin.detail.actions",
                headers={"if-none-match": all_etag},
            ),
        )

    import json
    assert slotted.status_code == 200
    assert [o["operation_id"] for o in json.loads(slotted.body)["operations"]] == ["act"]
    assert (slotted.headers.get("etag") or slotted.headers.get("ETag")) != all_etag


@pytest.mark.asyncio
async def test_catalog_hides_hidden_ops() -> None:
    cat = get_operation_catalog()
    cat.publish_owner(
        "p1",
        [
            OperationDescriptor(
                plugin_id="p1",
                operation_id="vis",
                description="v",
                visibility=Visibility.AUTHENTICATED,
            ),
            OperationDescriptor(
                plugin_id="p1",
                operation_id="hid",
                description="h",
                visibility=Visibility.HIDDEN,
            ),
        ],
    )

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["admin"])):
        res = await api_catalog_list(_make_request())
    import json
    data = json.loads(res.body)
    ids = {o["operation_id"] for o in data["operations"]}
    assert ids == {"vis"}


@pytest.mark.asyncio
async def test_catalog_openapi_returns_http_paths() -> None:
    cat = get_operation_catalog()
    cat.publish_owner(
        "p1",
        [
            OperationDescriptor(
                plugin_id="p1",
                operation_id="p1__list",
                description="List things",
                access=AccessClass.READ,
                tags=("p1",),
                http=HttpExposure(method="GET", path_template="/things"),
            ),
            OperationDescriptor(
                plugin_id="p1",
                operation_id="p1__call_only",
                description="MCP",
                mcp=None,
            ),
        ],
    )

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["admin"])):
        req = _make_request(path="/api/catalog/session_gated/openapi")
        res = await api_catalog_openapi(req)

    assert res.status_code == 200
    import json
    doc = json.loads(res.body)
    assert doc["openapi"] == "3.0.3"
    assert "/things" in doc["paths"]
    assert doc["paths"]["/things"]["get"]["operationId"] == "p1__list"
    assert res.headers.get("etag") or res.headers.get("ETag")
    assert res.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_catalog_openapi_filters_by_plugin_id() -> None:
    cat = get_operation_catalog()
    cat.publish_owner(
        "p1",
        [
            OperationDescriptor(
                plugin_id="p1",
                operation_id="p1__a",
                description="a",
                http=HttpExposure(method="GET", path_template="/a"),
            ),
        ],
    )
    cat.publish_owner(
        "p2",
        [
            OperationDescriptor(
                plugin_id="p2",
                operation_id="p2__b",
                description="b",
                http=HttpExposure(method="GET", path_template="/b"),
            ),
        ],
    )

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["admin"])):
        req = _make_request(
            path="/api/catalog/session_gated/openapi",
            query="plugin_id=p1",
        )
        res = await api_catalog_openapi(req)

    import json
    doc = json.loads(res.body)
    assert set(doc["paths"]) == {"/a"}


@pytest.mark.asyncio
async def test_catalog_openapi_304_on_view_etag_match() -> None:
    cat = get_operation_catalog()
    cat.publish_owner(
        "p1",
        [
            OperationDescriptor(
                plugin_id="p1",
                operation_id="p1__list",
                description="List things",
                http=HttpExposure(method="GET", path_template="/things"),
            ),
        ],
    )

    with patch("api.catalog_routes._resolve_caller_scopes", new=AsyncMock(return_value=["admin"])):
        first = await api_catalog_openapi(
            _make_request(path="/api/catalog/session_gated/openapi"),
        )
        assert first.status_code == 200
        etag = first.headers.get("etag") or first.headers.get("ETag")
        assert etag
        assert etag != cat.etag
        res = await api_catalog_openapi(
            _make_request(
                path="/api/catalog/session_gated/openapi",
                headers={"if-none-match": etag},
            ),
        )

    assert res.status_code == 304
    assert res.headers.get("cache-control") == "no-store"
