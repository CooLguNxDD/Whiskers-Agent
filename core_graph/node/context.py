"""
Graph context utilities.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class GraphRuntimeContext:
    """
    Runtime context for graph execution.
    """
    llm: Any
    context_params: dict = field(default_factory=dict)
    api_url: str = ""
    route_registry: Any = None
    checkpointer: Any = None

    async def llm_for_role(self, role: str) -> Any:
        """First-rung client for ``role`` (selection only, no escalation ladder).

        For nodes with nothing to validate (chat/builder/step_resolver) — see
        ``core_graph.model_roles``. Falls back to ``self.llm`` on any
        resolution failure, so this never changes behaviour when the model
        role layer is unavailable (flag off, empty registry, DB down).
        """
        try:
            from core_graph.model_roles.ladder import resolve_role_first_rung

            llm, _model_name = await resolve_role_first_rung(role, fallback=self.llm)
            return llm
        except Exception:
            return self.llm
