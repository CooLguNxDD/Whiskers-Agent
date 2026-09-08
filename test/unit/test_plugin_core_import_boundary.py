"""Assert plugins/ never reach past the IArtifactStore/IAuthService boundary.

Companion to ``test_scope_import_boundary.py``'s "no re-implemented
is_allowed" guardrail. Plugins must go through ``core.artifact_store.get_artifact_store()``
(``ctx.artifact_store``) and ``core.auth_service.get_auth_service()``
(``ctx.auth_service``) for storage and inbound-auth — never the private
internals those adapters wrap.

Scans both module-level *and* function-local imports (``ast.walk``, not just
the module body) since every bypass this test guards against was a
function-local import, not a module-level one — see repo-polish phase 3.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGINS_DIR = REPO / "plugins"

# (module path an import statement names, optional specific name imported)
# banned when the FROM-module is exactly one of these, the plain `import`
# target is exactly one of these, OR a `from <parent> import <name>` resolves
# to one of these dotted paths (e.g. `from core.artifact_store import
# minio_client` == `core.artifact_store.minio_client`). The public
# `core.artifact_store` package façade itself, and its `__init__`'s
# `get_artifact_store`/`DEFAULT_BUCKET`/etc. re-exports, stay allowed —
# only the `minio_client` submodule (and the other listed submodules) is banned.
BANNED_MODULES = {
    "core.artifact_store.minio_client",
    "db_layer.artifact_link_store",
    "db_layer.api_key_store",
    "core.api_key_management.store",
    "core.api_key_management.scopes",
}

# file:module pairs allowed to keep a banned import (e.g. the adapters
# themselves, or a documented one-off). Empty today — nothing in plugins/
# should need one.
ALLOWED: set[tuple[str, str]] = set()


def _oauth_provider_private_attr_access(tree: ast.AST) -> list[str]:
    """Find `oauth_provider._svc` / `<anything>._mint_jwt(...)` attribute reaches."""
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "_svc":
            offenders.append("._svc")
        if isinstance(node, ast.Attribute) and node.attr == "_mint_jwt":
            offenders.append("._mint_jwt")
    return offenders


def _banned_imports(tree: ast.AST) -> list[str]:
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in BANNED_MODULES:
                offenders.append(node.module)
            elif node.module:
                # Catch `from <parent> import <submodule>` — e.g.
                # `from db_layer import artifact_link_store` or
                # `from core.artifact_store import minio_client` — which
                # `node.module` alone (the parent) would miss.
                for alias in node.names:
                    dotted = f"{node.module}.{alias.name}"
                    if dotted in BANNED_MODULES:
                        offenders.append(dotted)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in BANNED_MODULES:
                    offenders.append(alias.name)
    return offenders


def test_plugins_never_import_artifact_or_api_key_internals_directly():
    """No plugins/ file imports the private storage/auth internals, at any nesting."""
    offenders: list[str] = []
    for path in PLUGINS_DIR.rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if "/tests/" in rel or rel.endswith("/tests"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), rel)
        for module in _banned_imports(tree):
            if (rel, module) in ALLOWED:
                continue
            offenders.append(f"{rel}: imports {module}")
    assert offenders == [], (
        "plugins/ must go through core.artifact_store.get_artifact_store() / "
        "core.auth_service.get_auth_service() instead:\n" + "\n".join(offenders)
    )


def test_banned_imports_catches_parent_submodule_form():
    """Guard the guard: `from <parent> import <submodule>` must not slip past `_banned_imports`."""
    src = "from core.artifact_store import minio_client\nfrom db_layer import artifact_link_store\n"
    tree = ast.parse(src, "synthetic")
    offenders = _banned_imports(tree)
    assert "core.artifact_store.minio_client" in offenders
    assert "db_layer.artifact_link_store" in offenders


def test_plugins_never_reach_oauth_service_private_attrs():
    """No plugins/ file touches `.` `_svc` or `._mint_jwt` (private OAuthService surface)."""
    offenders: list[str] = []
    for path in PLUGINS_DIR.rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if "/tests/" in rel or rel.endswith("/tests"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), rel)
        for attr in _oauth_provider_private_attr_access(tree):
            offenders.append(f"{rel}: reaches {attr}")
    assert offenders == [], (
        "plugins/ must go through core.auth_service.get_auth_service() instead "
        "of oauth_provider._svc / OAuthService._mint_jwt:\n" + "\n".join(offenders)
    )
