"""Parse an existing OpenAPI 3 / Swagger 2 document into route records."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from Tools.openapi_pipeline.models import RouteRecord, route_to_dict
from Tools.openapi_pipeline.paths import join_prefix, openapi_path


def _load_yaml(text: str) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to parse YAML OpenAPI documents") from exc
    return yaml.safe_load(text)


def parse_spec_text(text: str, *, source_name: str = "spec") -> dict[str, Any]:
    """Parse JSON or YAML OpenAPI/Swagger text into a dict."""
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        import json
        return json.loads(text)
    data = _load_yaml(text)
    if not isinstance(data, dict):
        raise ValueError(f"{source_name} did not contain an object")
    return data


def is_openapi_or_swagger(data: dict[str, Any]) -> bool:
    """True when the dict looks like OpenAPI 3 or Swagger 2."""
    return isinstance(data.get("openapi"), str) or isinstance(data.get("swagger"), str)


def spec_to_routes(spec: dict[str, Any], *, adapter: str = "spec") -> list[dict]:
    """Flatten OpenAPI/Swagger paths into route-manifest records.

    Operations that already have request/response shapes are marked
    ``already_specified`` so the enrichment step can be skipped.
    """
    base_path = str(spec.get("basePath") or "")
    routes: list[dict] = []
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return []

    for raw_path, item in paths.items():
        if not isinstance(item, dict):
            continue
        full_path = openapi_path(join_prefix(base_path, str(raw_path)))
        item_params = item.get("parameters") if isinstance(item.get("parameters"), list) else []
        for method, operation in item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            if not isinstance(operation, dict):
                continue
            op_id = str(operation.get("operationId") or f"{method.lower()}{full_path}")
            security: list[str] = []
            for entry in operation.get("security") or spec.get("security") or []:
                if isinstance(entry, dict):
                    security.extend(entry.keys())
            tags = operation.get("tags") if isinstance(operation.get("tags"), list) else []
            path_item = {method.lower(): operation}
            record: RouteRecord = {
                "method": method.upper(),
                "path": full_path,
                "operationId": op_id,
                "description": str(operation.get("summary") or operation.get("description") or ""),
                "adapter": adapter,
                "routerPath": str(raw_path),
                "security": security,
                "tags": [str(t) for t in tags],
                "openapi_path_item": path_item,
                "already_specified": True,
            }
            # Preserve path-level parameters on the operation for downstream assembly.
            if item_params:
                op_copy = dict(operation)
                merged = list(item_params) + list(op_copy.get("parameters") or [])
                op_copy["parameters"] = merged
                record["openapi_path_item"] = {method.lower(): op_copy}
            routes.append(route_to_dict(record))
    return routes


def pick_server_url(spec: dict[str, Any], fallback: str) -> str:
    """Choose a server URL from OpenAPI 3 servers[] or Swagger 2 host/basePath."""
    servers = spec.get("servers")
    if isinstance(servers, list) and servers:
        first = servers[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"])
    host = spec.get("host")
    schemes = spec.get("schemes") if isinstance(spec.get("schemes"), list) else ["https"]
    if host:
        scheme = str(schemes[0] if schemes else "https")
        return urljoin(f"{scheme}://{host}", str(spec.get("basePath") or "/"))
    return fallback
