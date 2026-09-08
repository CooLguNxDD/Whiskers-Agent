from dataclasses import dataclass
from typing import Callable, Union
import importlib

Target = Union[str, Callable[[], str]]  # "END" sentinel string, or callable resolved at build time

# 1. Verbatim ported flag-helpers
def decompose_first_enabled() -> bool:
    """Whether the decompose-first pipeline (decompose → embedder → planner) is on."""
    from utils.server_config import DECOMPOSE_FIRST
    return bool(DECOMPOSE_FIRST)


def planning_entry() -> str:
    """Target node for entering (or re-entering) the planning cluster."""
    return "decompose" if decompose_first_enabled() else "embedder"


def replan_entry() -> str:
    """Target node for the validator's replan edge (fresh decomposition when flag on)."""
    return "decompose" if decompose_first_enabled() else "planner"


# 2. Frozen dataclasses
@dataclass(frozen=True)
class NodeSpec:
    """Declarative node entry: name, factory dotted path, optional include predicate."""
    name: str
    factory: str                            # dotted path e.g. "core_graph.node.make_planner_node"
    when: "Callable[[], bool] | None" = None  # None = always include


@dataclass(frozen=True)
class StaticEdge:
    """Fixed edge from source to target (node name or END), optionally gated."""
    source: str
    target: str                             # node name or "END"
    when: "Callable[[], bool] | None" = None


@dataclass(frozen=True)
class ConditionalEdge:
    """Router-driven edge: maps router keys to target nodes (or END/callables)."""
    source: str
    router: str                             # dotted path e.g. "core_graph.node.routers.gate_router"
    mapping: dict                           # router key -> node name / "END" / callable


@dataclass(frozen=True)
class GraphSpec:
    """Full graph topology: entry node, nodes, static edges, and conditional edges."""
    entry: str
    nodes: tuple[NodeSpec, ...]
    static_edges: tuple[StaticEdge, ...]
    conditional_edges: tuple[ConditionalEdge, ...]


# 3. GRAPH_SPEC — composed in subgraphs/root (triage ⊕ classic_goap).
# Imported lazily via __getattr__ below to avoid circular imports with
# core_graph.subgraphs.*.graph_spec fragments that import helpers from here.


# 4. ResolvedTopology and resolve_topology
@dataclass(frozen=True)
class ResolvedTopology:
    """Concrete topology after evaluating include predicates (nodes + edges)."""
    entry: str
    nodes: set[str]
    edges: list[tuple[str, str, bool]]


def resolve_topology(spec: GraphSpec | None = None) -> ResolvedTopology:
    """Evaluate when-predicates and return the active nodes/edges for a GraphSpec."""
    if spec is None:
        from core_graph.subgraphs.root.graph_spec import ROOT_SPEC

        spec = ROOT_SPEC
    active_nodes = set()
    for node in spec.nodes:
        if node.when is None or node.when():
            active_nodes.add(node.name)

    edges = []
    for se in spec.static_edges:
        if se.when is None or se.when():
            if se.target != "END":
                edges.append((se.source, se.target, False))

    for ce in spec.conditional_edges:
        for key, val in ce.mapping.items():
            target = val() if callable(val) else val
            if target != "END":
                edges.append((ce.source, target, True))

    return ResolvedTopology(
        entry=spec.entry,
        nodes=active_nodes,
        edges=edges,
    )


# 5. build_from_spec
def build_from_spec(graph, ctx, spec: GraphSpec | None = None) -> None:
    """Wire a LangGraph StateGraph from a GraphSpec (nodes, static + conditional edges)."""
    from langgraph.graph import END

    if spec is None:
        from core_graph.subgraphs.root.graph_spec import ROOT_SPEC

        spec = ROOT_SPEC

    # Resolve and add nodes
    for spec_node in spec.nodes:
        if spec_node.when is None or spec_node.when():
            try:
                parts = spec_node.factory.split(".")
                module_path = ".".join(parts[:-1])
                func_name = parts[-1]
                mod = importlib.import_module(module_path)
                factory_fn = getattr(mod, func_name)
            except (ImportError, AttributeError) as e:
                raise ImportError(
                    f"graph_spec: cannot resolve factory {spec_node.factory!r} "
                    f"for node {spec_node.name!r}: {e}"
                ) from e
            node_val = factory_fn(ctx)
            graph.add_node(spec_node.name, node_val)

    # Resolve and add static edges
    for se in spec.static_edges:
        if se.when is None or se.when():
            target_val = END if se.target == "END" else se.target
            graph.add_edge(se.source, target_val)

    # Resolve and add conditional edges
    for ce in spec.conditional_edges:
        try:
            parts = ce.router.split(".")
            module_path = ".".join(parts[:-1])
            func_name = parts[-1]
            mod = importlib.import_module(module_path)
            router_fn = getattr(mod, func_name)
        except (ImportError, AttributeError) as e:
            raise ImportError(
                f"graph_spec: cannot resolve router {ce.router!r} "
                f"for edge source {ce.source!r}: {e}"
            ) from e

        resolved_mapping = {}
        for key, val in ce.mapping.items():
            target = val() if callable(val) else val
            resolved_mapping[key] = END if target == "END" else target

        graph.add_conditional_edges(ce.source, router_fn, resolved_mapping)

    # Validate the resolved topology
    topology = resolve_topology(spec)
    _validate(topology)

    # Set entry point
    graph.set_entry_point(spec.entry)


# 6. _validate
def _validate(topology: ResolvedTopology) -> None:
    # 1. Edge source and target must be in active nodes
    for source, target, _ in topology.edges:
        if source not in topology.nodes:
            raise ValueError(f"graph_spec: edge references undeclared node: {(source, target)!r}")
        if target not in topology.nodes:
            raise ValueError(f"graph_spec: edge references undeclared node: {(source, target)!r}")

    # 2. BFS/DFS reachability
    if topology.entry not in topology.nodes:
        raise ValueError(f"graph_spec: entry point {topology.entry!r} not in active nodes")

    # Build adjacency
    adj = {node: [] for node in topology.nodes}
    for source, target, _ in topology.edges:
        if source in adj:
            adj[source].append(target)

    visited = {topology.entry}
    queue = [topology.entry]
    while queue:
        curr = queue.pop(0)
        for neighbor in adj[curr]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    unreached = topology.nodes - visited
    if unreached:
        raise ValueError(f"graph_spec: unreachable nodes: {sorted(unreached)}")


def __getattr__(name: str):
    """Lazy GRAPH_SPEC / ROOT_SPEC so fragment modules can import helpers first."""
    if name in ("GRAPH_SPEC", "ROOT_SPEC"):
        from core_graph.subgraphs.root.graph_spec import ROOT_SPEC

        if name == "ROOT_SPEC":
            return ROOT_SPEC
        return ROOT_SPEC
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
