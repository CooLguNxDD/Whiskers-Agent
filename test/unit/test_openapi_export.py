"""Unit tests for OpenAPI 3.0 export from OperationDescriptor HTTP exposures."""

from __future__ import annotations

from core.route_registry.openapi_export import build_openapi_from_ops
from core.route_registry.operation_descriptor import (
    AccessClass,
    HttpExposure,
    McpExposure,
    OperationDescriptor,
)


def _http_op(
    *,
    plugin_id: str = "p1",
    operation_id: str = "list_items",
    path: str = "/items",
    method: str = "GET",
    description: str = "List items",
    input_schema: dict | None = None,
    output_schema: dict | None = None,
) -> OperationDescriptor:
    return OperationDescriptor(
        plugin_id=plugin_id,
        operation_id=operation_id,
        description=description,
        input_schema=input_schema or {},
        output_schema=output_schema,
        access=AccessClass.READ,
        http=HttpExposure(method=method, path_template=path),
    )


def test_get_items_path_present() -> None:
    op = _http_op()
    doc = build_openapi_from_ops([op])

    assert doc["openapi"] == "3.0.3"
    assert doc["info"]["title"] == "Whiskers Agent Catalog"
    assert "/items" in doc["paths"]
    assert "get" in doc["paths"]["/items"]
    entry = doc["paths"]["/items"]["get"]
    assert entry["operationId"] == "p1__list_items"
    assert entry["summary"] == "List items"
    assert entry["tags"] == ["p1"]
    assert "requestBody" not in entry
    assert entry["responses"]["200"]["content"]["application/json"]["schema"] == {}


def test_op_without_http_excluded() -> None:
    mcp_only = OperationDescriptor(
        plugin_id="p1",
        operation_id="p1__call_op",
        description="MCP only",
        mcp=McpExposure(tool_name="call_op"),
    )
    doc = build_openapi_from_ops([mcp_only])
    assert doc["paths"] == {}


def test_call_only_path_without_slash_excluded() -> None:
    call_op = OperationDescriptor(
        plugin_id="p1",
        operation_id="p1__call_op",
        description="CALL id path",
        http=HttpExposure(method="CALL", path_template="p1__call_op"),
    )
    doc = build_openapi_from_ops([call_op])
    assert doc["paths"] == {}


def test_post_includes_request_body_when_input_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    op = _http_op(
        method="POST",
        path="/items",
        input_schema=schema,
        output_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
    )
    doc = build_openapi_from_ops([op])
    post = doc["paths"]["/items"]["post"]
    assert post["requestBody"]["content"]["application/json"]["schema"] == schema
    assert post["responses"]["200"]["content"]["application/json"]["schema"] == {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
    }


def test_filtered_ops_passed_through() -> None:
    """Caller applies filter_for_caller before build; only supplied ops appear."""
    visible = _http_op(operation_id="visible", path="/visible")
    doc = build_openapi_from_ops([visible])
    assert set(doc["paths"]) == {"/visible"}