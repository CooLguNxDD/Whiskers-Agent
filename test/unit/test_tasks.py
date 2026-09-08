import asyncio
import logging

import pytest

from utils.tasks import spawn_supervised


@pytest.mark.asyncio
async def test_spawn_supervised_logs_unretrieved_exception(caplog):
    async def _boom():
        raise ValueError("kaboom")

    with caplog.at_level(logging.ERROR, logger="whiskers"):
        task = spawn_supervised(_boom(), name="test_boom")
        with pytest.raises(ValueError):
            await task

    assert any("test_boom" in r.message and "kaboom" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_spawn_supervised_invokes_on_error():
    seen: list[BaseException] = []

    async def _boom():
        raise RuntimeError("bad")

    task = spawn_supervised(_boom(), name="test_on_error", on_error=seen.append)
    with pytest.raises(RuntimeError):
        await task
    assert len(seen) == 1
    assert isinstance(seen[0], RuntimeError)


@pytest.mark.asyncio
async def test_spawn_supervised_success_no_log(caplog):
    async def _ok():
        return 42

    with caplog.at_level(logging.ERROR, logger="whiskers"):
        task = spawn_supervised(_ok(), name="test_ok")
        result = await task

    assert result == 42
    assert not any("test_ok" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_spawn_supervised_cancellation_not_logged_as_error(caplog):
    async def _sleep_forever():
        await asyncio.sleep(10)

    with caplog.at_level(logging.ERROR, logger="whiskers"):
        task = spawn_supervised(_sleep_forever(), name="test_cancel")
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert not any("test_cancel" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_spawn_supervised_tracks_task_until_done():
    from utils import tasks as tasks_mod

    async def _quick():
        return 1

    task = spawn_supervised(_quick(), name="test_tracking")
    assert task in tasks_mod._live_tasks
    await task
    assert task not in tasks_mod._live_tasks
