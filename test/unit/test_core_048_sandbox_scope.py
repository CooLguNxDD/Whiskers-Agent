"""Unit tests for the pure token-mapping logic in core_048_sandbox_scope.

Mirrors test_scope_cutover_migration.py's pattern for core_047: no DB
needed, cross-checks the migration's inlined sandbox:exec mapping against
core.scope_management.legacy_map (duplicated intentionally, not imported —
migrations never import from core/).
"""

import importlib.util
from pathlib import Path

import pytest

from core.scope_management.legacy_map import LEGACY_SCOPE_MAP as APP_LEGACY_SCOPE_MAP

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations" / "versions" / "core" / "core_048_sandbox_scope.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("core_048_sandbox_scope", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration_module()


def test_sandbox_token_matches_app_legacy_map(migration):
    assert APP_LEGACY_SCOPE_MAP.get(migration._LEGACY_TOKEN) == migration._NEW_TOKEN


def test_map_list_rewrites_sandbox_token(migration):
    assert migration._map_list(["sandbox:exec"]) == ["core:terminal.sandbox:write"]


def test_map_list_passes_through_everything_else(migration):
    assert migration._map_list(["admin", "core:graph:read", "plugin:p"]) == [
        "admin", "core:graph:read", "plugin:p",
    ]


def test_map_list_dedupes(migration):
    assert migration._map_list(["sandbox:exec", "core:terminal.sandbox:write"]) == [
        "core:terminal.sandbox:write",
    ]


def test_reverse_map_list_inverts(migration):
    assert migration._reverse_map_list(["core:terminal.sandbox:write"]) == ["sandbox:exec"]
