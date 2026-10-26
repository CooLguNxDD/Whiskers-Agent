"""Delete scope and planner visibility contracts for portfolio tools."""

import json
from pathlib import Path

from core.scope_management import evaluate_access
from core.scope_management.principal import PrincipalKind, ScopeGrant
from core.scope_management.request import AccessRequest
from db_layer.embeddings.embeddings_routes import GOAP_CANDIDATE_DENYLIST
from plugins.portfolio_plugin.plugin_config import _GOAP_HIDDEN_OPS

PLUGIN = "portfolio_plugin"
OPS = ("delete_project", "delete_job_layout", "delete_bake_runs")
PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_delete_requires_dedicated_scope_and_is_hidden_from_goap():
    for op in OPS:
        operation_id = f"{PLUGIN}__{op}"
        for scope in ("group:portfolio_plugin:ask", "group:portfolio_plugin:read", "group:portfolio_plugin:write"):
            decision = evaluate_access(
                ScopeGrant([scope], kind=PrincipalKind.API_KEY),
                request=AccessRequest(plugin_id=PLUGIN, operation_id=operation_id, tags=(PLUGIN, "delete")),
            )
            assert not decision.allowed, (op, scope, decision)
        decision = evaluate_access(
            ScopeGrant(["group:portfolio_plugin:delete"], kind=PrincipalKind.API_KEY),
            request=AccessRequest(plugin_id=PLUGIN, operation_id=operation_id, tags=(PLUGIN, "delete")),
        )
        assert decision.allowed, (op, decision)
        assert (PLUGIN, operation_id) in GOAP_CANDIDATE_DENYLIST
        assert operation_id in _GOAP_HIDDEN_OPS


def test_manifest_declares_delete_scope_and_tools_not_write_tagged():
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert "group:portfolio_plugin:delete" in {item["token"] for item in manifest["scopes"]}
    source = (PLUGIN_DIR / "MCPTools" / "delete_tools.py").read_text(encoding="utf-8")
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in OPS:
            decorator = ast.get_source_segment(source, node.decorator_list[0])
            assert 'tags={"portfolio_plugin", "delete"}' in decorator
            assert '"write"' not in decorator
