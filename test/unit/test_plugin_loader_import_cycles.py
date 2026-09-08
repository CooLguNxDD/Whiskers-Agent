"""Assert core/plugin_loader/ has no module-level import cycles.

Walks every module in the package, parses its module-level `import`/`from`
statements with `ast` (skipping anything inside a `TYPE_CHECKING` block or
nested inside a function/method — those are the legitimate escape hatches
the package already uses to break real dependency cycles at runtime), builds
the intra-package import graph, and asserts it is acyclic.

Regression guard for the `plugin <-> plugin_registry` cycle that used to be
papered over by a method-local `get_registry()` import inside
`Plugin.on_ready` (see `core/plugin_loader/types.py`'s module docstring).
"""
import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[2] / "core" / "plugin_loader"
PACKAGE_PREFIX = "core.plugin_loader"


def _module_name(path: Path) -> str:
    return f"{PACKAGE_PREFIX}.{path.stem}"


def _is_type_checking_test(node: ast.expr) -> bool:
    """True for `if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` guards."""
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _module_level_intra_package_imports(path: Path) -> set[str]:
    """Return the set of sibling `core.plugin_loader.*` modules imported at module level.

    Only inspects statements in the module's top-level body — imports nested
    inside `if TYPE_CHECKING:` or inside any function/class body are excluded
    by construction, since we never recurse into those node types.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    targets: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            continue  # TYPE_CHECKING-only imports are a sanctioned escape hatch
        if isinstance(node, ast.ImportFrom):
            if node.level and node.level >= 1:
                # relative import: `from .sibling import X` / `from . import sibling`
                if node.module:
                    targets.add(f"{PACKAGE_PREFIX}.{node.module}")
                else:
                    for alias in node.names:
                        targets.add(f"{PACKAGE_PREFIX}.{alias.name}")
            elif node.module and node.module.startswith(PACKAGE_PREFIX):
                targets.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE_PREFIX):
                    targets.add(alias.name)

    return targets


def _build_import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in PACKAGE_DIR.glob("*.py"):
        if path.stem == "__init__":
            continue
        graph[_module_name(path)] = _module_level_intra_package_imports(path)
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """DFS cycle detection; returns the cycle path (module names) or None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    path_stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        path_stack.append(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in graph:
                continue  # import of something outside this package's own modules
            if color[neighbor] == GRAY:
                cycle_start = path_stack.index(neighbor)
                return path_stack[cycle_start:] + [neighbor]
            if color[neighbor] == WHITE:
                result = visit(neighbor)
                if result:
                    return result
        path_stack.pop()
        color[node] = BLACK
        return None

    for node in sorted(graph):
        if color[node] == WHITE:
            cycle = visit(node)
            if cycle:
                return cycle
    return None


def test_plugin_loader_package_has_no_module_level_import_cycle():
    """No two modules in core/plugin_loader/ may import each other at module level."""
    graph = _build_import_graph()
    assert graph, "expected to find modules under core/plugin_loader/"
    cycle = _find_cycle(graph)
    assert cycle is None, (
        "module-level import cycle detected in core/plugin_loader/: "
        + " -> ".join(cycle)
        + " (break it with a TYPE_CHECKING-only import, a function-local import, "
        "or by typing against core.plugin_loader.types instead)"
    )


def test_plugin_and_plugin_registry_do_not_import_each_other_at_module_level():
    """Regression guard for the specific cycle types.py exists to prevent."""
    graph = _build_import_graph()
    plugin_deps = graph[f"{PACKAGE_PREFIX}.plugin"]
    registry_deps = graph[f"{PACKAGE_PREFIX}.plugin_registry"]
    assert f"{PACKAGE_PREFIX}.plugin_registry" not in plugin_deps, (
        "plugin.py must not import plugin_registry at module level — "
        "type against core.plugin_loader.types.IPluginContext / use ctx._registry instead"
    )
    # plugin_registry.py importing plugin.py (for the concrete Plugin re-export
    # every plugin_config.py relies on) is fine and expected — it is one-way.
    assert f"{PACKAGE_PREFIX}.plugin" in registry_deps
