"""Canonical route-manifest record used by every ingest adapter."""

from __future__ import annotations

from typing import Any, TypedDict


class RouteRecord(TypedDict, total=False):
    """One HTTP operation in the pipeline's intermediate manifest.

    Required keys: method, path, operationId.
    Optional keys carry adapter metadata used by enrichment / OpenAPI assembly.
    """

    method: str
    path: str
    operationId: str
    description: str
    adapter: str
    routerPath: str
    router: str
    controller: str
    controllerFile: str
    handlerFile: str
    authGuards: list[str]
    security: list[str]
    tags: list[str]
    openapi_path_item: dict[str, Any]
    already_specified: bool


REQUIRED_ROUTE_KEYS = ("method", "path", "operationId")


def route_to_dict(route: RouteRecord | dict[str, Any]) -> dict[str, Any]:
    """Normalize a route record for JSON serialization."""
    method = str(route.get("method") or "GET").upper()
    path = str(route.get("path") or "")
    op_id = str(route.get("operationId") or "")
    out: dict[str, Any] = {
        "method": method,
        "path": path,
        "operationId": op_id,
        "description": route.get("description") or "",
        "adapter": route.get("adapter") or "",
        "routerPath": route.get("routerPath") or path,
        "router": route.get("router") or "",
        "controller": route.get("controller") or "",
        "controllerFile": route.get("controllerFile") or route.get("handlerFile") or None,
        "handlerFile": route.get("handlerFile") or route.get("controllerFile") or None,
        "authGuards": list(route.get("authGuards") or []),
        "security": list(route.get("security") or []),
        "tags": list(route.get("tags") or []),
    }
    if route.get("openapi_path_item"):
        out["openapi_path_item"] = route["openapi_path_item"]
    if route.get("already_specified"):
        out["already_specified"] = True
    return out
