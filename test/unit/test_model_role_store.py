"""Unit tests for db_layer.model_role_store (mirrors test_step_model_store.py style)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core_graph.model_roles.role_spec import ModelRoleSpecError

_VALID_SPEC = {"role_id": "triage", "ladder": [{"selector": "core"}]}


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
    from db_layer import model_role_store as mrs
    with patch.object(mrs, "_db_available", return_value=False):
        val = await mrs.get_model_role_overrides()
        assert val == {"roles": {}, "effort_map": {}}
        assert val is not mrs._EMPTY


@pytest.mark.asyncio
async def test_set_and_get_role_roundtrip():
    from db_layer import model_role_store as mrs
    with patch.object(mrs, "_db_available", return_value=True):
        store: dict = {}

        async def fake_write(value):
            store["value"] = value

        with patch.object(mrs, "_write_raw", AsyncMock(side_effect=fake_write)):
            with patch.object(mrs, "_read_raw", AsyncMock(return_value={"roles": {}, "effort_map": {}})):
                result = await mrs.set_model_role_override("triage", _VALID_SPEC)
                assert result["roles"]["triage"] == _VALID_SPEC
                assert store["value"]["roles"]["triage"] == _VALID_SPEC

            with patch.object(mrs, "_read_raw", AsyncMock(return_value=store["value"])):
                got = await mrs.get_model_role_overrides()
                assert got["roles"]["triage"] == _VALID_SPEC


@pytest.mark.asyncio
async def test_set_effort_map_roundtrip():
    from db_layer import model_role_store as mrs
    with patch.object(mrs, "_db_available", return_value=True):
        store: dict = {}

        async def fake_write(value):
            store["value"] = value

        with patch.object(mrs, "_write_raw", AsyncMock(side_effect=fake_write)):
            with patch.object(mrs, "_read_raw", AsyncMock(return_value={"roles": {}, "effort_map": {}})):
                result = await mrs.set_effort_map({"low": "fast", "high": "strongest"})
                assert result["effort_map"] == {"low": "fast", "high": "strongest"}


@pytest.mark.asyncio
async def test_invalid_spec_raises_and_persists_nothing():
    from db_layer import model_role_store as mrs
    with patch.object(mrs, "_db_available", return_value=True):
        write_mock = AsyncMock()
        with patch.object(mrs, "_write_raw", write_mock):
            with pytest.raises(ModelRoleSpecError):
                await mrs.set_model_role_override("triage", {"role_id": "triage", "ladder": [], "bogus_key": 1})
            write_mock.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_effort_map_key_rejected():
    from db_layer import model_role_store as mrs
    with patch.object(mrs, "_db_available", return_value=True):
        write_mock = AsyncMock()
        with patch.object(mrs, "_write_raw", write_mock):
            with pytest.raises(ModelRoleSpecError):
                await mrs.set_effort_map({"extreme": "strongest"})
            write_mock.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_effort_map_value_rejected():
    from db_layer import model_role_store as mrs
    with patch.object(mrs, "_db_available", return_value=True):
        write_mock = AsyncMock()
        with patch.object(mrs, "_write_raw", write_mock):
            with pytest.raises(ModelRoleSpecError):
                await mrs.set_effort_map({"low": "not_a_real_alias"})
            write_mock.assert_not_called()


@pytest.mark.asyncio
async def test_set_model_role_override_none_deletes():
    from db_layer import model_role_store as mrs
    with patch.object(mrs, "_db_available", return_value=True):
        store = {"roles": {"triage": _VALID_SPEC, "chat": {"role_id": "chat", "ladder": [{"selector": "core"}]}}, "effort_map": {}}

        async def fake_write(value):
            store.clear()
            store.update(value)

        with patch.object(mrs, "_write_raw", AsyncMock(side_effect=fake_write)):
            with patch.object(mrs, "_read_raw", AsyncMock(return_value=dict(store))):
                result = await mrs.set_model_role_override("triage", None)
                assert "triage" not in result["roles"]
                assert "chat" in result["roles"]


@pytest.mark.asyncio
async def test_no_db_available_write_functions_do_not_raise():
    from db_layer import model_role_store as mrs
    with patch.object(mrs, "_db_available", return_value=False):
        result = await mrs.set_model_role_override("triage", _VALID_SPEC)
        assert result["roles"]["triage"] == _VALID_SPEC
        result2 = await mrs.set_effort_map({"low": "fast"})
        assert result2["effort_map"] == {"low": "fast"}
        # clear must not raise either
        await mrs.clear_model_role_overrides()


@pytest.mark.asyncio
async def test_read_raw_handles_missing_row():
    from db_layer import model_role_store as mrs
    with patch("db_layer.model_role_store.get_async_session") as mock_sess:
        mock_sess.return_value = _session_cm(None)
        val = await mrs._read_raw()
        assert val == {"roles": {}, "effort_map": {}}
