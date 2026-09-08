"""Unit tests for portfolio_plugin ask-turn telemetry (Phase 2 of the telemetry plan).

Mirrors test_portfolio_bake.py's telemetry-adjacent tests: the dual-write is
individually swallowed, the tenant is stamped, the question is truncated, and
a build_ask_overlay failure updates the same run_id rather than writing a
second row.
"""

import pytest
from unittest.mock import AsyncMock, patch

from plugins.portfolio_plugin.ask.telemetry import (
    mark_ask_turn_failed,
    new_run_id,
    record_ask_turn,
)
from plugins.portfolio_plugin.MCPTools.ask_tools import (
    build_ask_overlay,
    route_portfolio_ask,
)

TELEMETRY_MODULE = "plugins.portfolio_plugin.ask.telemetry"
ASK_TOOLS_MODULE = "plugins.portfolio_plugin.MCPTools.ask_tools"


def test_new_run_id_is_opaque_and_unique():
    a, b = new_run_id(), new_run_id()
    assert a != b
    assert len(a) == 32  # uuid4().hex


@pytest.mark.asyncio
async def test_record_ask_turn_dual_writes_and_swallows_collector_errors():
    """collector failure must not prevent the durable row from being written."""
    with (
        patch("core.telemetry.collector.collector.record_tool_call", side_effect=RuntimeError("boom")),
        patch(f"{TELEMETRY_MODULE}.logger") as mock_logger,
        patch(
            "plugins.portfolio_plugin.store.create_ask_turn", new=AsyncMock()
        ) as mock_create,
    ):
        await record_ask_turn(
            run_id="run-1",
            tenant_id=1,
            question="what did you build?",
            latency_ms=42,
            ok=True,
            intent="focus_fish",
        )

    mock_create.assert_awaited_once()
    assert mock_create.call_args.kwargs["run_id"] == "run-1"
    assert mock_create.call_args.kwargs["intent"] == "focus_fish"
    mock_logger.debug.assert_called_once()


