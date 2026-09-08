"""Assemble an OpenAPI 3.0 document from a route-manifest list."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from Tools.openapi_pipeline.config import IngestConfig
from Tools.openapi_pipeline.paths import extract_path_params, infer_tag, openapi_path


def skeleton_operation(route: dict, extra_skip: set[str] | None = None) -> dict:
    """Build a minimal but valid OpenAPI Operation Object."""
    method = route["method"].lower()
    tag = (route.get("tags") or [None])[0] or infer_tag(route["path"], extra_skip)
    op: dict[str, Any] = {
        "operationId": route["operationId"],
        "summary": route.get("description") or route["operationId"],
        "tags": [tag],
        "parameters": extract_path_params(route["path"]),
        "responses": {
            "200": {
                "description": "Success",
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "additionalProperties": True}
                    }
                },
            },
            "400": {"description": "Bad request"},
        },
    }
    if route.get("security"):
        op["responses"]["401"] = {"description": "Unauthorized"}
        op["security"] = [{s: []} for s in route["security"]]
    if method in ("post", "put", "patch"):
        op["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True}
                }
            },
        }
    return op


def build_spec(
    routes: list[dict],
    config: IngestConfig,
    *,
    description: str | None = None,
) -> dict[str, Any]:
    """Assemble a full OpenAPI 3.0 document from route records."""
    extra_skip = set(config.ignored_path_segments)
    used_schemes: set[str] = set()
    for route in routes:
        used_schemes.update(route.get("security") or [])

    paths: dict[str, dict] = defaultdict(dict)
    for route in routes:
        raw_path = route["path"]
        oa_path = openapi_path(raw_path)
        method = route["method"].lower()
        ai_item = route.get("openapi_path_item") or {}
        if ai_item and method in ai_item:
            operation = dict(ai_item[method])
            operation.setdefault("operationId", route["operationId"])
            operation.setdefault("summary", route.get("description") or route["operationId"])
        else:
            operation = skeleton_operation(route, extra_skip)

        existing_param_names = {
            p.get("name") for p in operation.get("parameters") or [] if isinstance(p, dict)
        }
        path_params = [
            p for p in extract_path_params(raw_path) if p["name"] not in existing_param_names
        ]
        if path_params:
            operation.setdefault("parameters", [])
            operation["parameters"] = path_params + list(operation["parameters"])
        paths[oa_path][method] = operation

    all_schemes = config.resolved_security_schemes()
    sec_schemes = {k: v for k, v in all_schemes.items() if k in used_schemes}

    tags: list[dict[str, str]] = []
    seen: set[str] = set()
    for route in routes:
        name = (route.get("tags") or [None])[0] or infer_tag(route["path"], extra_skip)
        if name not in seen:
            seen.add(name)
            tags.append({"name": name})
    tags.sort(key=lambda item: item["name"])

    spec_description = description or (
        "Auto-generated OpenAPI 3.0 document from backend routes or a live spec."
    )
    return {
        "openapi": "3.0.3",
        "info": {
            "title": config.title,
            "version": config.api_version,
            "description": spec_description,
        },
        "servers": [{"url": config.server, "description": "API server"}],
        "paths": dict(paths),
        "components": {"securitySchemes": sec_schemes},
        "tags": tags,
    }


def passthrough_spec(spec: dict[str, Any], config: IngestConfig) -> dict[str, Any]:
    """Normalize a live/file spec: keep operations, fill missing info/servers."""
    out = dict(spec)
    if not out.get("openapi"):
        # Swagger 2 documents stay Swagger 2 unless the caller already converted.
        out.setdefault("swagger", spec.get("swagger") or "2.0")
    else:
        out["openapi"] = out.get("openapi") or "3.0.3"
    info = dict(out.get("info") or {})
    info.setdefault("title", config.title)
    info.setdefault("version", config.api_version)
    out["info"] = info
    if not out.get("servers") and not out.get("host"):
        out["servers"] = [{"url": config.server, "description": "API server"}]
    return out
