"""Anti-rot guard for HTTP endpoint scope binding (Stage 3 of the scope overhaul).

``api/middleware.py::SessionGateMiddleware`` enforces a route's
``required_scopes`` whenever it is non-empty, independent of the
``AuthPolicy`` gate segment (see ``core.route_registry.http_route_registry``).
This test regex-scans real ``@http_route_registry.route(`` declarations —
mirroring ``test_node_role_bindings.py``'s scan-vs-declared pattern — so a
new SESSION_GATED route under an already-bound owner silently shipping with
no ``required_scopes`` (and no explicit opt-out) becomes a CI failure
instead of an unenforced route nobody notices.

Only ``api.config`` is bound as of this branch (Stage 3's other owner
groups — ``api.apikeys``, ``api.analytics``, ``api.plugins``,
``api.proxies`` — are declared-but-unbound scope tokens with no route
binding yet, deliberately deferred past this branch). ``_BOUND_OWNERS``
below is the single place to extend as more owner groups get bound.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Owner groups whose SESSION_GATED routes must all carry a required_scopes
# binding. Extend this set (and _UNBOUND_EXCEPTIONS if a route is a real,
# reviewed exception) as more owner groups get bound in a future branch.
_BOUND_OWNERS = frozenset({
    "api.config",
    "api.analytics",
    "api.apikeys",
    "api.route",
    "api.proxy",
    "api.admin",
    "api.plugins",
})

# (owner, route_name) pairs allowed to stay unbound within a bound owner —
# e.g. a resource that is declared under the owner for mounting purposes but
# is not actually config-domain-scoped.
_UNBOUND_EXCEPTIONS = frozenset({
    ("api.config", "api_list_workflows"),
    ("api.config", "api_get_workflow"),
})

_DECL_RE = re.compile(
    r'@http_route_registry\.route\(\s*(?P<body>.*?)\n\)\n',
    re.DOTALL,
)
_FIELD_RE = re.compile(r'(\w+)\s*=\s*(.+?),?\s*$', re.MULTILINE)


def _scan_route_declarations() -> list[dict]:
    decls: list[dict] = []
    for py_file in (_REPO_ROOT / "api").rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for m in _DECL_RE.finditer(text):
            body = m.group("body")
            fields: dict[str, str] = {}
            depth = 0
            current_key = None
            buf = ""
            # Simple line-based field scan (values here are always single-line
            # literals: strings, AuthPolicy.X, tuples of scope-token strings).
            for line in body.splitlines():
                line = line.strip().rstrip(",")
                fm = re.match(r'^(\w+)\s*=\s*(.*)$', line)
                if fm:
                    fields[fm.group(1)] = fm.group(2)
            fields["_file"] = py_file.name
            decls.append(fields)
    return decls


def test_bound_owner_routes_all_carry_required_scopes():
    decls = _scan_route_declarations()
    assert decls, "scan found zero @http_route_registry.route(...) declarations — regex drifted"

    missing: list[str] = []
    for d in decls:
        owner_raw = d.get("owner", "")
        owner = owner_raw.strip('"')
        if owner not in _BOUND_OWNERS:
            continue
        auth_policy = d.get("auth_policy", "")
        if "SESSION_GATED" not in auth_policy and "SCOPE_REQUIRED" not in auth_policy:
            continue
        name = d.get("name", "").strip('"')
        if (owner, name) in _UNBOUND_EXCEPTIONS:
            continue
        required = d.get("required_scopes", "")
        if not required or required in ("()", "( )"):
            missing.append(f"{d.get('_file')}::{name or '<unnamed>'} (owner={owner})")

    assert missing == [], (
        "SESSION_GATED routes under a bound owner with no required_scopes binding "
        f"(add one, or add to _UNBOUND_EXCEPTIONS with justification): {missing}"
    )


def test_admin_login_api_key_stays_public_and_unbound():
    """admin_login_api_key must stay PUBLIC/unbound — it's the login endpoint itself."""
    decls = _scan_route_declarations()
    login_decls = [d for d in decls if d.get("name", "").strip('"') == "admin_login_api_key"]
    assert len(login_decls) == 1
    d = login_decls[0]
    assert "PUBLIC" in d.get("auth_policy", "")
    assert not d.get("required_scopes")
