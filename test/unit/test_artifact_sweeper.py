"""artifact_sweeper: retention sweep of artifact_links rows + their MinIO objects."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core_graph.worker import artifact_sweeper as sweeper


class _SelectResult:
    """Stands in for the Result of the id/bucket/object_key select."""

    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _DeleteResult:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount


class _FakeSession:
    """Scripts the select result, then records the bulk delete statement."""

    def __init__(self, rows=()):
        self.executed = []
        self.commits = 0
        self._rows = list(rows)
        self._i = 0

    async def execute(self, stmt):
        self.executed.append(stmt)
        self._i += 1
        # First execute is the select; anything after it is the bulk delete.
        if self._i == 1:
            return _SelectResult(self._rows)
        return _DeleteResult(len(self._rows))

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _S3Error(Exception):
    """Minimal stand-in for minio.error.S3Error (only .code is read)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@pytest.mark.asyncio
async def test_tick_deletes_rows_and_objects():
    fake = _FakeSession([(1, "whiskers-artifacts", "1/s/a.md"), (2, "whiskers-artifacts", "1/s/b.md")])
    removed = []
    with patch("core_graph.worker.artifact_sweeper.get_async_session", return_value=fake), \
         patch("core_graph.worker.artifact_sweeper.minio_available", return_value=True), \
         patch("core_graph.worker.artifact_sweeper.remove_object",
               side_effect=lambda b, k: removed.append((b, k))):
        result = await sweeper._tick()

    assert result is False  # partial batch: idle a full interval
    assert removed == [("whiskers-artifacts", "1/s/a.md"), ("whiskers-artifacts", "1/s/b.md")]
    assert len(fake.executed) == 2  # select + delete
    assert fake.commits == 1


@pytest.mark.asyncio
async def test_tick_noop_when_nothing_stale():
    fake = _FakeSession([])
    with patch("core_graph.worker.artifact_sweeper.get_async_session", return_value=fake), \
         patch("core_graph.worker.artifact_sweeper.minio_available", return_value=True), \
         patch("core_graph.worker.artifact_sweeper.remove_object") as rm:
        result = await sweeper._tick()

    assert result is False
    assert len(fake.executed) == 1  # select only — never a blind delete
    assert fake.commits == 0
    rm.assert_not_called()


@pytest.mark.asyncio
async def test_tick_returns_true_on_full_batch():
    rows = [(i, "whiskers-artifacts", f"1/s/{i}.md") for i in range(sweeper._BATCH)]
    fake = _FakeSession(rows)
    with patch("core_graph.worker.artifact_sweeper.get_async_session", return_value=fake), \
         patch("core_graph.worker.artifact_sweeper.minio_available", return_value=True), \
         patch("core_graph.worker.artifact_sweeper.remove_object"):
        result = await sweeper._tick()

    assert result is True  # loop immediately so a backlog drains


@pytest.mark.asyncio
async def test_minio_unavailable_skips_the_whole_tick():
    """A MinIO outage must never delete rows whose objects still exist."""
    fake = _FakeSession([(1, "whiskers-artifacts", "1/s/a.md")])
    with patch("core_graph.worker.artifact_sweeper.get_async_session", return_value=fake), \
         patch("core_graph.worker.artifact_sweeper.minio_available", return_value=False), \
         patch("core_graph.worker.artifact_sweeper.remove_object") as rm:
        result = await sweeper._tick()

    assert result is False
    assert fake.executed == []
    rm.assert_not_called()


@pytest.mark.asyncio
async def test_hard_object_failure_keeps_the_row():
    """The link must never outlive its object — retry on the next tick instead."""
    fake = _FakeSession([(1, "whiskers-artifacts", "1/s/a.md")])
    with patch("core_graph.worker.artifact_sweeper.get_async_session", return_value=fake), \
         patch("core_graph.worker.artifact_sweeper.minio_available", return_value=True), \
         patch("core_graph.worker.artifact_sweeper.remove_object",
               side_effect=OSError("connection reset")):
        result = await sweeper._tick()

    assert result is False
    assert len(fake.executed) == 1  # select only, no delete issued
    assert fake.commits == 0


