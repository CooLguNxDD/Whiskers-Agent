"""Unit tests for db_layer.plugin_gate_store (mirrors test_model_role_store.py style)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.scope_management.gates import GateSpecError

_VALID_SPEC = {"core": ["core:graph:read"]}


class _Row:
    def __init__(self, v):
        self._v = v

    def __getitem__(self, i):
        return self._v if i == 0 else None


def _session_cm(fetchone_result):
    cm = AsyncMock()
    cm.__aenter__.return_value.execute = AsyncMock(
        return_value=type("R", (), {"fetchone": lambda s: fetchone_result})()
    )
    cm.__aenter__.return_value.commit = AsyncMock()
    return cm


@pytest.mark.asyncio
async def test_get_overrides_returns_empty_when_no_db():
    from db_layer import plugin_gate_store as pgs
    with patch.object(pgs, "_db_available", return_value=False):
        val = await pgs.get_plugin_gate_overrides()
        assert val == {}
        assert val is not pgs._EMPTY


@pytest.mark.asyncio
async def test_set_and_get_gate_roundtrip():
    from db_layer import plugin_gate_store as pgs
    with patch.object(pgs, "_db_available", return_value=True):
        store: dict = {}

        async def fake_write(value):
            store["value"] = value

        with patch.object(pgs, "_write_raw", AsyncMock(side_effect=fake_write)):
            with patch.object(pgs, "_read_raw", AsyncMock(return_value={})):
                result = await pgs.set_plugin_gate_override("jules_plugin", _VALID_SPEC)
                assert result["jules_plugin"] == _VALID_SPEC
                assert store["value"]["jules_plugin"] == _VALID_SPEC

            with patch.object(pgs, "_read_raw", AsyncMock(return_value=store["value"])):
                got = await pgs.get_plugin_gate_overrides()
                assert got["jules_plugin"] == _VALID_SPEC


@pytest.mark.asyncio
async def test_invalid_spec_raises_and_persists_nothing():
    from db_layer import plugin_gate_store as pgs
    with patch.object(pgs, "_db_available", return_value=True):
        write_mock = AsyncMock()
        with patch.object(pgs, "_write_raw", write_mock):
            with pytest.raises(GateSpecError):
                await pgs.set_plugin_gate_override("jules_plugin", {"bogus_key": 1})
            write_mock.assert_not_called()


@pytest.mark.asyncio
async def test_empty_plugin_id_rejected():
    from db_layer import plugin_gate_store as pgs
    with patch.object(pgs, "_db_available", return_value=True):
        with pytest.raises(ValueError):
            await pgs.set_plugin_gate_override("", _VALID_SPEC)


@pytest.mark.asyncio
async def test_set_plugin_gate_override_none_deletes():
    from db_layer import plugin_gate_store as pgs
    with patch.object(pgs, "_db_available", return_value=True):
        store = {"jules_plugin": _VALID_SPEC, "portfolio_plugin": {"core": ["core:graph:read"]}}

        async def fake_write(value):
            store.clear()
            store.update(value)

        with patch.object(pgs, "_write_raw", AsyncMock(side_effect=fake_write)):
            with patch.object(pgs, "_read_raw", AsyncMock(return_value=dict(store))):
                result = await pgs.set_plugin_gate_override("jules_plugin", None)
                assert "jules_plugin" not in result
                assert "portfolio_plugin" in result


@pytest.mark.asyncio
async def test_no_db_available_write_functions_do_not_raise():
    from db_layer import plugin_gate_store as pgs
    with patch.object(pgs, "_db_available", return_value=False):
        result = await pgs.set_plugin_gate_override("jules_plugin", _VALID_SPEC)
        assert result["jules_plugin"] == _VALID_SPEC
        # clear must not raise either
        await pgs.clear_plugin_gate_overrides()


@pytest.mark.asyncio
async def test_read_raw_handles_missing_row():
    from db_layer import plugin_gate_store as pgs
    with patch("db_layer.plugin_gate_store.get_async_session") as mock_sess:
        mock_sess.return_value = _session_cm(None)
        val = await pgs._read_raw()
        assert val == {}


@pytest.mark.asyncio
async def test_apply_db_overrides_loads_into_registry():
    from core.scope_management.gate_overlay import apply_db_overrides
    from core.scope_management.gates import PluginGateRegistry, _set_plugin_gate_registry

    _set_plugin_gate_registry(PluginGateRegistry())
    try:
        with patch(
            "db_layer.plugin_gate_store.get_plugin_gate_overrides",
            AsyncMock(return_value={"jules_plugin": _VALID_SPEC, "bad": {"bogus_key": 1}}),
        ):
            applied = await apply_db_overrides()
            assert applied == 1

        from core.scope_management.gates import get_plugin_gate_registry

        gate = get_plugin_gate_registry().get_gate("jules_plugin")
        assert gate is not None
        assert gate.core == ("core:graph:read",)
        assert get_plugin_gate_registry().get_gate("bad") is None
    finally:
        _set_plugin_gate_registry(None)


@pytest.mark.asyncio
async def test_apply_db_overrides_never_raises_on_read_failure():
    from core.scope_management.gate_overlay import apply_db_overrides

    with patch(
        "db_layer.plugin_gate_store.get_plugin_gate_overrides",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        applied = await apply_db_overrides()
        assert applied == 0
