"""select_agent_model — the single, documented precedence chain for the specialist stack.

Every agentic unit inside the specialist stack (a FlowSpec stage, a domain
runner's agent call, the generic pipeline's agent call) picks its model
through this one function, so the precedence order lives in exactly one
place instead of being re-implemented per call site. First non-null wins:

1. ``stage_model``            — explicit selector on a FlowSpec stage
2. ``stage_effort``            -> ``effort:<level>``
3. ``flow_effort``              -> ``effort:<level>`` (flow-wide default)
4. ``manifest_effort_overrides[goal_class]`` -> ``effort:<level>``
5. ``manifest_effort``          -> ``effort:<level>`` (plugin-wide default)
6. ``subgraph_model_role``      -> ``"role:<id>"``
7. core role ``"specialist"``   -> ``"role:specialist"`` (falls back to core LLM)
8. ``None`` (caller uses ``get_graph_core_llm()``)

Returns an ``AgentSpec.model``-compatible selector string, or ``None``.
"""

from __future__ import annotations


def select_agent_model(
    *,
    stage_model: str | None = None,
    stage_effort: str | None = None,
    flow_effort: str | None = None,
    manifest_effort_overrides: dict[str, str] | None = None,
    manifest_effort: str | None = None,
    goal_class: str | None = None,
    subgraph_model_role: str | None = None,
    subgraph_effort: str | None = None,
) -> str | None:
    """Resolve a model selector for one agentic call, per the precedence above."""
    if stage_model:
        return stage_model
    if stage_effort:
        return f"effort:{stage_effort}"
    if flow_effort:
        return f"effort:{flow_effort}"
    if manifest_effort_overrides and goal_class and manifest_effort_overrides.get(goal_class):
        return f"effort:{manifest_effort_overrides[goal_class]}"
    if manifest_effort:
        return f"effort:{manifest_effort}"
    if subgraph_model_role:
        return f"role:{subgraph_model_role}"
    if subgraph_effort:
        return f"effort:{subgraph_effort}"
    return "role:specialist"
