"""Guard documented server configuration keys against code drift."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config" / "server_config_example.json"
# Each permitted omission must remain explicit and explainable.
ALLOWED_UNDOCUMENTED: set[str] = set()
CONFIG_ALIASES = {
    "PAGINATION_CONFIG": ("pagination",), "CONTEXT_CONFIG": ("context",),
    "PARAMETERS": ("parameters",), "OAUTH_CONFIG": ("oauth",),
    "GRAPH_CONFIG": ("graph",), "SCHEDULED_JOBS_CONFIG": ("scheduled_jobs",),
    "TERMINAL_RELAY_CONFIG": ("terminal_relay",), "GATEWAY_CONFIG": ("gateway",),
    "SECURITY_CONFIG": ("security",), "RATE_LIMIT_CONFIG": ("rate_limit",),
}


def collect_config_paths(source: str) -> set[str]:
    """Collect literal config paths from direct reads and known config aliases."""
    tree = ast.parse(source.removeprefix("\ufeff"))
    aliases = {"SERVER_CONFIG": (), **CONFIG_ALIASES}
    paths: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            value = node.value
            if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute) or value.func.attr != "get" or not value.args or not isinstance(value.args[0], ast.Constant) or not isinstance(value.args[0].value, str):
                continue
            base = value.func.value
            if isinstance(base, ast.Name) and base.id in aliases:
                path = (*aliases[base.id], value.args[0].value)
                paths.add(".".join(path))
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in aliases:
                        aliases[target.id] = path
                        changed = True
    def path_for(expression):
        if isinstance(expression, ast.Name):
            return aliases.get(expression.id)
        if isinstance(expression, ast.Call) and isinstance(expression.func, ast.Attribute) and expression.func.attr == "get" and expression.args and isinstance(expression.args[0], ast.Constant) and isinstance(expression.args[0].value, str):
            parent = path_for(expression.func.value)
            if parent is not None:
                return (*parent, expression.args[0].value)
            if isinstance(expression.func.value, ast.Call) and isinstance(expression.func.value.func, ast.Name) and expression.func.value.func.id == "load_server_config":
                return (expression.args[0].value,)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            path = path_for(node)
            if path:
                paths.add(".".join(path))
    return paths


def path_exists(config: dict, path: str) -> bool:
    """Return whether a dotted path is present, regardless of its value."""
    current = config
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def missing_config_paths(reads: set[str], example: dict) -> set[str]:
    """Return config reads absent from the example, excluding documented exceptions."""
    return {path for path in reads if not path_exists(example, path)}


def test_collector_handles_annotated_aliases_and_nested_reads():
    source = 'GRAPH_CONFIG: dict = SERVER_CONFIG.get("graph", {})\nGRAPH_CONFIG.get("max_fanout")\n'
    assert collect_config_paths(source) == {"graph", "graph.max_fanout"}


def test_collector_seeds_imported_scheduled_jobs_alias():
    assert collect_config_paths('SCHEDULED_JOBS_CONFIG.get("checkpoint_sweep", {}).get("interval_seconds")') == {
        "scheduled_jobs.checkpoint_sweep", "scheduled_jobs.checkpoint_sweep.interval_seconds"
    }


def test_missing_example_key_is_detected_by_drift_assertion():
    documented = {"graph.enabled", "scheduled_jobs.checkpoint_sweep.interval_seconds"}
    incomplete_example = {"graph": {"enabled": True}, "scheduled_jobs": {"checkpoint_sweep": {}}}
    assert missing_config_paths(documented, incomplete_example) == {"scheduled_jobs.checkpoint_sweep.interval_seconds"}


def test_server_config_reads_are_documented():
    """Every confidently mapped literal config read has an example entry."""
    sources = [ROOT / "utils" / "server_config.py"]
    sources.extend(ROOT / p for p in ("core_graph", "core", "api", "plugins", "db_layer") if (ROOT / p).exists())
    reads = set()
    for path in sources:
        for source_file in path.rglob("*.py") if path.is_dir() else [path]:
            reads.update(collect_config_paths(source_file.read_text(encoding="utf-8")))
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    missing = missing_config_paths(reads, example)
    assert missing == ALLOWED_UNDOCUMENTED, f"undocumented config paths: {sorted(missing - ALLOWED_UNDOCUMENTED)}; stale allow-list: {sorted(ALLOWED_UNDOCUMENTED - missing)}"
