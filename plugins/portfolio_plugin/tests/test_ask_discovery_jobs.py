"""Ask-mode background discovery jobs: eviction, cancellation, rejoin rules."""

import asyncio

import pytest

from plugins.portfolio_plugin.ask import discovery_jobs as dj


@pytest.fixture(autouse=True)
def _reset():
    dj.reset_jobs()
    yield
    dj.reset_jobs()


def _enable(monkeypatch):
    monkeypatch.setattr(dj, "_ask_settings", lambda: {"discovery_fallback": True})


async def test_evict_cancels_a_still_running_task():
    """A job popped before its task finishes must not orphan the task."""
    started = asyncio.Event()

    async def _never_ends():
        started.set()
        await asyncio.sleep(100)

    job_id = "job-1"
    dj._jobs[job_id] = {
        "status": "pending",
        "query": "q",
        "created_at": 0.0,  # already past _JOB_TTL_S
        "projects": [],
        "error": "",
    }
    task = asyncio.create_task(_never_ends())
    dj._tasks[job_id] = task
    await started.wait()

    dj._evict(dj.time.time())

    assert job_id not in dj._jobs
    assert job_id not in dj._tasks
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_error_job_is_not_rejoined(monkeypatch):
    """A dead job (error/empty) must not pin the query for the rest of its TTL."""
    _enable(monkeypatch)
    q = "some ghost project"
    job_id = "job-err"
    dj._jobs[job_id] = {
        "status": "error",
        "query": q,
        "created_at": dj.time.time(),
        "projects": [],
        "error": "boom",
    }
    dj._by_query[q] = job_id

    out = dj.start_discovery(q)

    assert out["job_id"] != job_id
    assert out["status"] == "pending"
    dj._tasks[out["job_id"]].cancel()


async def test_ready_job_is_rejoined(monkeypatch):
    _enable(monkeypatch)
    q = "helix ai"
    job_id = "job-ready"
    dj._jobs[job_id] = {
        "status": "ready",
        "query": q,
        "created_at": dj.time.time(),
        "projects": [{"slug": "helix-ai"}],
        "error": "",
    }
    dj._by_query[q] = job_id

    out = dj.start_discovery(q)

    assert out["job_id"] == job_id
    assert out["status"] == "ready"


def test_tenant_int_never_raises():
    assert dj._tenant_int("not-a-number") == 1
    assert dj._tenant_int(None) == 1
    assert dj._tenant_int(7) == 7


async def test_await_discovery_logs_task_failure_and_still_returns_record(caplog):
    """A task that raises must be logged, not silently swallowed, and the
    caller still gets back whatever record exists (fail-open contract)."""
    job_id = "job-fail"
    dj._jobs[job_id] = {
        "status": "pending",
        "query": "q",
        "created_at": dj.time.time(),
        "projects": [],
        "error": "",
    }

    async def _boom():
        raise RuntimeError("discovery source exploded")

    task = asyncio.create_task(_boom())
    dj._tasks[job_id] = task

    with caplog.at_level("WARNING", logger="whiskers.plugins.portfolio.ask.discovery"):
        result = await dj.await_discovery(job_id, timeout_s=5.0)

    assert result["status"] == "pending"
    assert any("failed while awaiting" in r.message for r in caplog.records)


async def test_await_discovery_propagates_caller_cancellation():
    """A shielded task that is still running when the *caller's* await gets
    cancelled must re-raise CancelledError, not swallow it as if the task
    itself had been cancelled."""
    started = asyncio.Event()

    async def _slow():
        started.set()
        await asyncio.sleep(100)

    job_id = "job-slow"
    dj._jobs[job_id] = {
        "status": "pending",
        "query": "q",
        "created_at": dj.time.time(),
        "projects": [],
        "error": "",
    }
    task = asyncio.create_task(_slow())
    dj._tasks[job_id] = task
    await started.wait()

    outer = asyncio.create_task(dj.await_discovery(job_id, timeout_s=100.0))
    await asyncio.sleep(0)  # let it start waiting
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
