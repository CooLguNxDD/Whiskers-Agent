"""Unit tests for WorkerRegistry's public query API and idempotent registration.

These exist so plugins stop reaching into `_workers` / `_running_workers` to
hand-roll dedupe scans (world_semantic did this in two places).
"""

import asyncio

import pytest

from core_graph.worker.worker_registry import WorkerRegistry, WorkerSpec


def _spec(name: str, *, enabled: bool = True) -> WorkerSpec:
    """Build a throwaway spec whose run() returns a real Task."""
    async def _noop() -> None:
        await asyncio.sleep(0)

    async def _stop() -> None:
        return None

    return WorkerSpec(
        name=name,
        run=lambda: asyncio.get_event_loop().create_task(_noop()),
        stop=_stop,
        enabled_check=(lambda: enabled),
    )


def test_get_and_is_registered():
    reg = WorkerRegistry()
    assert reg.get("w") is None
    assert reg.is_registered("w") is False
    spec = _spec("w")
    reg.register(spec)
    assert reg.get("w") is spec
    assert reg.is_registered("w") is True
    assert reg.names() == ["w"]


def test_register_duplicate_is_ignored():
    """A second register() under the same name must not shadow the first."""
    reg = WorkerRegistry()
    first, second = _spec("w"), _spec("w")
    reg.register(first)
    reg.register(second)
    assert reg.names() == ["w"]
    assert reg.get("w") is first


def test_register_replace_wins():
    """Hot-reload path: replace=True swaps the spec, still one entry."""
    reg = WorkerRegistry()
    first, second = _spec("w"), _spec("w")
    reg.register(first)
    reg.register(second, replace=True)
    assert reg.names() == ["w"]
    assert reg.get("w") is second


def test_ensure_registered_reports_novelty():
    reg = WorkerRegistry()
    assert reg.ensure_registered(_spec("w")) is True
    assert reg.ensure_registered(_spec("w")) is False
    assert reg.names() == ["w"]


def test_register_rejects_non_spec():
    reg = WorkerRegistry()
    with pytest.raises(TypeError):
        reg.register(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_running_state_tracking():
    reg = WorkerRegistry()
    reg.register(_spec("w"))
    assert reg.is_running("w") is False
    assert reg.running_names() == []

    reg.start("w")
    assert reg.is_running("w") is True
    assert reg.running_names() == ["w"]

    await reg.stop("w")
    assert reg.is_running("w") is False
    assert reg.running_names() == []


@pytest.mark.asyncio
async def test_is_running_is_sticky_after_task_completes():
    """Deliberate: is_running tracks registry bookkeeping, not task liveness."""
    reg = WorkerRegistry()
    reg.register(_spec("w"))
    task = reg.start("w")
    await task
    assert task.done()
    assert reg.is_running("w") is True
    await reg.stop("w")


@pytest.mark.asyncio
async def test_disabled_worker_does_not_start():
    reg = WorkerRegistry()
    reg.register(_spec("w", enabled=False))
    assert reg.start("w") is None
    assert reg.is_running("w") is False


@pytest.mark.asyncio
async def test_stop_all_unwinds_in_reverse_start_order():
    stopped: list[str] = []
    reg = WorkerRegistry()

    def _mk(name: str) -> WorkerSpec:
        async def _stop() -> None:
            stopped.append(name)

        async def _noop() -> None:
            await asyncio.sleep(0)

        return WorkerSpec(
            name=name,
            run=lambda: asyncio.get_event_loop().create_task(_noop()),
            stop=_stop,
        )

    for n in ("a", "b", "c"):
        reg.register(_mk(n))
    reg.start("b")
    reg.start("a")
    reg.start("c")
    await reg.stop_all()
    assert stopped == ["c", "a", "b"]
    assert reg.running_names() == []


def test_register_all_is_core_only():
    """core must not import a plugin to find a worker.

    portfolio_discovery_worker used to be registered here via a
    `from plugins.portfolio_plugin...` import inside register_all(); the plugin
    now self-registers in its own on_ready hook.
    """
    from core_graph.worker import register_all

    reg = WorkerRegistry()
    register_all(reg)
    names = reg.names()
    assert "embedding_worker" in names
    assert "content_sync_worker" in names
    assert "portfolio_discovery_worker" not in names
    assert not any(n.startswith("world_") for n in names)
