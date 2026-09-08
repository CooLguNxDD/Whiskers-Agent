"""OpenAPI 3.0 export from OperationDescriptor HTTP exposures."""

from __future__ import annotations

from typing import Any

from core.route_registry.operation_descriptor import OperationDescriptor

_OPENAPI_VERSION = "3.0.3"
_BODY_METHODS_SKIP = frozenset({"GET", "HEAD"})


def _is_http_path(path_template: str) -> bool:
    """True when path_template looks like a real HTTP path (leading /)."""
    return bool(path_template) and path_template.startswith("/")


def _operation_id(op: OperationDescriptor) -> str:
    """Build OpenAPI operationId from plugin + operation identity."""
    oid = op.operation_id
    prefix = f"{op.plugin_id}__"
    if oid.startswith(prefix):
        return oid
    return f"{op.plugin_id}__{oid}"


def build_openapi_from_ops(
    ops: list[OperationDescriptor],
    *,
    title: str = "Whiskers Agent Catalog",
    version: str = "1",
) -> dict:
    """Build an OpenAPI 3.0 document from operations with HTTP exposure."""
    paths: dict[str, dict[str, Any]] = {}

    for op in ops:
        http = op.http
        if http is None:
            continue
        path_template = http.path_template
        if not _is_http_path(path_template):
            continue

        method = http.method.upper()
        method_key = method.lower()

        op_entry: dict[str, Any] = {
            "operationId": _operation_id(op),
            "summary": op.description or op.operation_id,
            "tags": [op.plugin_id],
        }

        input_schema = op.input_schema or {}
        if method not in _BODY_METHODS_SKIP and input_schema:
            op_entry["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": input_schema,
                    }
                },
            }

        output_schema = op.output_schema if op.output_schema is not None else {}
        op_entry["responses"] = {
            "200": {
                "description": "Successful response",
                "content": {
                    "application/json": {
                        "schema": output_schema,
                    }
                },
            }
        }

        if path_template not in paths:
            paths[path_template] = {}
        paths[path_template][method_key] = op_entry

    return {
        "openapi": _OPENAPI_VERSION,
        "info": {
            "title": title,
            "version": version,
        },
        "paths": paths,
    }