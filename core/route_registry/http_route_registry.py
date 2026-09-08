"""Unified HTTP and WebSocket route registry.

This module provides a singleton registry for managing dynamic routes, handling both
early-bound (custom_route) and late-bound (app router) mounting.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any, Callable

from starlette.routing import Route, WebSocketRoute

from core.route_registry.path_builder import build_api_path

logger = logging.getLogger("whiskers")


_PATH_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[a-zA-Z_]+)?\}")


@lru_cache(maxsize=512)
def _compile_path_template(template: str) -> re.Pattern:
    """Compile a Starlette-style ``{param}``/``{param:convertor}`` path template.

    Pure function of the template string, so ``lru_cache`` needs no explicit
    invalidation hook — a route removed from the registry just stops being
    looked up; its stale cache entry is harmless and bounded by maxsize.
    """
    # split() with a capturing group returns [literal, name, literal, name, ...];
    # escape each literal segment, substitute a single-path-segment matcher
    # for each {param}/{param:convertor} marker.
    parts = _PATH_PARAM_RE.split(template)
    escaped = "".join(
        re.escape(part) if i % 2 == 0 else "[^/]+"
        for i, part in enumerate(parts)
    )
    return re.compile(f"^{escaped}$")


class AuthPolicy(str, Enum):
    """Policies defining authorization requirements for a route."""

    PUBLIC = "public"
    SESSION_GATED = "session_gated"
    SCOPE_REQUIRED = "scope_required"
    NONE = "none"


@dataclass
class RouteDeclaration:
    """Represents a declared route and its configuration."""

    path: str
    endpoint: Callable | None
    kind: str = "http"
    methods: tuple[str, ...] = ("GET",)
    name: str | None = None
    middleware: list = field(default_factory=list)
    auth_policy: AuthPolicy = AuthPolicy.NONE
    required_scopes: tuple[str, ...] = ()
    owner: str = "core"
    match_mode: str = "prefix"
    include_in_schema: bool = True

    def to_starlette(self) -> Route | WebSocketRoute:
        """Convert the declaration into a Starlette Route or WebSocketRoute."""
        if self.kind == "ws":
            return WebSocketRoute(self.path, self.endpoint, name=self.name)
        return Route(
            self.path,
            self.endpoint,
            methods=list(self.methods),
            name=self.name,
            middleware=self.middleware or None,
            include_in_schema=self.include_in_schema,
        )


class HttpRouteRegistry:
    """Registry managing the declaration and mounting of routes."""

    def __init__(self, mcp: Any) -> None:
        """Initialize the registry with a reference to the MCP server."""
        self._mcp = mcp
        self._app: Any = None
        self._router: Any = None
        self._decls: dict[str, RouteDeclaration] = {}
        self._all_decls: list[RouteDeclaration] = []
        self._mounted: dict[str, object] = {}
        self._pending_late: list[RouteDeclaration] = []

    def clear(self) -> None:
        """Clear all declarations, mounted routes, and pending routes."""
        self._decls.clear()
        self._all_decls.clear()
        self._mounted.clear()
        self._pending_late.clear()
        try:
            from core.route_registry.host_catalog import clear_host_catalog_index
            clear_host_catalog_index()
        except Exception:
            # Catalog mirror optional during early boot / tests.
            logger.debug("http_route_registry.clear: host catalog clear failed", exc_info=True)
        _reset_middleware_cache()

    def bind_app(self, app: Any) -> None:
        """Bind the underlying Starlette application to enable late mounting."""
        self._app = app
        self._router = app.router

    @property
    def is_http_active(self) -> bool:
        """Return True if an application is bound and active."""
        return self._router is not None

    def declare(self, decl: RouteDeclaration) -> RouteDeclaration:
        """Record a route declaration, and mount/queue it appropriately.

        Named HTTP handlers are also mirrored into the live OperationCatalog
        under ``(owner, name)`` so the console can call them via the inference
        client without hardcoding paths.
        """
        prev = self._decls.get(decl.path)
        self._decls[decl.path] = decl
        self._all_decls.append(decl)

        if decl.endpoint is None:
            _reset_middleware_cache()
            return decl

        if not self.is_http_active:
            if decl.kind == "http" and not decl.middleware:
                self._mcp.custom_route(
                    decl.path,
                    methods=list(decl.methods),
                    name=decl.name,
                    include_in_schema=decl.include_in_schema,
                )(decl.endpoint)
            else:
                self._pending_late.append(decl)
        else:
            if prev is not None and prev.path in self._mounted:
                self.unmount(prev.path, keep_decl=True)
            self.mount(decl)

        # Live catalog mirror (skip policy stubs / WS via host_catalog filters)
        try:
            from core.route_registry.host_catalog import upsert_host_declaration
            upsert_host_declaration(decl)
        except Exception:
            # Catalog mirror optional during early boot / tests.
            logger.debug("http_route_registry.declare: host catalog upsert failed", exc_info=True)

        _reset_middleware_cache()
        return decl

    def route(
        self,
        path: str | None = None,
        *,
        route: str | None = None,
        endpoint: str = "",
        methods: tuple[str, ...] = ("GET",),
        name: str | None = None,
        kind: str = "http",
        middleware: list | None = None,
        auth_policy: AuthPolicy = AuthPolicy.NONE,
        required_scopes: tuple[str, ...] = (),
        owner: str = "core",
        match_mode: str = "prefix",
        include_in_schema: bool = True,
    ) -> Callable:
        """Decorator to declare a route inline."""
        if route is not None:
            resolved_path = build_api_path(route, auth_policy, endpoint)
        elif path is not None:
            resolved_path = path
        else:
            raise ValueError("Either 'path' or 'route' must be provided")

        def deco(fn: Callable) -> Callable:
            """Inner decorator function returning the decorated endpoint."""
            self.declare(
                RouteDeclaration(
                    path=resolved_path,
                    endpoint=fn,
                    kind=kind,
                    methods=methods,
                    name=name,
                    middleware=middleware or [],
                    auth_policy=auth_policy,
                    required_scopes=required_scopes,
                    owner=owner,
                    match_mode=match_mode,
                    include_in_schema=include_in_schema,
                )
            )
            return fn
        return deco

    def register_ws_route(
        self,
        path: str | None = None,
        endpoint: Callable | str | None = None,
        name: str | None = None,
        owner: str = "core",
        auth_policy: AuthPolicy = AuthPolicy.NONE,
        *,
        route: str | None = None,
        handler: Callable | None = None,
    ) -> RouteDeclaration:
        """Declare a new WebSocket route."""
        if route is not None:
            subpath = endpoint if isinstance(endpoint, str) else ""
            resolved_path = build_api_path(route, auth_policy, subpath)
        elif path is not None:
            resolved_path = path
        else:
            raise ValueError("Either 'path' or 'route' must be provided")

        if handler is not None:
            fn = handler
        elif callable(endpoint):
            fn = endpoint
        else:
            fn = None

        return self.declare(
            RouteDeclaration(
                path=resolved_path,
                endpoint=fn,
                kind="ws",
                name=name,
                owner=owner,
                auth_policy=auth_policy,
            )
        )

    def register_http_route(
        self,
        path: str | None = None,
        endpoint: Callable | str | None = None,
        methods: list[str] | tuple[str, ...] = ("GET",),
        name: str | None = None,
        owner: str = "core",
        auth_policy: AuthPolicy = AuthPolicy.SESSION_GATED,
        *,
        route: str | None = None,
        handler: Callable | None = None,
    ) -> RouteDeclaration:
        """Declare a new HTTP route."""
        if route is not None:
            subpath = endpoint if isinstance(endpoint, str) else ""
            resolved_path = build_api_path(route, auth_policy, subpath)
        elif path is not None:
            resolved_path = path
        else:
            raise ValueError("Either 'path' or 'route' must be provided")

        if handler is not None:
            fn = handler
        elif callable(endpoint):
            fn = endpoint
        else:
            fn = None

        return self.declare(
            RouteDeclaration(
                path=resolved_path,
                endpoint=fn,
                methods=tuple(methods),
                name=name,
                owner=owner,
                auth_policy=auth_policy,
            )
        )

    def mount(self, decl: RouteDeclaration) -> bool:
        """Mount a declaration to the active router."""
        if not self.is_http_active:
            _reset_middleware_cache()
            return False
        if decl.path in self._mounted:
            _reset_middleware_cache()
            return True
        obj = decl.to_starlette()
        self._router.routes.append(obj)
        self._mounted[decl.path] = obj
        self._decls[decl.path] = decl
        _reset_middleware_cache()
        return True

    def unmount(self, path: str, *, keep_decl: bool = False) -> bool:
        """Unmount a live route from the active router."""
        if not self.is_http_active:
            _reset_middleware_cache()
            return False

        removed = False
        if path in self._mounted:
            obj = self._mounted.pop(path)
            try:
                self._router.routes.remove(obj)
            except ValueError:
                pass
            removed = True

        if not keep_decl:
            self._decls.pop(path, None)
            try:
                from core.route_registry.host_catalog import remove_host_path
                remove_host_path(path)
            except Exception:
                # Catalog mirror optional during early boot / tests.
                logger.debug("http_route_registry.unmount: host catalog remove failed", exc_info=True)

        _reset_middleware_cache()
        return removed

    def mount_owner(self, owner: str) -> int:
        """Mount all unmounted routes belonging to a specific owner."""
        count = 0
        for decl in self._decls.values():
            if (
                decl.owner == owner
                and decl.path not in self._mounted
                and decl.endpoint is not None
            ):
                if self.mount(decl):
                    count += 1
        return count

    def unmount_owner(self, owner: str) -> int:
        """Unmount all live routes belonging to a specific owner."""
        count = 0
        paths_to_unmount = [p for p, d in self._decls.items() if d.owner == owner]
        for p in paths_to_unmount:
            if self.unmount(p, keep_decl=True):
                count += 1
        return count

    def drain_pending(self) -> int:
        """Mount all queued routes that were declared before app bind."""
        count = 0
        for decl in self._pending_late:
            if decl.path not in self._mounted:
                if self.mount(decl):
                    count += 1
        self._pending_late.clear()
        return count

    def public_prefixes(self) -> tuple[str, ...]:
        """Return paths with PUBLIC auth policy mapped as prefixes."""
        return tuple(
            d.path
            for d in self._decls.values()
            if d.auth_policy == AuthPolicy.PUBLIC and d.match_mode == "prefix"
        )

    def gated_prefixes(self) -> tuple[str, ...]:
        """Return paths with gated auth policies mapped as prefixes."""
        return tuple(
            d.path
            for d in self._decls.values()
            if d.auth_policy in (AuthPolicy.SESSION_GATED, AuthPolicy.SCOPE_REQUIRED)
            and d.match_mode == "prefix"
        )

    def gated_exact(self) -> tuple[str, ...]:
        """Return paths with gated auth policies mapped as exact matches."""
        return tuple(
            d.path
            for d in self._decls.values()
            if d.auth_policy in (AuthPolicy.SESSION_GATED, AuthPolicy.SCOPE_REQUIRED)
            and d.match_mode == "exact"
        )

    def required_scopes_for(self, path: str, method: str | None = None) -> tuple[str, ...]:
        """Return required scopes configured for the given concrete request path.

        Exact hit first (the common case — most declared paths carry no
        ``{param}`` template). Falls back to matching against every
        templated declaration (``/api/config/.../model-roles/{role_id}``)
        via a compiled regex, so a param-bearing route's ``required_scopes``
        actually resolves for a real request instead of only for its
        literal template string.

        When ``method`` is given, declarations matching the HTTP method take
        precedence over arbitrary method declarations on the same path.
        """
        if method:
            norm_method = method.upper()
            for decl in self._all_decls:
                if decl.path == path and any(m.upper() == norm_method for m in decl.methods):
                    return decl.required_scopes

            for decl in self._all_decls:
                if "{" in decl.path and any(m.upper() == norm_method for m in decl.methods):
                    if _compile_path_template(decl.path).match(path):
                        return decl.required_scopes

        decl = self._decls.get(path)
        if decl is not None:
            return decl.required_scopes

        for template, target_decl in self._decls.items():
            if "{" not in template:
                continue
            if _compile_path_template(template).match(path):
                return target_decl.required_scopes
        return ()

    def seed_default_policies(self) -> None:
        """Insert synthetic policy-only declarations for baseline endpoints."""
        public_paths = [
            "/.well-known/",
            "/oauth/plugin/",
            "/oauth/connect/",
            "/oauth/complete-layer1/",
        ]
        for path in public_paths:
            self.declare(
                RouteDeclaration(
                    path=path,
                    endpoint=None,
                    auth_policy=AuthPolicy.PUBLIC,
                    owner="__policy__",
                    match_mode="prefix",
                )
            )

        gated_prefixes = ["/oauth/authorize", "/connect", "/callback", "/plugins"]
        for path in gated_prefixes:
            self.declare(
                RouteDeclaration(
                    path=path,
                    endpoint=None,
                    auth_policy=AuthPolicy.SESSION_GATED,
                    owner="__policy__",
                    match_mode="prefix",
                )
            )

        self.declare(
            RouteDeclaration(
                path="/",
                endpoint=None,
                auth_policy=AuthPolicy.SESSION_GATED,
                owner="__policy__",
                match_mode="exact",
            )
        )


_http_route_registry: HttpRouteRegistry | None = None


def get_http_route_registry() -> HttpRouteRegistry:
    """Retrieve the global route registry instance."""
    if _http_route_registry is None:
        raise RuntimeError("HttpRouteRegistry not initialized")
    return _http_route_registry


def _set_http_route_registry(r: HttpRouteRegistry | None) -> None:
    """Set the global route registry instance."""
    global _http_route_registry
    _http_route_registry = r


# Callbacks registered by upper layers (e.g. api.middleware) so this package
# never imports ``api`` (layer inversion fix).
_routes_changed_callbacks: list[Callable[[], None]] = []


def register_routes_changed_callback(cb: Callable[[], None]) -> None:
    """Register a no-arg callback invoked after route registry mutations.

    Idempotent for the same callable identity. Used by ``api.middleware`` to
    invalidate prefix caches without ``core`` importing ``api``.
    """
    if cb not in _routes_changed_callbacks:
        _routes_changed_callbacks.append(cb)


def _reset_middleware_cache() -> None:
    """Notify registered listeners that route policy caches may be stale."""
    for cb in list(_routes_changed_callbacks):
        try:
            cb()
        except Exception:
            # Listener optional during early boot / partial imports.
            logger.debug("http_route_registry: routes-changed callback failed", exc_info=True)
