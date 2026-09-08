"""Generic specialist stack: MCP tool discovery → agent execution → synthesis.

Portfolio discover/compose/bake is in ``plugins.portfolio_plugin`` and registers
as a specialist domain via ``register_specialist_domain``.
"""

from core_graph.subgraphs.specialist.graph_spec import (
    SPECIALIST_CONDITIONAL_EDGES,
    SPECIALIST_NODES,
    SPECIALIST_STATIC_EDGES,
)
from core_graph.subgraphs.specialist.pipeline import run_specialist_pipeline
from core_graph.subgraphs.specialist.registry import (
    clear_specialist_domains,
    list_specialist_domains,
    register_specialist_domain,
    run_specialist,
    unregister_specialist_domain,
)

__all__ = [
    "SPECIALIST_NODES",
    "SPECIALIST_STATIC_EDGES",
    "SPECIALIST_CONDITIONAL_EDGES",
    "run_specialist_pipeline",
    "run_specialist",
    "register_specialist_domain",
    "unregister_specialist_domain",
    "clear_specialist_domains",
    "list_specialist_domains",
]
