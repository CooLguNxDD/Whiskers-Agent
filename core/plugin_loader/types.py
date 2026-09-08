"""
Shared structural protocols for the plugin loader package.

``plugin.py`` and ``plugin_lifecycle_registry.py`` used to reach each other's
concrete classes to satisfy type hints, which is what forced the deferred,
method-local import in ``Plugin.on_ready`` (see git history) — a real
``plugin_registry`` -> ``plugin`` -> ``plugin_registry`` cycle would occur if
that import were ever promoted to module level, since ``plugin_registry.py``
imports the concrete ``Plugin`` class at module level for re-export.

This module holds only ``typing.Protocol``/``TypedDict`` definitions and
imports nothing from sibling loader modules, so it can be imported from
anywhere in ``core/plugin_loader/`` without becoming part of any cycle.
Mirrors the ``core/interfaces/`` convention used for ``IProxyManager`` /
``IRouteRegistry`` / ``IScopeManager`` / ``IPermissionRegistry``.
"""
from typing import Any, Awaitable, Callable, List, Protocol, TypedDict


class IPluginContext(Protocol):
    """Structural surface a plugin's lifecycle hooks receive.

    Mirrors the public methods of the concrete ``PluginContext``
    (``plugin_context.py``) that ``Plugin`` and the lifecycle registry rely
    on. ``_registry`` is intentionally exposed — ``Plugin.on_load``/``on_ready``/
    ``on_unload`` read it directly rather than going through the global
    registry singleton, which is what keeps ``plugin.py`` cycle-free.
    """

    _registry: Any

    @property
    def artifact_store(self) -> Any:
        """Plugin-facing ``IArtifactStore`` — object bytes + short_id links.

        Typed ``Any`` here (not ``core.interfaces.IArtifactStore``) so this
        module keeps importing nothing but stdlib typing — see module
        docstring. Concrete shape lives in ``core/interfaces/artifact_store.py``.
        """
        ...

    @property
    def auth_service(self) -> Any:
        """Plugin-facing ``IAuthService`` — bearer/cookie -> principal, token minting.

        Typed ``Any`` for the same reason as ``artifact_store``. Concrete
        shape lives in ``core/interfaces/auth_service.py``.
        """
        ...

    def register_tool(self, name: str, fn: Callable) -> None: ...

    def get_app(self) -> Any: ...

    def register_service(self, service_name: str, service: Any) -> None: ...

    def get_service(self, service_name: str) -> Any: ...

    def register_auth(self, fn: "Callable[[], Awaitable[dict[str, str]]]") -> None: ...

    async def disabled_tool_names(self, plugin_id: str | None = None) -> set[str]: ...

    async def hidden_tool_names(self, plugin_id: str | None = None) -> set[str]: ...

    def delegate_auth_to(self, parent_plugin_id: str) -> None: ...

    def register_direct_auth_config(self, config: dict) -> None: ...

    def enable_tools(self) -> None: ...

    def disable_tools(self, names: set[str] | None = None) -> None: ...

    def contribute_routes(self, routes: list) -> None: ...

    def register_binding(self, binding: Any) -> None: ...

    def remove_routes(self) -> None: ...


class IPlugin(Protocol):
    """Structural surface ``PluginLifecycleRegistry`` drives every plugin through.

    Lets ``plugin_lifecycle_registry.py`` type ``register_plugin`` /
    ``_plugins`` / ``_plugin_id_map`` without a runtime import of the
    concrete ``Plugin`` class.
    """

    name: str
    version: str
    tier: int
    min_api_version: int

    async def on_load(self, ctx: IPluginContext) -> None: ...

    async def on_ready(self, ctx: IPluginContext) -> None: ...

    async def on_unload(self, ctx: IPluginContext) -> None: ...


class PluginManifest(TypedDict, total=False):
    """Shape of a parsed plugin ``manifest.json``.

    Non-exhaustive by design — mirrors only the top-level keys
    ``resolver.py::build_specs``/``parse_schema_cfg`` read to build a
    ``PluginSpec``, plus the ``scopes``/``gate``/``settings`` blocks documented
    in the root ``CLAUDE.md`` scope-vocabulary and specialist-stack sections.
    Plugin-specific ``settings`` sub-shapes are not modeled here.
    """

    name: str
    version: str
    tier: "str | int"
    requires: List[str]
    required_credentials: list
    optional_credentials: list
    skills: List[str]
    scopes: list
    gate: dict
    schema: dict
    settings: dict
