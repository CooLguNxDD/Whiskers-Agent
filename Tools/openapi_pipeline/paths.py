"""Path-parameter helpers that accept Express `:id` and OpenAPI `{id}`."""

from __future__ import annotations

import re

_EXPRESS_PARAM_RE = re.compile(r":(\w+)")
_OPENAPI_PARAM_RE = re.compile(r"\{(\w+)\}")
_VERSION_RE = re.compile(r"^v\d+$", re.IGNORECASE)
_SKIP_SEGMENTS = frozenset({"api", "rest", "graphql", "public", "internal"})


def path_param_names(path: str) -> list[str]:
    """Return unique path parameter names in appearance order."""
    names: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r":(\w+)|\{(\w+)\}", path):
        name = match.group(1) or match.group(2)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def extract_path_params(path: str) -> list[dict]:
    """Return OpenAPI Parameter Objects for every path parameter."""
    params = []
    for name in path_param_names(path):
        schema = {"type": "integer"} if name.lower().endswith("id") else {"type": "string"}
        params.append({
            "name": name,
            "in": "path",
            "required": True,
            "schema": schema,
            "description": f"The {name}",
        })
    return params


def openapi_path(raw_path: str) -> str:
    """Normalize Express `:param` notation to OpenAPI `{param}`."""
    return _EXPRESS_PARAM_RE.sub(r"{\1}", raw_path)


def infer_tag(path: str, extra_skip: frozenset[str] | set[str] | None = None) -> str:
    """Best-effort tag from the first meaningful path segment."""
    skip = set(_SKIP_SEGMENTS)
    if extra_skip:
        skip.update(s.lower() for s in extra_skip)
    for segment in openapi_path(path).split("/"):
        if not segment:
            continue
        if segment.startswith("{") and segment.endswith("}"):
            continue
        lowered = segment.lower()
        if lowered in skip or _VERSION_RE.match(segment):
            continue
        tag = re.sub(r"([a-z])([A-Z])", r"\1-\2", segment).lower()
        return tag or "general"
    return "general"


def join_prefix(prefix: str, path: str) -> str:
    """Join a router prefix and a route path without doubling slashes."""
    if not prefix:
        return path if path.startswith("/") else f"/{path}"
    if not path or path == "/":
        return prefix.rstrip("/") or "/"
    return prefix.rstrip("/") + "/" + path.lstrip("/")
