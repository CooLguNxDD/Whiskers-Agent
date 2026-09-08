"""
core_graph — Dynamic LangGraph orchestrator + route embedding pipeline.

Promoted from plugins/pro_plugin/src/pro_graph/ (core_014) so the dynamic
graph can be reached from any plugin (including free-tier installs).

Public surface:
  * ``build_dynamic_graph`` — graph factory (embedder → planner → builder →
    executor → validator → step_dispatcher)
  * ``RouteRegistry`` — central bus where plugins contribute their routes
  * ``RouteDescriptor`` — dataclass shared by registry + loaders + nodes
  * Loaders for the two contribution paths:
      - ``static_tool_loader`` for ``@mcp.tool``-decorated functions
      - ``dynamic_route_loader`` for JSON-spec endpoints

Hard requirement: ``DATABASE_URL`` + ``MASTER_KEY`` must be set. The graph
cannot start without Postgres + pgvector.
"""

from core_graph.graph import build_dynamic_graph
from core_graph.states import DynamicAPIState, ExecutionStep, RouteCandidate

# Registry symbols exported lazily — on demand to avoid eager module loads.
def __getattr__(name: str):
    if name == "RouteRegistry":
        from core.route_registry import RouteRegistry
        return RouteRegistry
    if name == "RouteDescriptor":
        from core.route_registry.route_descriptor import RouteDescriptor
        return RouteDescriptor
    # Allow ``core_graph.worker`` / submodule attribute access (importlib).
    import importlib

    try:
        return importlib.import_module(f"core_graph.{name}")
    except ModuleNotFoundError as exc:
        # Only swallow "no such submodule"; re-raise import errors from inside
        # an existing package (e.g. worker → missing plugin dependency).
        missing = getattr(exc, "name", "") or ""
        if missing == f"core_graph.{name}" or missing == name:
            raise AttributeError(
                f"module 'core_graph' has no attribute {name!r}"
            ) from exc
        raise


__all__ = [
    "build_dynamic_graph",
    "DynamicAPIState",
    "ExecutionStep",
    "RouteCandidate",
    "RouteRegistry",
    "RouteDescriptor",
]