@pytest.mark.asyncio
async def test_absent_object_still_deletes_the_row():
    fake = _FakeSession([(1, "whiskers-artifacts", "1/s/gone.md")])
    with patch("core_graph.worker.artifact_sweeper.get_async_session", return_value=fake), \
         patch("core_graph.worker.artifact_sweeper.minio_available", return_value=True), \
         patch("core_graph.worker.artifact_sweeper.remove_object",
               side_effect=_S3Error("NoSuchKey")):
        result = await sweeper._tick()

    assert result is False
    assert len(fake.executed) == 2  # the row is still reaped
    assert fake.commits == 1


@pytest.mark.asyncio
async def test_mixed_batch_deletes_only_the_removable_rows():
    rows = [(1, "whiskers-artifacts", "ok.md"), (2, "whiskers-artifacts", "bad.md")]
    fake = _FakeSession(rows)

    def _remove(bucket, object_key):
        if object_key == "bad.md":
            raise OSError("boom")

    with patch("core_graph.worker.artifact_sweeper.get_async_session", return_value=fake), \
         patch("core_graph.worker.artifact_sweeper.minio_available", return_value=True), \
         patch("core_graph.worker.artifact_sweeper.remove_object", side_effect=_remove):
        result = await sweeper._tick()

    assert result is False
    assert fake.commits == 1
    assert "artifact_links" in str(fake.executed[1])


@pytest.mark.asyncio
async def test_select_filters_on_the_bucket_allowlist():
    """Portfolio assets / resume PDFs live in other buckets and must not be selected."""
    fake = _FakeSession([])
    with patch("core_graph.worker.artifact_sweeper.get_async_session", return_value=fake), \
         patch("core_graph.worker.artifact_sweeper.minio_available", return_value=True), \
         patch("core_graph.worker.artifact_sweeper.remove_object"):
        await sweeper._tick()

    select_sql = str(fake.executed[0])
    assert "artifact_links.bucket IN" in select_sql
    assert "artifact_links.created_at <" in select_sql


@pytest.mark.asyncio
async def test_empty_bucket_allowlist_sweeps_nothing():
    fake = _FakeSession([(1, "whiskers-artifacts", "a.md")])
    with patch("core_graph.worker.artifact_sweeper.get_async_session", return_value=fake), \
         patch("core_graph.worker.artifact_sweeper.minio_available", return_value=True), \
         patch("core_graph.worker.artifact_sweeper._buckets", return_value=[]), \
         patch("core_graph.worker.artifact_sweeper.remove_object") as rm:
        result = await sweeper._tick()

    assert result is False
    assert fake.executed == []
    rm.assert_not_called()


def test_config_readers_use_defaults_when_unset():
    with patch.dict("utils.server_config.SCHEDULED_JOBS_CONFIG", {}, clear=True):
        assert sweeper._interval_seconds() == sweeper._DEFAULT_INTERVAL_SECONDS
        assert sweeper._retention_hours() == sweeper._DEFAULT_RETENTION_HOURS
        assert sweeper._enabled() is True
        from core.artifact_store.store import ARTIFACT_BUCKET
        assert sweeper._buckets() == [ARTIFACT_BUCKET]


def test_config_readers_honor_overrides():
    override = {
        "artifact_sweep": {
            "enabled": False,
            "interval_seconds": 60,
            "retention_hours": 1,
            "buckets": ["whiskers-artifacts", "other-bucket"],
        }
    }
    with patch.dict("utils.server_config.SCHEDULED_JOBS_CONFIG", override, clear=True):
        assert sweeper._interval_seconds() == 60.0
        assert sweeper._retention_hours() == 1.0
        assert sweeper._enabled() is False
        assert sweeper._buckets() == ["whiskers-artifacts", "other-bucket"]


def test_register_adds_the_worker_to_a_registry():
    from core_graph.worker.worker_registry import WorkerRegistry

    registry = WorkerRegistry()
    sweeper.register(registry)
    assert registry.is_registered("artifact_sweeper")
