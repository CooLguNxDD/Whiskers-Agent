"""Database-free regression tests for core_052's guarded, rename-only repair."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

_ROOT = Path(__file__).resolve().parents[2]
_OLD = "embedding_jobs_plugin_id_operation_id_content_hash_key"
_NEW = "embedding_jobs_dedup"


@pytest.fixture
def migration(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "core_052_embedding_jobs_dedup_name",
        _ROOT / "migrations/versions/core/core_052_embedding_jobs_dedup_name.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "op", Mock())
    return module


@pytest.mark.parametrize("direction,source,target", [
    ("upgrade", _OLD, _NEW),
    ("downgrade", _NEW, _OLD),
])
@pytest.mark.parametrize("source_exists,target_exists", [
    (True, False), (False, True), (True, True), (False, False),
])
def test_rename_and_noop_guards(migration, direction, source, target,
                              source_exists, target_exists):
    names = ["embedding_jobs_pkey", "unrelated_constraint"]
    if source_exists:
        names.append(source)
    if target_exists:
        names.append(target)
    conn = migration.op.get_bind.return_value
    conn.execute.return_value.scalars.return_value = names

    getattr(migration, direction)()

    # Catalog lookup must not mistake a constraint on another table for ours.
    conn.execute.assert_called_once()
    assert str(conn.execute.call_args.args[0]) == (
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'embedding_jobs'::regclass"
    )
    if source_exists and not target_exists:
        migration.op.execute.assert_called_once()
        assert str(migration.op.execute.call_args.args[0]) == (
            f"ALTER TABLE embedding_jobs RENAME CONSTRAINT {source} TO {target}"
        )
    else:
        migration.op.execute.assert_not_called()


def test_upgrade_retry_and_downgrade_retry_are_noops(migration):
    conn = migration.op.get_bind.return_value
    conn.execute.return_value.scalars.side_effect = [
        [_OLD], [_NEW], [_NEW], [_OLD],
    ]

    migration.upgrade()
    migration.upgrade()
    migration.downgrade()
    migration.downgrade()

    assert [str(call.args[0]) for call in migration.op.execute.call_args_list] == [
        f"ALTER TABLE embedding_jobs RENAME CONSTRAINT {_OLD} TO {_NEW}",
        f"ALTER TABLE embedding_jobs RENAME CONSTRAINT {_NEW} TO {_OLD}",
    ]


def test_revision_extends_core_without_merging_legacy_head(migration):
    scripts = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    assert set(scripts.get_heads()) == {"core_052", "568ee6fe3f3f"}
    assert scripts.get_revision("core_052").down_revision == "core_051"
    assert migration.branch_labels is None
    assert migration.depends_on is None
