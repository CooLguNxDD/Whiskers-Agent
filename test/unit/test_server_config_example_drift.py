"""Guard documented server configuration keys against code drift."""

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Read config/server_config_example.json only; never inspect the ignored live config/server_config.json.
EXAMPLE = ROOT / "config" / "server_config_example.json"

# Paths intentionally consumed as opaque/dynamic data rather than operator settings.
ALLOWED_UNDOCUMENTED = {}


def collect_config_paths(source: str) -> set[str]:
    """Collect confident literal config paths from server config aliases and direct reads."""
    tree = ast.parse(source.removeprefix("\ufeff"))
    aliases = {"SERVER_CONFIG": ()}
    paths: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Attribute) and call.func.attr == "get" and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    base = call.func.value
                    if isinstance(base, ast.Name) and base.id in aliases:
                        path = (*aliases[base.id], call.args[0].value)
                        paths.add(".".join(path))
                        if node.targets and isinstance(node.targets[0], ast.Name) and node.targets[0].id not in aliases:
                            aliases[node.targets[0].id] = path
                            changed = True
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "load_server_config":
                for parent in ast.walk(tree):
                    if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == "get" and isinstance(parent.func.value, ast.Call) and parent.func.value is node and parent.args and isinstance(parent.args[0], ast.Constant) and isinstance(parent.args[0].value, str):
                        paths.add(parent.args[0].value)
    # Reads through aliases at one nested level.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) and isinstance(node.func.value, ast.Name) and node.func.value.id in aliases:
            paths.add(".".join((*aliases[node.func.value.id], node.args[0].value)))
    return paths


def path_exists(config: dict, path: str) -> bool:
    """Return whether a dotted path is present, regardless of its configured value."""
    current = config
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def test_server_config_reads_are_documented():
    """Every confidently mapped literal config read has an example entry."""
    sources = [ROOT / "utils/server_config.py"]
    sources.extend(ROOT / p for p in ("core_graph", "core", "api", "plugins", "db_layer") if (ROOT / p).exists())
    reads = set()
    for path in sources:
        for source_file in path.rglob("*.py") if path.is_dir() else [path]:
            reads.update(collect_config_paths(source_file.read_text(encoding="utf-8")))
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    missing = reads - {key for key in reads if path_exists(example, key)}
    assert missing == set(ALLOWED_UNDOCUMENTED), f"undocumented config paths: {sorted(missing - set(ALLOWED_UNDOCUMENTED))}; stale allow-list: {sorted(set(ALLOWED_UNDOCUMENTED) - missing)}"


@pytest.mark.parametrize("path", ["graph.enabled", "scheduled_jobs.checkpoint_sweep.interval_seconds"])
def test_missing_example_key_is_detected(path):
    """Synthetic config proves deleting a documented key is detected."""
    assert not path_exists({"graph": {}, "scheduled_jobs": {"checkpoint_sweep": {}}}, path)
