"""ACCESS_PATH_CONTRACTS — golden matrix C01–C12 for scope enforcement."""

from __future__ import annotations

from core.scope_management.principal import PrincipalKind

# Each contract: id, principal (kind/role/scopes), action, expected allow|deny
ACCESS_PATH_CONTRACTS: list[dict] = [
    {
        "id": "C01",
        "description": "master session read → allow",
        "principal": {
            "kind": PrincipalKind.SESSION_USER,
            "role": "master",
            "scopes": ["admin"],  # playground_mcp_scopes master includes admin
        },
        "action": {
            "plugin_id": "fake_plugin",
            "tags": ["read"],
            "tool_name": "list_records",
            "path": "direct_tool",
        },
        "expected": "allow",
    },
    {
        "id": "C02",
        "description": "viewer write → deny",
        "principal": {
            "kind": PrincipalKind.SESSION_USER,
            "role": "viewer",
            "scopes": ["group:fake_plugin:read"],  # read-only groups
        },
        "action": {
            "plugin_id": "fake_plugin",
            "tags": ["write"],
            "tool_name": "create_record",
            "path": "direct_tool",
        },
        "expected": "deny",
    },
    {
        "id": "C03",
        "description": "API key ['all'] → allow",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": ["all"],
        },
        "action": {
            "plugin_id": "jules_plugin",
            "tags": ["sessions"],
            "tool_name": "julescreate_session",
            "path": "direct_tool",
        },
        "expected": "allow",
    },
    {
        "id": "C04",
        "description": "API key ['opencat'] proxy → deny",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": ["opencat"],
        },
        "action": {
            "plugin_id": "proxy_notion",
            "tags": ["proxy"],
            "tool_name": "proxy_notion_search",
            "path": "direct_tool",
        },
        "expected": "deny",
    },
    {
        "id": "C05",
        "description": "API key [] → deny",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": [],
        },
        "action": {
            "plugin_id": "fake_plugin",
            "tags": ["read"],
            "tool_name": "list_records",
            "path": "direct_tool",
        },
        "expected": "deny",
    },
    {
        "id": "C06",
        "description": "OAuth operator pending role → operator scopes allow data plugin",
        "principal": {
            "kind": PrincipalKind.OAUTH_CLIENT,
            "role": "operator",
            # scopes after union with playground_mcp_scopes(operator)
            "scopes": ["plugin:fake_plugin", "group:fake_plugin:read"],
        },
        "action": {
            "plugin_id": "fake_plugin",
            "tags": ["read"],
            "tool_name": "list_records",
            "path": "direct_tool",
        },
        "expected": "allow",
    },
    {
        "id": "C07",
        "description": "anonymous HTTP → deny",
        "principal": {
            "kind": PrincipalKind.ANONYMOUS,
            "role": None,
            "scopes": [],
        },
        "action": {
            "plugin_id": "fake_plugin",
            "tags": ["read"],
            "tool_name": "list_records",
            "path": "direct_tool",
        },
        "expected": "deny",
    },
    {
        "id": "C08",
        "description": "LOCAL_CLI graph step → allow",
        "principal": {
            "kind": PrincipalKind.LOCAL_CLI,
            "role": None,
            "scopes": None,
        },
        "action": {
            "plugin_id": "fake_plugin",
            "tags": ["write"],
            "tool_name": "create_record",
            "path": "graph_step",
        },
        "expected": "allow",
    },
    {
        "id": "C09",
        "description": "authenticated + unresolved plugin_id → deny",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": ["plugin:fake_plugin"],
        },
        "action": {
            "plugin_id": "",
            "tags": (),
            "tool_name": "unknown_tool",
            "path": "direct_tool",
        },
        "expected": "deny",
    },
    {
        "id": "C10",
        "description": "viewer REST invoke write → deny",
        "principal": {
            "kind": PrincipalKind.SESSION_USER,
            "role": "viewer",
            "scopes": ["group:fake_plugin:read"],
        },
        "action": {
            "plugin_id": "fake_plugin",
            "tags": ["write"],
            "tool_name": "create_record",
            "path": "direct_tool",
        },
        "expected": "deny",
    },
    {
        "id": "C11",
        "description": "master REST invoke read → allow",
        "principal": {
            "kind": PrincipalKind.SESSION_USER,
            "role": "master",
            "scopes": ["admin"],
        },
        "action": {
            "plugin_id": "fake_plugin",
            "tags": ["read"],
            "tool_name": "list_records",
            "path": "direct_tool",
        },
        "expected": "allow",
    },
    {
        "id": "C12",
        "description": "key plugin:X no terminal:use → sandbox exec deny",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": ["plugin:fake_plugin"],
        },
        "action": {
            "plugin_id": "cat_terminal_relay_plugin",
            "tags": ["terminal"],
            "tool_name": "exec_command",
            "path": "direct_tool",
        },
        "expected": "deny",
    },
]

