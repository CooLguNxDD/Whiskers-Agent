"""
Unit tests for step_model_settings_store (mirrors test_gateway_settings.py style).
"""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_get_step_model_policy_returns_default_when_no_db():
    from db_layer import step_model_settings_store as sms
    with patch.object(sms, "_db_available", return_value=False):
        val = await sms.get_step_model_policy()
        assert val == sms.DEFAULT_POLICY
        # must be a copy, not the module constant
        assert val is not sms.DEFAULT_POLICY
        assert val["strategy"] == "strength"
        assert val["parallel_enabled"] is True
        assert val["fanout_concurrency"] == 5
        assert isinstance(val["task_type_map"], dict)
        assert isinstance(val["op_overrides"], dict)
        assert val["task_type_map"] == {}
        assert val["op_overrides"] == {}


@pytest.mark.asyncio
async def test_get_step_model_policy_merges_stored_partial_over_defaults():
    from db_layer import step_model_settings_store as sms
    with patch.object(sms, "_db_available", return_value=True):
        class Row:
            def __init__(self, v):
                self._v = v
            def __getitem__(self, i):
                return self._v if i == 0 else None

        with patch("db_layer.step_model_settings_store.get_async_session") as mock_sess:
            cm = AsyncMock()
            # simulate row[0] being a partial dict
            cm.__aenter__.return_value.execute = AsyncMock(
                return_value=type("R", (), {"fetchone": lambda s: Row({"strategy": "off"})})()
            )
            mock_sess.return_value = cm

            val = await sms.get_step_model_policy()
            assert val["strategy"] == "off"
            # missing keys come from DEFAULT
            assert val["fanout_concurrency"] == 5
            assert val["parallel_enabled"] is True
            assert val["task_type_map"] == {}
            assert val["op_overrides"] == {}


@pytest.mark.asyncio
async def test_set_step_model_policy_roundtrip_with_db():
    from db_layer import step_model_settings_store as sms
    with patch.object(sms, "_db_available", return_value=True):
        call_idx = {"i": 0}

        class Row:
            def __init__(self, v):
                self._v = v
            def __getitem__(self, i):
                return self._v if i == 0 else None

        def make_exec_result(*a, **k):
            i = call_idx["i"]
            call_idx["i"] += 1
            if i == 0:
                # read returns no prior row
                r = type("R", (), {"fetchone": lambda s: None})()
                return r
            else:
                # upsert execute result (fetchone not used)
                r = type("R", (), {"fetchone": lambda s: None})()
                return r

        with patch("db_layer.step_model_settings_store.get_async_session") as mock_sess:
            cm = AsyncMock()
            cm.__aenter__.return_value.execute = AsyncMock(side_effect=make_exec_result)
            cm.__aenter__.return_value.commit = AsyncMock()
            mock_sess.return_value = cm

            res = await sms.set_step_model_policy({"strategy": "task_type"})
            assert res["strategy"] == "task_type"
            assert res["fanout_concurrency"] == 5
            assert res["parallel_enabled"] is True
            assert isinstance(res["task_type_map"], dict)
            assert isinstance(res["op_overrides"], dict)


@pytest.mark.asyncio
async def test_set_step_model_policy_ignores_invalid_strategy():
    from db_layer import step_model_settings_store as sms
    with patch.object(sms, "_db_available", return_value=True):
        call_idx = {"i": 0}

        class Row:
            def __init__(self, v):
                self._v = v
            def __getitem__(self, i):
                return self._v if i == 0 else None

        def make_exec_result(*a, **k):
            i = call_idx["i"]
            call_idx["i"] += 1
            if i == 0:
                # prior stored value with explicit strategy
                r = type("R", (), {"fetchone": lambda s: Row({"strategy": "explicit"})})()
                return r
            else:
                r = type("R", (), {"fetchone": lambda s: None})()
                return r

        with patch("db_layer.step_model_settings_store.get_async_session") as mock_sess:
            cm = AsyncMock()
            cm.__aenter__.return_value.execute = AsyncMock(side_effect=make_exec_result)
            cm.__aenter__.return_value.commit = AsyncMock()
            mock_sess.return_value = cm

            # patch contains invalid strategy; also a valid other field
            res = await sms.set_step_model_policy({"strategy": "nonsense", "fanout_concurrency": 7})
            # strategy kept from prior (explicit), not changed to nonsense
            assert res["strategy"] == "explicit"
            # other valid patch key applied
            assert res["fanout_concurrency"] == 7
            # defaults for absent still present
            assert "task_type_map" in res
