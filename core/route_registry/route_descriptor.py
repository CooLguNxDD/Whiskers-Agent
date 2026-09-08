"""
RouteDescriptor — the unit of contribution that flows through the registry.

Each plugin emits one RouteDescriptor per operation it exposes. The descriptor
carries everything the embedder/planner/builder/executor need: identity
(``plugin_id`` + ``operation_id``), HTTP shape (``method`` + ``path_template``
+ ``parameters``), semantic text (``description``), execution mode
(``is_fast_path`` + optional ``callable_ref``), and a stable ``content_hash``
the embedding worker uses for idempotency.

``to_operation()`` maps this compat façade into the canonical OperationDescriptor
contract used by the live OperationCatalog.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from core.route_registry.operation_descriptor import (
    AccessClass,
    HttpExposure,
    McpExposure,
    OperationDescriptor,
    Visibility,
)


@dataclass(frozen=True)
class RouteDescriptor:
    """Immutable description of a single operation a plugin contributes.

    ``content_hash`` is derived from description + parameters (+ contract fields)
    so the embedding worker can detect "no change → skip re-embed".
    """

    plugin_id: str
    operation_id: str
    description: str
    parameters: dict = field(default_factory=dict)
    method: str = ""
    path_template: str = ""
    is_fast_path: bool = False
    callable_ref: Callable[..., Any] | None = None
    tags: tuple[str, ...] = ()
    # Contract-layer extensions (optional; defaults preserve loader call sites)
    output_schema: dict | None = None
    access: str = ""  # empty → inferred from tags
    required_scopes: tuple[str, ...] = ()
    visibility: str = "authenticated"
    version: str = "1"
    workspace_label: str | None = None

    @property
    def effective_workspace_label(self) -> str | None:
        """Return explicit workspace_label or extract from tags formatted as 'workspace:<label>'."""
        if self.workspace_label:
            return self.workspace_label
        for t in self.tags:
            if t.startswith("workspace:"):
                return t.split("workspace:", 1)[1]
        return None

    @property
    def content_hash(self) -> str:
        """Stable hash of description + parameters + contract fields. Embedding dedup key."""
        blob = json.dumps(
            {
                "d": self.description,
                "p": self.parameters,
                "m": self.method,
                "pt": self.path_template,
                "os": self.output_schema,
                "a": self.access,
                "rs": list(self.required_scopes),
                "v": self.visibility,
                "ver": self.version,
                "wl": self.effective_workspace_label,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_payload(self) -> dict:
        """Serializable shape for the embedding_jobs payload column."""
        return {
            "operation_id": self.operation_id,
            "plugin_id": self.plugin_id,
            "description": self.description,
            "parameters": self.parameters,
            "method": self.method,
            "path_template": self.path_template,
            "is_fast_path": self.is_fast_path,
            "tags": list(self.tags),
            "output_schema": self.output_schema,
            "access": self.access,
            "required_scopes": list(self.required_scopes),
            "visibility": self.visibility,
            "version": self.version,
            "workspace_label": self.effective_workspace_label,
            "param_names": _extract_param_names(self.parameters),
        }

    @property
    def key(self) -> tuple[str, str]:
        """Return the unique (plugin_id, operation_id) tuple identifying this route."""
        return (self.plugin_id, self.operation_id)

    def _infer_access(self) -> AccessClass:
        """Infer AccessClass from explicit access string or tags."""
        if self.access:
            try:
                return AccessClass(self.access)
            except ValueError:
                pass
        tags_lower = {t.lower() for t in (self.tags or ())}
        if "admin" in tags_lower:
            return AccessClass.ADMIN
        if "write" in tags_lower:
            return AccessClass.WRITE
        return AccessClass.READ

    def _infer_visibility(self) -> Visibility:
        """Parse visibility string; default authenticated."""
        try:
            return Visibility(self.visibility or "authenticated")
        except ValueError:
            return Visibility.AUTHENTICATED

    def to_operation(self) -> OperationDescriptor:
        """Map this route descriptor into the canonical OperationDescriptor."""
        http: HttpExposure | None = None
        mcp: McpExposure | None = None
        if self.method and self.method.upper() != "CALL" and self.path_template:
            http = HttpExposure(
                method=self.method,
                path_template=self.path_template,
            )
        elif self.method and self.path_template and not self.is_fast_path:
            http = HttpExposure(
                method=self.method,
                path_template=self.path_template,
            )
        if self.is_fast_path or (self.method or "").upper() == "CALL":
            tool_name = self.operation_id
            if "__" in tool_name:
                tool_name = tool_name.split("__", 1)[1]
            mcp = McpExposure(
                tool_name=tool_name,
                qualified_name=self.operation_id,
            )
        # Dynamic HTTP routes still carry method+path
        if http is None and self.method and self.path_template:
            http = HttpExposure(method=self.method, path_template=self.path_template)

        return OperationDescriptor(
            plugin_id=self.plugin_id,
            operation_id=self.operation_id,
            description=self.description,
            input_schema=self.parameters or {},
            output_schema=self.output_schema,
            access=self._infer_access(),
            required_scopes=tuple(self.required_scopes or ()),
            visibility=self._infer_visibility(),
            version=self.version or "1",
            tags=tuple(self.tags or ()),
            http=http,
            mcp=mcp,
            ui=None,
            is_fast_path=self.is_fast_path,
            callable_ref=self.callable_ref,
        )


def _extract_param_names(parameters: dict | None) -> list[str]:
    """Extract list of parameter names from parameters dict (JSON Schema or flat dict)."""
    if not parameters or not isinstance(parameters, dict):
        return []
    if "properties" in parameters and isinstance(parameters["properties"], dict):
        return list(parameters["properties"].keys())
    meta_keys = {"type", "properties", "required", "title", "description", "$schema", "additionalProperties"}
    keys = [k for k in parameters.keys() if k not in meta_keys]
    return keys


@dataclass(frozen=True)
class InstanceBinding:
    """Per-instance (per-org) runtime config + optional callable override.

    Routes carry shape (description, parameters, default callable) keyed by
    ``(plugin_id, operation_id)``. Bindings carry the per-org config the
    executor injects at call time, keyed by ``(plugin_id, instance_id)``.

    ``instance_id="default"`` is the implicit single-tenant binding every
    plugin registers; multi-org plugins register one additional binding per
    org with the appropriate config.
    """

    plugin_id: str
    instance_id: str = "default"
    config: dict = field(default_factory=dict)
    # When set, overrides the route's default callable for this specific
    # instance. Rare — only needed when an org runs against a different
    # backend implementation, not just different config values.
    callable_override: Callable[..., Any] | None = None

    @property
    def key(self) -> tuple[str, str]:
        """Return the unique (plugin_id, instance_id) tuple identifying this binding."""
        return (self.plugin_id, self.instance_id)