# C13-C20 — level-1/level-3 grammar + plugin-gate matrix. These rows need
# fields the loose action kwargs don't cover (explicit ``required``, a
# ``request_extra`` dict merged into the built ``AccessRequest``, and an
# optional ``gate`` spec registered before evaluation) — see
# test/unit/test_scope_contracts.py's extended runner for how each key is
# consumed. Kept in a second list rather than appended to
# ACCESS_PATH_CONTRACTS so the original C01-C12 matrix (and any external
# code importing it) stays byte-identical.
GATE_AND_GRAMMAR_CONTRACTS: list[dict] = [
    {
        "id": "C13",
        "description": "ask key + inherited core:graph:read → run_graph allow",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": ["group:portfolio_plugin:ask", "core:graph:read"],
        },
        "action": {"plugin_id": "portfolio_plugin", "tags": ["ask"]},
        "request_extra": {"operation_id": "run_graph", "core_domain": "graph"},
        "required": {"core:graph:read"},
        "gate": {"plugin_id": "portfolio_plugin", "spec": {"core": ["core:graph:read"]}},
        "expected": "allow",
    },
    {
        "id": "C14",
        "description": "core:config:read → config write deny",
        "principal": {"kind": PrincipalKind.API_KEY, "role": None, "scopes": ["core:config:read"]},
        "action": {"plugin_id": ""},
        "required": {"core:config:write"},
        "expected": "deny",
    },
    {
        "id": "C15",
        "description": "core:whiskers.console:read → analytics read deny (no implied sibling)",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": ["core:whiskers.console:read"],
        },
        "action": {"plugin_id": ""},
        "required": {"core:whiskers.analytics:read"},
        "expected": "deny",
    },
    {
        "id": "C16",
        "description": "core:whiskers:write → core:whiskers.proxy:write allow (domain closure)",
        "principal": {"kind": PrincipalKind.API_KEY, "role": None, "scopes": ["core:whiskers:write"]},
        "action": {"plugin_id": ""},
        "required": {"core:whiskers.proxy:write"},
        "expected": "allow",
    },
    {
        "id": "C17",
        "description": "plugin:X:read → write-tagged op deny",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": ["plugin:fake_plugin:read"],
        },
        "action": {"plugin_id": "fake_plugin"},
        "required": {"plugin:fake_plugin:write"},
        "expected": "deny",
    },
    {
        "id": "C18",
        "description": "op:X:foo → foo allow",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": ["op:jules_plugin:foo"],
        },
        "action": {"plugin_id": "jules_plugin"},
        "required": {"op:jules_plugin:foo"},
        "expected": "allow",
    },
    {
        "id": "C19",
        "description": "gated plugin, scoped principal, op outside gate → deny",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": ["plugin:jules_plugin"],
        },
        "action": {"plugin_id": "jules_plugin"},
        "request_extra": {"operation_id": "delete_source"},
        "required": {"plugin:jules_plugin"},
        "gate": {
            "plugin_id": "jules_plugin",
            "spec": {"operations": {"allow": ["*"], "deny": ["delete_source"]}},
        },
        "expected": "deny",
    },
    {
        "id": "C20",
        "description": "DB override widens the manifest gate → allow (full-replace)",
        "principal": {
            "kind": PrincipalKind.API_KEY,
            "role": None,
            "scopes": ["plugin:jules_plugin", "core:graph:read"],
        },
        "action": {"plugin_id": "jules_plugin"},
        "request_extra": {"core_domain": "graph"},
        "required": {"core:graph:read"},
        "gate": {
            "plugin_id": "jules_plugin",
            "spec": {"core": []},
            "db_override_spec": {"core": ["core:graph:read"]},
        },
        "expected": "allow",
    },
]
