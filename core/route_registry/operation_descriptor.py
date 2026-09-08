"""OperationDescriptor — canonical executable contract + optional exposures.

Identity/schema/access live on OperationDescriptor. HTTP, MCP, and UI are
optional attachments so a UI slot is never confused with an executable op.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class AccessClass(str, Enum):
    """Access class for permission gating and catalog filtering."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class Visibility(str, Enum):
    """Catalog visibility (hidden ops never appear in live catalogs)."""

    PUBLIC_CATALOG = "public_catalog"
    AUTHENTICATED = "authenticated"
    HIDDEN = "hidden"


@dataclass(frozen=True)
class HttpExposure:
    """How an operation is reached over HTTP (path/method/auth)."""

    method: str
    path_template: str
    auth_policy: str = "session_gated"
    request_map: dict = field(default_factory=dict)
    response_map: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize for catalog JSON."""
        return {
            "method": self.method,
            "path_template": self.path_template,
            "auth_policy": self.auth_policy,
            "request_map": self.request_map,
            "response_map": self.response_map,
        }


@dataclass(frozen=True)
class McpExposure:
    """How an operation is reached as an MCP tool."""

    tool_name: str
    qualified_name: str = ""

    def to_dict(self) -> dict:
        """Serialize for catalog JSON."""
        return {
            "tool_name": self.tool_name,
            "qualified_name": self.qualified_name or self.tool_name,
        }


@dataclass(frozen=True)
class UiContribution:
    """Optional console UI binding for schema-driven hosts."""

    slot: str
    renderer_kind: str  # form | action | table | custom
    ui_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize for catalog JSON."""
        return {
            "slot": self.slot,
            "renderer_kind": self.renderer_kind,
            "ui_schema": self.ui_schema,
        }


_VALID_RENDERERS = frozenset({"form", "action", "table", "custom"})


@dataclass(frozen=True)
class OperationDescriptor:
    """Canonical operation contract: identity, schemas, access, optional exposures."""

    plugin_id: str
    operation_id: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    output_schema: dict | None = None
    access: AccessClass = AccessClass.READ
    required_scopes: tuple[str, ...] = ()
    visibility: Visibility = Visibility.AUTHENTICATED
    version: str = "1"
    tags: tuple[str, ...] = ()
    http: HttpExposure | None = None
    mcp: McpExposure | None = None
    ui: UiContribution | None = None
    is_fast_path: bool = False
    callable_ref: Callable[..., Any] | None = None

    @property
    def key(self) -> tuple[str, str]:
        """Unique (plugin_id, operation_id) identity."""
        return (self.plugin_id, self.operation_id)

    def to_catalog_dict(self) -> dict:
        """JSON-serializable catalog entry (never includes callables).

        Advertises the ``_response_shape`` override on the input schema for fast-path
        operations only — ``core.route_registry.execute.execute_operation`` pops the key
        before schema validation unless the op already declares it, so this is
        display-only and does not affect the underlying ``input_schema`` used for
        validation or the descriptor hash below.
        """
        return {
            "plugin_id": self.plugin_id,
            "operation_id": self.operation_id,
            "description": self.description,
            "input_schema": self._catalog_input_schema(),
            "output_schema": self.output_schema,
            "access": self.access.value if isinstance(self.access, AccessClass) else str(self.access),
            "required_scopes": list(self.required_scopes),
            "visibility": (
                self.visibility.value
                if isinstance(self.visibility, Visibility)
                else str(self.visibility)
            ),
            "version": self.version,
            "tags": list(self.tags),
            "http": self.http.to_dict() if self.http else None,
            "mcp": self.mcp.to_dict() if self.mcp else None,
            "ui": self.ui.to_dict() if self.ui else None,
            "is_fast_path": self.is_fast_path,
            "descriptor_hash": self.descriptor_hash,
        }

    def _catalog_input_schema(self) -> dict:
        """``input_schema`` with ``_response_shape`` advertised for shapeable fast-path ops.

        Mirrors ``core.context.response_shape_middleware.ResponseShapeMiddleware.on_list_tools``,
        the equivalent advertisement for direct MCP tool calls.
        """
        schema = self.input_schema or {}
        if not self.is_fast_path:
            return schema
        properties = schema.get("properties")
        if isinstance(properties, dict) and "_response_shape" in properties:
            return schema
        from utils.response_shape_hints import SHAPE_PARAM_DESCRIPTION

        schema = dict(schema)
        schema["properties"] = {
            **(properties or {}),
            "_response_shape": {"type": "object", "description": SHAPE_PARAM_DESCRIPTION},
        }
        return schema

    @property
    def descriptor_hash(self) -> str:
        """Stable hash of serializable contract fields (excludes callable_ref and self-hash)."""
        payload = {
            "plugin_id": self.plugin_id,
            "operation_id": self.operation_id,
            "description": self.description,
            "input_schema": self.input_schema or {},
            "output_schema": self.output_schema,
            "access": self.access.value if isinstance(self.access, AccessClass) else str(self.access),
            "required_scopes": list(self.required_scopes),
            "visibility": (
                self.visibility.value
                if isinstance(self.visibility, Visibility)
                else str(self.visibility)
            ),
            "version": self.version,
            "tags": list(self.tags),
            "http": self.http.to_dict() if self.http else None,
            "mcp": self.mcp.to_dict() if self.mcp else None,
            "ui": self.ui.to_dict() if self.ui else None,
            "is_fast_path": self.is_fast_path,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def validate_operation(op: OperationDescriptor, *, owner_plugin_id: str | None = None) -> None:
    """Raise ValueError if an operation fails publish-time validation."""
    if not op.plugin_id or not op.operation_id:
        raise ValueError("operation requires non-empty plugin_id and operation_id")
    if owner_plugin_id is not None and op.plugin_id != owner_plugin_id:
        raise ValueError(
            f"operation plugin_id={op.plugin_id!r} does not match owner {owner_plugin_id!r}"
        )
    if op.input_schema is not None and not isinstance(op.input_schema, dict):
        raise ValueError("input_schema must be a dict")
    if op.ui is not None and op.ui.renderer_kind not in _VALID_RENDERERS:
        raise ValueError(
            f"ui.renderer_kind must be one of {sorted(_VALID_RENDERERS)}, got {op.ui.renderer_kind!r}"
        )
