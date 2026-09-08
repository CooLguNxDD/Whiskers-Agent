"""Unit tests for SessionRegistry reaper lifecycle."""

import asyncio
import pytest
from plugins.cat_terminal_relay_plugin.services.session_registry import SessionRegistry


@pytest.mark.asyncio
async def test_start_reaper_idempotent():
    """Verify that calling start_reaper() twice only creates one active task."""
    reg = SessionRegistry()
    assert reg._reaper_task is None

    reg.start_reaper()
    first_task = reg._reaper_task
    assert first_task is not None
    assert not first_task.done()

    # Call again, should not create a new task
    reg.start_reaper()
    second_task = reg._reaper_task
    assert second_task is first_task

    # Clean up
    await reg.stop_reaper()


@pytest.mark.asyncio
async def test_stop_reaper_cancels_and_nils():
    """Verify that stop_reaper() cancels and nils _reaper_task."""
    reg = SessionRegistry()
    reg.start_reaper()
    task = reg._reaper_task
    assert task is not None

    # stop_reaper now awaits the cancelled task so cleanup fully completes.
    await reg.stop_reaper()
    assert reg._reaper_task is None
    assert task.cancelled() or task.done()
