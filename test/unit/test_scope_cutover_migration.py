"""Unit tests for the pure token-mapping logic in core_047_scope_cutover.

DB-side execution is exercised by scripts/run_tests.py's own alembic
upgrade-to-head step (every test run applies this migration against a real
Postgres). This file isolates just the mapping functions — no DB needed —
and cross-checks them against core.scope_management.legacy_map, which the
migration intentionally duplicates rather than imports (existing core_0NN
migrations never import from core/, to stay decoupled from app import-time
side effects).
"""

import importlib.util
from pathlib import Path

import pytest

from core.scope_management.legacy_map import LEGACY_SCOPE_MAP as APP_LEGACY_SCOPE_MAP

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations" / "versions" / "core" / "core_047_scope_cutover.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("core_047_scope_cutover", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration_module()


def test_migration_map_matches_app_legacy_map(migration):
    """core_047's frozen, historical map is a subset of the app's live map.

    Not byte-identical: a token discovered after core_047 was authored
    (e.g. ``sandbox:exec``, added in ``core_048_sandbox_scope``) gets its
    own follow-on migration with its own frozen map rather than being
    folded back into core_047 — see ``test_core_048_sandbox_scope.py``.
    Every entry core_047 *does* carry must still agree with the app map.
    """
    app_map = dict(APP_LEGACY_SCOPE_MAP)
    for token, mapped in migration.LEGACY_SCOPE_MAP.items():
        assert app_map.get(token) == mapped, f"{token!r} diverged between core_047 and the app map"


def test_map_list_rewrites_legacy_tokens(migration):
    assert migration._map_list(["terminal:use", "terminal:host", "whiskers"]) == [
        "core:terminal:write",
        "core:terminal:read",
        "core:graph:write",
    ]


def test_map_list_passes_through_sentinels_and_grammar_tokens(migration):
    assert migration._map_list(["admin", "all", "*", "plugin:p", "group:p:t", "op:p:o", "core:graph:read"]) == [
        "admin", "all", "*", "plugin:p", "group:p:t", "op:p:o", "core:graph:read",
    ]


def test_map_list_drops_unmapped_and_dedupes(migration):
    assert migration._map_list(["bogus_legacy_token", "whiskers", "whiskers"]) == ["core:graph:write"]


def test_map_list_empty_result_stays_empty_list(migration):
    assert migration._map_list(["bogus_legacy_token"]) == []


def test_reverse_map_list_inverts(migration):
    assert migration._reverse_map_list(["core:terminal:write", "core:terminal:read", "core:graph:write"]) == [
        "terminal:use", "terminal:host", "whiskers",
    ]


def test_reverse_map_list_passes_through_unmapped(migration):
    assert migration._reverse_map_list(["admin", "core:config:read"]) == ["admin", "core:config:read"]


def test_round_trip_for_mapped_tokens(migration):
    original = ["terminal:use", "terminal:host", "whiskers", "admin", "plugin:p"]
    forward = migration._map_list(original)
    back = migration._reverse_map_list(forward)
    assert back == original