@pytest.mark.asyncio
async def test_record_ask_turn_swallows_store_errors():
    """A store failure must never raise into the caller (never fail the ask turn)."""
    with (
        patch("core.telemetry.collector.collector.record_tool_call"),
        patch(
            "plugins.portfolio_plugin.store.create_ask_turn",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch(f"{TELEMETRY_MODULE}.logger") as mock_logger,
    ):
        await record_ask_turn(
            run_id="run-2", tenant_id=1, question="q", latency_ms=1, ok=True,
        )
    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_mark_ask_turn_failed_delegates_to_store():
    with patch(
        "plugins.portfolio_plugin.store.mark_ask_turn_failed", new=AsyncMock()
    ) as mock_mark:
        await mark_ask_turn_failed(run_id="run-3", error_type="overlay_build_failed")
    mock_mark.assert_awaited_once_with(run_id="run-3", error_type="overlay_build_failed")


@pytest.mark.asyncio
async def test_route_portfolio_ask_records_turn_on_success():
    fake_plan = type(
        "Plan",
        (),
        {
            "intent": "focus_fish",
            "focus_slug": "cat-mcp",
            "highlight_slugs": ("cat-mcp",),
            "add_slugs": (),
            "to_dict": lambda self: {"intent": "focus_fish"},
        },
    )()

    with (
        patch(f"{ASK_TOOLS_MODULE}._ask_enabled", return_value=True),
        patch(f"{ASK_TOOLS_MODULE}.current_tenant_id") as mock_tid,
        patch(f"{ASK_TOOLS_MODULE}.route_ask", new=AsyncMock(return_value=fake_plan)),
        patch(f"{ASK_TOOLS_MODULE}.record_ask_turn", new=AsyncMock()) as mock_record,
    ):
        mock_tid.get.return_value = 1
        res = await route_portfolio_ask(question="who built this?")

    assert res["status"] == "ok"
    assert res["run_id"]
    mock_record.assert_awaited_once()
    assert mock_record.call_args.kwargs["ok"] is True
    assert mock_record.call_args.kwargs["intent"] == "focus_fish"
    assert mock_record.call_args.kwargs["run_id"] == res["run_id"]


@pytest.mark.asyncio
async def test_route_portfolio_ask_records_failure_and_reraises():
    with (
        patch(f"{ASK_TOOLS_MODULE}._ask_enabled", return_value=True),
        patch(f"{ASK_TOOLS_MODULE}.current_tenant_id") as mock_tid,
        patch(f"{ASK_TOOLS_MODULE}.route_ask", new=AsyncMock(side_effect=RuntimeError("routing exploded"))),
        patch(f"{ASK_TOOLS_MODULE}.record_ask_turn", new=AsyncMock()) as mock_record,
    ):
        mock_tid.get.return_value = 1
        with pytest.raises(RuntimeError, match="routing exploded"):
            await route_portfolio_ask(question="who built this?")

    mock_record.assert_awaited_once()
    assert mock_record.call_args.kwargs["ok"] is False
    assert mock_record.call_args.kwargs["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_build_ask_overlay_marks_turn_failed_on_error_status():
    with (
        patch(f"{ASK_TOOLS_MODULE}._ask_enabled", return_value=True),
        patch(f"{ASK_TOOLS_MODULE}.current_tenant_id") as mock_tid,
        patch(
            f"{ASK_TOOLS_MODULE}._build_ask_overlay",
            new=AsyncMock(return_value={"status": "error", "error": "list_projects_failed"}),
        ),
        patch(f"{ASK_TOOLS_MODULE}.mark_ask_turn_failed", new=AsyncMock()) as mock_mark,
    ):
        mock_tid.get.return_value = 1
        res = await build_ask_overlay(
            ask_plan={"intent": "answer_only"},
            question="q",
            run_id="run-4",
        )

    assert res["overlay_status"] == "error"
    mock_mark.assert_awaited_once_with(run_id="run-4", error_type="overlay_build_failed")


@pytest.mark.asyncio
async def test_build_ask_overlay_ok_does_not_mark_failed():
    """A legitimate empty-blocks answer-only turn (status: ok) must not touch the row."""
    with (
        patch(f"{ASK_TOOLS_MODULE}._ask_enabled", return_value=True),
        patch(f"{ASK_TOOLS_MODULE}.current_tenant_id") as mock_tid,
        patch(
            f"{ASK_TOOLS_MODULE}._build_ask_overlay",
            new=AsyncMock(return_value={"status": "ok", "blocks": []}),
        ),
        patch(f"{ASK_TOOLS_MODULE}.mark_ask_turn_failed", new=AsyncMock()) as mock_mark,
    ):
        mock_tid.get.return_value = 1
        res = await build_ask_overlay(
            ask_plan={"intent": "answer_only"},
            question="q",
            run_id="run-5",
        )

    assert res["overlay_status"] == "ok"
    mock_mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_ask_turn_truncates_long_question():
    from plugins.portfolio_plugin.store import create_ask_turn

    long_question = "x" * 900
    captured = {}

    class _FakeSession:
        def add(self, row):
            captured["row"] = row

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    with patch(
        "plugins.portfolio_plugin.store.get_async_session",
        return_value=_FakeSession(),
    ):
        await create_ask_turn(run_id="run-6", tenant_id=1, question=long_question)

    assert len(captured["row"].question) == 500


@pytest.mark.asyncio
async def test_create_ask_turn_clips_visitor_session_id():
    from plugins.portfolio_plugin.models import ASK_TURN_SESSION_MAX
    from plugins.portfolio_plugin.store import create_ask_turn

    captured = {}

    class _FakeSession:
        def add(self, row):
            captured["row"] = row

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    with patch(
        "plugins.portfolio_plugin.store.get_async_session",
        return_value=_FakeSession(),
    ):
        await create_ask_turn(
            run_id="run-7",
            tenant_id=1,
            question="q",
            visitor_session_id="v" * 10_000,
        )

    assert captured["row"].visitor_session_id == "v" * ASK_TURN_SESSION_MAX


@pytest.mark.asyncio
async def test_list_ask_turns_requires_tenant():
    from plugins.portfolio_plugin.MCPTools.bake_admin_tools import list_ask_turns

    with patch(
        "plugins.portfolio_plugin.tenant.require_tenant_id",
        return_value=None,
    ):
        res = await list_ask_turns()
    assert res["status"] == "error"
    assert res["error"] == "tenant_unresolved"


@pytest.mark.asyncio
async def test_list_ask_turns_returns_tenant_rows():
    from plugins.portfolio_plugin.MCPTools.bake_admin_tools import list_ask_turns

    rows = [{"run_id": "abc", "intent": "focus_fish"}]
    with (
        patch("plugins.portfolio_plugin.tenant.require_tenant_id", return_value=7),
        patch(
            "plugins.portfolio_plugin.store.list_ask_turns",
            new=AsyncMock(return_value=rows),
        ) as mock_list,
    ):
        res = await list_ask_turns(limit=10, intent="focus_fish")

    assert res["status"] == "ok"
    assert res["turns"] == rows
    assert res["count"] == 1
    mock_list.assert_awaited_once_with(tenant_id=7, limit=10, intent="focus_fish")
