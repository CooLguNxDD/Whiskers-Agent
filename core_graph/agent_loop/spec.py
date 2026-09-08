"""AgentSpec / ToolRef — declarative shape of one in-process tool-calling agent."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolRef:
    """One tool binding: (plugin_id, operation_id), operation_id may be a glob.

    ``plugin_id`` may also be a glob (e.g. ``proxy_github-*``) to bind every
    matching mounted proxy's tools. Resolved against the live OperationCatalog
    at run time by ``toolset.build_toolset`` — never resolved eagerly, since
    proxies mount/unmount at runtime.
    """

    plugin_id: str
    operation_id: str = "*"
    # Optional static description override (else pulled from the catalog op).
    description: str | None = None


@dataclass(frozen=True)
class AgentSpec:
    """Declarative spec for one in-process LLM tool-calling agent run."""

    name: str
    system_prompt: str
    tools: list[ToolRef] = field(default_factory=list)
    max_steps: int = 12
    max_seconds: float = 90.0
    # JSON-schema the final assistant message's structured output must match.
    # When set, the runner asks for (and repairs, once) a matching JSON object.
    output_schema: dict | None = None
    # "core" (default) uses the graph backbone LLM (get_graph_core_llm()).
    # Deprecated for anything else: when `model` is unset, a non-"core" value
    # here is treated as `model`'s selector (back-compat — this field used to
    # be a no-op for any value besides "core"; see runner._get_llm).
    llm_kind: str = "core"
    # Model selector for this agent run: an alias/effort/pool-name/numeric
    # selector (core_graph/model_roles/), or "role:<role_id>" to resolve a
    # declared ModelRoleSpec's first rung. None -> get_graph_core_llm().
    # Resolves ONE rung, never a full escalation ladder — the agent loop is
    # multi-step/stateful, so retrying it would re-run side-effecting tool
    # calls. See core_graph/model_roles/ladder.py::resolve_role_first_rung.
    model: str | None = None
    parallel_tool_calls: bool = True
    # Max tools resolved into the bound schema (glob expansion safety valve).
    max_tools: int = 40
    # Scopes granted to the caller invoking the agent loop.
    caller_scopes: list[str] | None = None

