"""Unit tests for OperationDescriptor and RouteDescriptor.to_operation()."""

from __future__ import annotations

from core.route_registry.operation_descriptor import (
    AccessClass,
    Visibility,
    HttpExposure,
    McpExposure,
    UiContribution,
    OperationDescriptor,
    validate_operation,
)
from core.route_registry.route_descriptor import RouteDescriptor


def test_to_operation_maps_parameters_to_input_schema() -> None:
    rd = RouteDescriptor(
        plugin_id="p1",
        operation_id="p1__op1",
        description="d",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        method="CALL",
        is_fast_path=True,
        tags=("p1", "read"),
    )
    op = rd.to_operation()
    assert op.input_schema == rd.parameters
    assert op.access == AccessClass.READ
    assert op.mcp is not None
    assert op.mcp.tool_name == "op1"
    assert op.is_fast_path is True


def test_access_inferred_from_write_tag() -> None:
    rd = RouteDescriptor(
        plugin_id="p1",
        operation_id="p1__write_thing",
        description="w",
        tags=("p1", "write"),
        is_fast_path=True,
        method="CALL",
    )
    assert rd.to_operation().access == AccessClass.WRITE


def test_explicit_access_overrides_tags() -> None:
    rd = RouteDescriptor(
        plugin_id="p1",
        operation_id="p1__x",
        description="x",
        tags=("write",),
        access="admin",
    )
    assert rd.to_operation().access == AccessClass.ADMIN


def test_descriptor_hash_stable() -> None:
    a = OperationDescriptor(
        plugin_id="p1",
        operation_id="op1",
        description="d",
        input_schema={"type": "object"},
        access=AccessClass.READ,
    )
    b = OperationDescriptor(
        plugin_id="p1",
        operation_id="op1",
        description="d",
        input_schema={"type": "object"},
        access=AccessClass.READ,
    )
    assert a.descriptor_hash == b.descriptor_hash
    assert len(a.descriptor_hash) == 64


def test_content_hash_changes_when_output_schema_changes() -> None:
    r1 = RouteDescriptor(plugin_id="p1", operation_id="op1", description="d")
    r2 = RouteDescriptor(
        plugin_id="p1",
        operation_id="op1",
        description="d",
        output_schema={"type": "object"},
    )
    assert r1.content_hash != r2.content_hash


def test_content_hash_changes_when_workspace_label_changes() -> None:
    r1 = RouteDescriptor(plugin_id="p1", operation_id="op1", description="d")
    r2 = RouteDescriptor(
        plugin_id="p1",
        operation_id="op1",
        description="d",
        workspace_label="team-a",
    )
    r3 = RouteDescriptor(
        plugin_id="p1",
        operation_id="op1",
        description="d",
        tags=("workspace:team-a",),
    )
    assert r1.content_hash != r2.content_hash
    # tag-derived effective label is equivalent for hash purposes
    assert r2.content_hash == r3.content_hash


def test_http_exposure_from_method_path() -> None:
    rd = RouteDescriptor(
        plugin_id="p1",
        operation_id="list_items",
        description="list",
        method="GET",
        path_template="/items",
        is_fast_path=False,
    )
    op = rd.to_operation()
    assert op.http is not None
    assert op.http.method == "GET"
    assert op.http.path_template == "/items"


def test_to_catalog_dict_has_no_callable() -> None:
    def fn():
        return None

    op = OperationDescriptor(
        plugin_id="p1",
        operation_id="op1",
        description="d",
        callable_ref=fn,
        is_fast_path=True,
        mcp=McpExposure(tool_name="op1"),
    )
    d = op.to_catalog_dict()
    assert "callable_ref" not in d
    assert d["mcp"]["tool_name"] == "op1"
    assert d["descriptor_hash"] == op.descriptor_hash


def test_validate_operation_rejects_bad_ui() -> None:
    op = OperationDescriptor(
        plugin_id="p1",
        operation_id="op1",
        ui=UiContribution(slot="x", renderer_kind="nope"),
    )
    try:
        validate_operation(op, owner_plugin_id="p1")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "renderer_kind" in str(e)


def test_validate_operation_rejects_cross_owner() -> None:
    op = OperationDescriptor(plugin_id="p1", operation_id="op1")
    try:
        validate_operation(op, owner_plugin_id="p2")
        assert False, "expected ValueError"
    except ValueError:
        pass
