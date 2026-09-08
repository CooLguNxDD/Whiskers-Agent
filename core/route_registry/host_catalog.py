"""Mirror host HttpRouteRegistry declarations into the live OperationCatalog.

Console REST handlers registered with ``@http_route_registry.route`` become
discoverable/callable via ``(owner, name)`` without hand-coded FE path strings.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import TYPE_CHECKING, Any

from core.route_registry.operation_catalog import get_operation_catalog
from core.route_registry.operation_descriptor import (
    AccessClass,
    HttpExposure,
    OperationDescriptor,
    Visibility,
)

if TYPE_CHECKING:
    from core.route_registry.http_route_registry import RouteDeclaration

logger = logging.getLogger("whiskers")

_PATH_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# (plugin_id, operation_id) → OperationDescriptor for host HTTP routes
_host_ops: dict[tuple[str, str], OperationDescriptor] = {}
_host_lock = threading.Lock()

# Owners that are policy stubs / non-executable — never publish
_SKIP_OWNERS = frozenset({"__policy__", ""})


def _infer_access(methods: tuple[str, ...]) -> AccessClass:
    """Map HTTP methods to AccessClass (GET family → read, else write)."""
    read_only = {"GET", "HEAD", "OPTIONS"}
    if methods and all(m.upper() in read_only for m in methods):
        return AccessClass.READ
    return AccessClass.WRITE


def _path_params_schema(path: str) -> dict[str, Any]:
    """Build a minimal object schema from ``{param}`` segments in *path*."""
    names = _PATH_PARAM_RE.findall(path or "")
    if not names:
        return {"type": "object", "properties": {}, "additionalProperties": True}
    properties = {
        name: {"type": "string", "title": name, "description": f"Path parameter `{name}`"}
        for name in names
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(names),
        "additionalProperties": True,
    }


def declaration_to_operations(decl: "RouteDeclaration") -> list[OperationDescriptor]:
    """Convert one HTTP RouteDeclaration into catalog OperationDescriptor(s).

    One op per HTTP method. Skips WS, policy stubs, missing names, and bare
    policy declarations without a callable endpoint.
    """
    if getattr(decl, "kind", "http") != "http":
        return []
    owner = (getattr(decl, "owner", None) or "").strip()
    if owner in _SKIP_OWNERS:
        return []
    if getattr(decl, "endpoint", None) is None:
        return []
    name = (getattr(decl, "name", None) or "").strip()
    if not name:
        return []
    path = getattr(decl, "path", None) or ""
    if not path.startswith("/"):
        return []

    methods = tuple(getattr(decl, "methods", None) or ("GET",))
    auth = getattr(decl, "auth_policy", None)
    auth_value = auth.value if hasattr(auth, "value") else str(auth or "none")
    visibility = (
        Visibility.PUBLIC_CATALOG
        if auth_value == "public"
        else Visibility.AUTHENTICATED
    )
    required_scopes = tuple(getattr(decl, "required_scopes", None) or ())
    # Host console APIs are already session/public gated by middleware; empty
    # scopes keep catalog visibility for any authenticated principal.
    input_schema = _path_params_schema(path)
    doc_raw = getattr(decl.endpoint, "__doc__", None)
    doc = doc_raw.strip().split("\n")[0] if isinstance(doc_raw, str) else ""
    description = doc or f"{methods[0]} {path}"

    ops: list[OperationDescriptor] = []
    for method in methods:
        method_u = (method or "GET").upper()
        op_id = name if len(methods) == 1 else f"{name}__{method_u.lower()}"
        ops.append(
            OperationDescriptor(
                plugin_id=owner,
                operation_id=op_id,
                description=description,
                input_schema=input_schema,
                access=_infer_access((method_u,)),
                required_scopes=required_scopes,
                visibility=visibility,
                version="1",
                tags=(owner.split(".")[-1] if "." in owner else owner, "host"),
                http=HttpExposure(
                    method=method_u,
                    path_template=path,
                    auth_policy=auth_value,
                ),
                mcp=None,
                ui=None,
                is_fast_path=False,
                callable_ref=None,
            )
        )
    return ops


def host_ops_for_owner(plugin_id: str) -> list[OperationDescriptor]:
    """Host-indexed operations for *plugin_id* (empty if none)."""
    with _host_lock:
        return [o for k, o in _host_ops.items() if k[0] == plugin_id]


def host_op_keys() -> set[tuple[str, str]]:
    """All (plugin_id, operation_id) pairs managed by the host index."""
    with _host_lock:
        return set(_host_ops.keys())


def publish_owner_merged(plugin_id: str, plugin_ops: list[OperationDescriptor] | None = None) -> int:
    """Publish *plugin_ops* merged with host-indexed ops for the same owner.

    Prevents RouteRegistry.contribute and host HTTP mirroring from wiping
    each other when they share a plugin_id (e.g. terminal control plane).
    """
    host = host_ops_for_owner(plugin_id)
    host_keys = {o.key for o in host}
    contrib = list(plugin_ops or [])
    # Drop contributed ops that collide with host keys (host HTTP is authoritative for those ids)
    contrib = [o for o in contrib if o.key not in host_keys]
    merged = contrib + host
    if not merged:
        return get_operation_catalog().remove_owner(plugin_id)
    return get_operation_catalog().publish_owner(plugin_id, merged)


def upsert_host_declaration(decl: "RouteDeclaration") -> int:
    """Index a declaration and republish its owner into OperationCatalog.

    Returns the number of ops published for that owner (0 if skipped).
    """
    ops = declaration_to_operations(decl)
    if not ops:
        return 0

    owner = ops[0].plugin_id
    with _host_lock:
        for op in ops:
            _host_ops[op.key] = op

    try:
        # Preserve non-host ops already published for this owner (plugin tools)
        catalog = get_operation_catalog()
        host_keys = host_op_keys()
        preserved = [o for o in catalog.ops_for_plugin(owner) if o.key not in host_keys]
        return publish_owner_merged(owner, preserved)
    except Exception:
        logger.exception("host_catalog: failed to publish owner=%s", owner)
        return 0


def remove_host_path(path: str, *, owner: str | None = None) -> None:
    """Drop host ops whose HttpExposure path matches *path* (and optional owner)."""
    with _host_lock:
        drop = [
            k
            for k, op in _host_ops.items()
            if op.http and op.http.path_template == path
            and (owner is None or k[0] == owner)
        ]
        owners: set[str] = set()
        for k in drop:
            owners.add(k[0])
            del _host_ops[k]

    catalog = get_operation_catalog()
    for o in owners:
        host_keys = host_op_keys()
        preserved = [op for op in catalog.ops_for_plugin(o) if op.key not in host_keys]
        publish_owner_merged(o, preserved)


def clear_host_catalog_index() -> None:
    """Clear the host op index (tests / full registry clear).

    Preserves non-host (plugin-contributed) ops that share an owner id.
    """
    with _host_lock:
        drop_by_owner: dict[str, set[tuple[str, str]]] = {}
        for k in _host_ops:
            drop_by_owner.setdefault(k[0], set()).add(k)
        _host_ops.clear()
    catalog = get_operation_catalog()
    for owner, keys in drop_by_owner.items():
        preserved = [o for o in catalog.ops_for_plugin(owner) if o.key not in keys]
        if preserved:
            catalog.publish_owner(owner, preserved)
        else:
            catalog.remove_owner(owner)


def host_ops_snapshot() -> list[OperationDescriptor]:
    """Return a copy of indexed host operations (tests/debug)."""
    with _host_lock:
        return list(_host_ops.values())
