"""Unit tests for core_graph.model_roles.db_overlay.apply_db_overrides / clear_db_overrides_local."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core_graph.model_roles.db_overlay import apply_db_overrides, clear_db_overrides_local
from core_graph.model_roles.registry import get_model_role, get_model_role_registry


@pytest.mark.asyncio
async def test_apply_db_overrides_registers_role_and_effort_map():
    stored = {
        "roles": {"triage": {"role_id": "triage", "ladder": [{"selector": "strongest"}]}},
        "effort_map": {"low": "fast"},
    }
    with patch(
        "db_layer.model_role_store.get_model_role_overrides",
        AsyncMock(return_value=stored),
    ):
        applied = await apply_db_overrides()
        assert applied == 1

    spec = get_model_role("triage")
    assert spec is not None
    assert spec.owner == "db"
    assert spec.ladder[0].selector == "strongest"

    from core_graph.model_roles.resolver import get_effort_map
    assert get_effort_map()["low"] == "fast"


@pytest.mark.asyncio
async def test_db_spec_beats_core_for_same_role_id():
    stored = {
        "roles": {"triage": {"role_id": "triage", "ladder": [{"selector": "strongest"}]}},
        "effort_map": {},
    }
    with patch(
        "db_layer.model_role_store.get_model_role_overrides",
        AsyncMock(return_value=stored),
    ):
        await apply_db_overrides()

    reg = get_model_role_registry()
    assert reg.source_of("triage") == "db"

    reg.clear_db_overrides()
    assert reg.source_of("triage") == "core"


@pytest.mark.asyncio
async def test_invalid_stored_spec_is_skipped_others_still_load():
    stored = {
        "roles": {
            "triage": {"role_id": "triage", "ladder": [], "bogus": 1},  # invalid: fails parse
            "chat": {"role_id": "chat", "ladder": [{"selector": "core"}]},
        },
        "effort_map": {},
    }
    with patch(
        "db_layer.model_role_store.get_model_role_overrides",
        AsyncMock(return_value=stored),
    ):
        applied = await apply_db_overrides()
        assert applied == 1

    reg = get_model_role_registry()
    assert reg.source_of("triage") == "core"  # DB spec rejected, falls back to core
    assert reg.source_of("chat") == "db"


@pytest.mark.asyncio
async def test_db_row_for_unknown_role_id_does_not_crash_others():
    stored = {
        "roles": {
            "portfolio_custom_role": {"role_id": "portfolio_custom_role", "ladder": [{"selector": "core"}]},
        },
        "effort_map": {},
    }
    with patch(
        "db_layer.model_role_store.get_model_role_overrides",
        AsyncMock(return_value=stored),
    ):
        applied = await apply_db_overrides()
        assert applied == 1

    assert get_model_role("portfolio_custom_role") is not None
    assert get_model_role("triage") is not None  # unaffected core role


@pytest.mark.asyncio
async def test_db_unavailable_returns_zero_and_leaves_registry_untouched():
    with patch(
        "db_layer.model_role_store.get_model_role_overrides",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        applied = await apply_db_overrides()
        assert applied == 0

    reg = get_model_role_registry()
    assert reg.source_of("triage") == "core"


def test_clear_db_overrides_local():
    reg = get_model_role_registry()
    from core_graph.model_roles.role_spec import Rung, ModelRoleSpec
    reg.set_db_override("triage", ModelRoleSpec(role_id="triage", ladder=(Rung(selector="strongest"),), owner="db"))
    assert reg.source_of("triage") == "db"

    clear_db_overrides_local()
    assert reg.source_of("triage") == "core"

    from core_graph.model_roles.resolver import get_effort_map
    from core_graph.model_roles.builtin import get_default_effort_map
    assert get_effort_map() == get_default_effort_map()
