"""AccessRequest — the value object requirement providers and rules read.

Collapses the loose ``plugin_id=/tags=/tool_name=/path=`` kwarg bag that
``ScopeManager.evaluate`` has threaded since the two-level model into one
typed request. Existing call sites are not required to construct this
directly yet (``ScopeManager.evaluate`` builds one internally); new code
(providers, the level-3 gate rule) should prefer it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.route_registry.operation_descriptor import AccessClass


@dataclass(frozen=True)
class AccessRequest:
    """Everything a requirement provider or rule might need to know.

    ``access`` is intentionally ``Optional`` (default ``None``, not
    ``AccessClass.READ``): providers treat "unset" as "don't add an
    access-tagged requirement" so the default construction path stays byte-
    for-byte compatible with the legacy ``{plugin:<id>} ∪ {group:<id>:<tag>}``
    formula. Callers that want the finer read/write split pass ``access=``
    explicitly.
    """

    plugin_id: str = ""
    operation_id: str = ""              # today's tool_name, kept as an alias below
    tags: tuple[str, ...] = field(default_factory=tuple)
    access: Optional[AccessClass] = None
    core_domain: str = ""               # e.g. "whiskers.proxy", "config", "graph"
    tool_name: str = ""
    http_path: str = ""
    path: str = ""                      # "direct_tool" | "graph_step" | "compat" | ...

    def __post_init__(self) -> None:
        """Keep operation_id/tool_name in sync when only one is supplied."""
        if self.operation_id and not self.tool_name:
            object.__setattr__(self, "tool_name", self.operation_id)
        elif self.tool_name and not self.operation_id:
            object.__setattr__(self, "operation_id", self.tool_name)
