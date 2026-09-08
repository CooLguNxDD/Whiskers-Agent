"""Unit tests for PluginSchemaMigrator (discover, ledger, gate, auto_migrate)."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from db_layer.plugin_schema_migrator import (
    MigrationStep,
    PluginMigrationError,
    PluginSchemaMigrator,
    reset_core_ancestor_cache,
    _file_checksum,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_core_ancestor_cache()
    yield
    reset_core_ancestor_cache()


@pytest.fixture
def migrator() -> PluginSchemaMigrator:
    return PluginSchemaMigrator()


def _write_steps(tmp: Path, names: list[str], bodies: dict[str, str] | None = None) -> Path:
    mig = tmp / "migrations"
    mig.mkdir(parents=True, exist_ok=True)
    bodies = bodies or {}
    for name in names:
        content = bodies.get(name, f"-- {name}\nSELECT 1;\n" if name.endswith(".sql") else "")
        if name.endswith(".py") and name not in bodies:
            content = (
                "def upgrade(conn):\n"
                "    from sqlalchemy import text\n"
                "    conn.execute(text('SELECT 1'))\n"
            )
        (mig / name).write_text(content, encoding="utf-8")
    return mig


# ── 1. discover_steps ────────────────────────────────────────────────────────


def test_discover_steps_ordering_and_regex(tmp_path, migrator):
    mig = _write_steps(
        tmp_path,
        [
            "0002_b.sql",
            "0001_a.sql",
            "README.md",
            "evil.sql",
            "0003_c.py",
            "../not_here.sql",
        ],
    )
    # Also drop a path-looking filename that should fail regex
    (mig / "0001_bad-name.sql").write_text("SELECT 1;", encoding="utf-8")

    steps = migrator.discover_steps(mig)
    assert [s.revision for s in steps] == ["0001_a", "0002_b", "0003_c"]
    assert steps[0].kind == "sql"
    assert steps[2].kind == "py"
    assert all(len(s.checksum) == 64 for s in steps)


def test_discover_steps_empty_or_missing(tmp_path, migrator):
    assert migrator.discover_steps(tmp_path / "nope") == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert migrator.discover_steps(empty) == []


def test_discover_steps_sql_py_mix(tmp_path, migrator):
    mig = _write_steps(tmp_path, ["0001_init.sql", "0002_alter.py"])
    steps = migrator.discover_steps(mig)
    assert [s.kind for s in steps] == ["sql", "py"]


# ── 2. Ledger diff ───────────────────────────────────────────────────────────


def test_ledger_diff_fresh_applies_all(tmp_path, migrator):
    mig = _write_steps(tmp_path, ["0001_a.sql", "0002_b.sql"])
    steps = migrator.discover_steps(mig)

    applied: dict = {}
    engine, lock_conn = _mock_engine_pair()

    def begin_cm():
        cm = MagicMock()
        conn = MagicMock()
        cm.__enter__ = MagicMock(return_value=conn)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    engine.begin.side_effect = lambda: begin_cm()

    with patch(
        "db_layer.connection.create_db_engine", return_value=engine
    ), patch.object(migrator, "_read_ledger", return_value=applied), patch.object(
        migrator, "_apply_step"
    ) as apply:
        n = migrator._migrate_sync("demo", "1.0.0", mig, None, True)

    assert n == 2
    assert apply.call_count == 2
    assert [c.args[2].revision for c in apply.call_args_list] == [
        "0001_a",
        "0002_b",
    ]


def test_ledger_diff_partial_applies_tail(tmp_path, migrator):
    mig = _write_steps(tmp_path, ["0001_a.sql", "0002_b.sql", "0003_c.sql"])
    steps = migrator.discover_steps(mig)
    applied = {
        "0001_a": {"checksum": steps[0].checksum},
    }
    engine, _ = _mock_engine_pair()
    engine.begin.side_effect = lambda: _begin_cm()

    with patch(
        "db_layer.connection.create_db_engine", return_value=engine
    ), patch.object(migrator, "_read_ledger", return_value=applied), patch.object(
        migrator, "_apply_step"
    ) as apply:
        n = migrator._migrate_sync("demo", "1.0.0", mig, None, True)

    assert n == 2
    assert [c.args[2].revision for c in apply.call_args_list] == [
        "0002_b",
        "0003_c",
    ]


def test_checksum_mismatch_warns_no_rerun(tmp_path, migrator, caplog):
    mig = _write_steps(tmp_path, ["0001_a.sql"])
    steps = migrator.discover_steps(mig)
    applied = {"0001_a": {"checksum": "0" * 64}}
    engine, _ = _mock_engine_pair()

    with caplog.at_level(logging.WARNING), patch(
        "db_layer.connection.create_db_engine", return_value=engine
    ), patch.object(migrator, "_read_ledger", return_value=applied), patch.object(
        migrator, "_apply_step"
    ) as apply:
        n = migrator._migrate_sync("demo", "1.0.0", mig, None, True)

    assert n == 0
    apply.assert_not_called()
    assert any("checksum mismatch" in r.message for r in caplog.records)


# ── 3. Per-step transaction ──────────────────────────────────────────────────


def test_per_step_txn_failure_stops(tmp_path, migrator):
    mig = _write_steps(tmp_path, ["0001_a.sql", "0002_b.sql", "0003_c.sql"])
    engine, _ = _mock_engine_pair()
    engine.begin.side_effect = lambda: _begin_cm()
    calls: list[str] = []

    def apply(conn, plugin_id, step):
        calls.append(step.revision)
        if step.revision == "0002_b":
            raise RuntimeError("boom")

    with patch(
        "db_layer.connection.create_db_engine", return_value=engine
    ), patch.object(migrator, "_read_ledger", return_value={}), patch.object(
        migrator, "_apply_step", side_effect=apply
    ):
        with pytest.raises(PluginMigrationError, match="0002_b"):
            migrator._migrate_sync("demo", "1.0.0", mig, None, True)

    assert calls == ["0001_a", "0002_b"]


# ── 5. min_core_revision gate ────────────────────────────────────────────────


def test_gate_satisfied(tmp_path, migrator):
    mig = _write_steps(tmp_path, ["0001_a.sql"])
    engine, lock_conn = _mock_engine_pair()
    engine.begin.side_effect = lambda: _begin_cm()

    with patch(
        "db_layer.connection.create_db_engine", return_value=engine
    ), patch.object(
        migrator, "_get_core_ancestors", return_value={"core_039", "core_040"}
    ), patch.object(migrator, "_read_ledger", return_value={}), patch.object(
        migrator, "_apply_step"
    ):
        n = migrator._migrate_sync("demo", "1.0.0", mig, "core_040", True)
    assert n == 1


def test_gate_unsatisfied(tmp_path, migrator):
    mig = _write_steps(tmp_path, ["0001_a.sql"])
    engine, _ = _mock_engine_pair()

    with patch(
        "db_layer.connection.create_db_engine", return_value=engine
    ), patch.object(
        migrator, "_get_core_ancestors", return_value={"core_039"}
    ), patch.object(migrator, "_read_ledger", return_value={}):
        with pytest.raises(PluginMigrationError, match="min_core_revision"):
            migrator._migrate_sync("demo", "1.0.0", mig, "core_040", True)


def test_gate_weird_revision_names_use_set_membership(tmp_path, migrator):
    """Lexicographic comparison would mishandle core_031b / core_goal_001."""
    mig = _write_steps(tmp_path, ["0001_a.sql"])
    engine, _ = _mock_engine_pair()
    engine.begin.side_effect = lambda: _begin_cm()
    ancestors = {"core_031b", "core_goal_001", "core_chat_001", "core_040"}

    with patch(
        "db_layer.connection.create_db_engine", return_value=engine
    ), patch.object(
        migrator, "_get_core_ancestors", return_value=ancestors
    ), patch.object(migrator, "_read_ledger", return_value={}), patch.object(
        migrator, "_apply_step"
    ):
        n = migrator._migrate_sync("demo", "1.0.0", mig, "core_031b", True)
    assert n == 1


def test_gate_missing_alembic_ini_fails_closed(tmp_path, migrator):
    mig = _write_steps(tmp_path, ["0001_a.sql"])
    engine, lock_conn = _mock_engine_pair()
    # Force real _get_core_ancestors path with empty heads → fail closed
    lock_conn.execute.return_value.fetchall.return_value = []

    with patch("db_layer.connection.create_db_engine", return_value=engine):
        with pytest.raises(PluginMigrationError):
            migrator._migrate_sync("demo", "1.0.0", mig, "core_040", True)


def test_gate_failure_cache_expires_after_ttl(tmp_path, migrator):
    """A transient gate failure must not poison the gate forever (TTL, not sticky)."""
    import db_layer.plugin_schema_migrator as mod

    mig = _write_steps(tmp_path, ["0001_a.sql"])
    engine, lock_conn = _mock_engine_pair()
    engine.begin.side_effect = lambda: _begin_cm()
    lock_conn.execute.return_value.fetchall.return_value = []

    with patch("db_layer.connection.create_db_engine", return_value=engine):
        with pytest.raises(PluginMigrationError):
            migrator._migrate_sync("demo", "1.0.0", mig, "core_040", True)
    assert mod._CORE_ANCESTORS_FAILED is True

    # Still within TTL: cached failure short-circuits without recomputation.
    with patch("db_layer.connection.create_db_engine", return_value=engine):
        with pytest.raises(PluginMigrationError, match="previous load failure"):
            migrator._migrate_sync("demo", "1.0.0", mig, "core_040", True)

    # TTL elapsed: next call retries instead of staying poisoned.
    mod._CORE_ANCESTORS_FAILED_AT -= mod._CORE_ANCESTORS_FAILURE_TTL_S + 1
    with patch(
        "db_layer.connection.create_db_engine", return_value=engine
    ), patch.object(
        migrator, "_get_core_ancestors", return_value={"core_039", "core_040"}
    ), patch.object(migrator, "_read_ledger", return_value={}), patch.object(
        migrator, "_apply_step"
    ):
        n = migrator._migrate_sync("demo", "1.0.0", mig, "core_040", True)
    assert n == 1


# ── 6. auto_migrate:false ────────────────────────────────────────────────────


def test_auto_migrate_false_with_pending_raises(tmp_path, migrator):
    mig = _write_steps(tmp_path, ["0001_a.sql"])
    engine, _ = _mock_engine_pair()

    with patch(
        "db_layer.connection.create_db_engine", return_value=engine
    ), patch.object(migrator, "_read_ledger", return_value={}):
        with pytest.raises(PluginMigrationError, match="auto_migrate is false"):
            migrator._migrate_sync("demo", "1.0.0", mig, None, False)


# ── helpers ──────────────────────────────────────────────────────────────────


def _mock_engine_pair(*, lock_acquired: bool = True):
    engine = MagicMock()
    lock_conn = MagicMock()
    result = MagicMock()
    result.scalar.return_value = lock_acquired
    lock_conn.execute.return_value = result
    engine.connect.return_value = lock_conn
    return engine, lock_conn


def _begin_cm():
    cm = MagicMock()
    conn = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def test_file_checksum_stable(tmp_path):
    p = tmp_path / "f.sql"
    p.write_text("SELECT 1;", encoding="utf-8")
    assert _file_checksum(p) == _file_checksum(p)


def test_file_checksum_chunked_matches_full_read(tmp_path):
    import hashlib

    p = tmp_path / "big.sql"
    body = b"SELECT 1;\n" * 5000
    p.write_bytes(body)
    assert _file_checksum(p) == hashlib.sha256(body).hexdigest()


def test_lock_timeout_raises(tmp_path, migrator, monkeypatch):
    from db_layer import plugin_schema_migrator as mod

    monkeypatch.setenv("PLUGIN_SCHEMA_LOCK_TIMEOUT_S", "0.15")
    monkeypatch.setenv("PLUGIN_SCHEMA_LOCK_POLL_S", "0.05")
    mig = _write_steps(tmp_path, ["0001_a.sql"])
    engine, _ = _mock_engine_pair(lock_acquired=False)

    with patch("db_layer.connection.create_db_engine", return_value=engine):
        with pytest.raises(PluginMigrationError, match="lock timeout"):
            migrator._migrate_sync("demo", "1.0.0", mig, None, True)


def test_oversized_migration_rejected_at_apply(tmp_path, migrator, monkeypatch):
    from db_layer import plugin_schema_migrator as mod

    monkeypatch.setattr(mod, "MAX_MIGRATION_FILE_BYTES", 64)
    mig = tmp_path / "migrations"
    mig.mkdir()
    big = mig / "0001_a.sql"
    big.write_text("SELECT 1;\n" + ("x" * 200), encoding="utf-8")

    # Discover still lists the step (size gated at apply, fail-closed).
    steps = migrator.discover_steps(mig)
    assert [s.revision for s in steps] == ["0001_a"]

    engine, _ = _mock_engine_pair()
    with patch(
        "db_layer.connection.create_db_engine", return_value=engine
    ), patch.object(migrator, "_read_ledger", return_value={}):
        with pytest.raises(PluginMigrationError, match="bytes"):
            migrator._migrate_sync("demo", "1.0.0", mig, None, True)


def test_py_migrations_disabled(tmp_path, migrator, monkeypatch):
    monkeypatch.setenv("PLUGIN_SCHEMA_ALLOW_PY_MIGRATIONS", "0")
    mig = _write_steps(tmp_path, ["0001_init.py", "0002_b.sql"])
    steps = migrator.discover_steps(mig)
    assert [s.revision for s in steps] == ["0002_b"]
    assert all(s.kind == "sql" for s in steps)
