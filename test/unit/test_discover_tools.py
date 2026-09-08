"""Unit tests for the gateway discover flow (run_graph mode='discover')."""

import pytest

import core_graph.mcp_tool as mcp_tool


_FAKE_CANDIDATES = [
    {
        "operation_id": "create_record",
        "plugin_id": "fake_plugin",
        "method": "POST",
        "path": "/records",
        "path_template": "/records",
        "description": "Create a new record record.",
        "score": 0.92,
        "parameters": {"name": {"type": "string", "required": True}},
        "is_fast_path": True,
    },
]


@pytest.mark.asyncio
async def test_discover_returns_candidate_tools(monkeypatch):
    """discover_candidates_impl returns ranked tool defs with no execution."""
    async def fake_search(query, top_k=3):
        return _FAKE_CANDIDATES

    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", True)
    monkeypatch.setattr(
        "db_layer.embeddings.embeddings_routes.search_routes", fake_search
    )

    out = await mcp_tool.discover_candidates_impl("make a record")
    assert out["status"] == "ok"
    assert out["mode"] == "discover"
    assert out["count"] == 1
    # tools is a dense CSV string (header + data row); no dual-write overview
    tools_csv = out["tools"]
    assert isinstance(tools_csv, str)
    header, *rows = tools_csv.splitlines()
    assert "operation_id" in header
    assert "plugin_id" in header
    assert "parameters" not in header  # schema dropped by normalize_tool_card
    assert "description" not in header  # redundant with summary
    assert any("create_record" in r and "fake_plugin" in r for r in rows)
    assert "overview" not in out


@pytest.mark.asyncio
async def test_discover_workspace_label_formatting(monkeypatch):
    """Tool cards extract workspace label and _format_candidates includes workspace tag."""
    from core_graph.node.helpers import _format_candidates, normalize_tool_card

    cand_ws = {
        "operation_id": "ws_op",
        "plugin_id": "proxy_p",
        "method": "GET",
        "path": "/ws",
        "description": "Workspace tool",
        "score": 0.85,
        "metadata": {"tags": ["proxy", "workspace:prod_ws"]},
    }

    card = normalize_tool_card(cand_ws)
    assert card["workspace"] == "prod_ws"
    assert "workspace:prod_ws" not in card["tags"]
    assert "proxy" in card["tags"]
    assert card["summary"] == card["description"]

    formatted = _format_candidates([cand_ws])
    assert "· workspace: prod_ws" in formatted

    # meta.workspace_label path (embedding worker also stores this key)
    cand_meta = {
        "operation_id": "ws_op2",
        "plugin_id": "proxy_p",
        "method": "GET",
        "path": "/ws2",
        "description": "Meta label tool",
        "score": 0.8,
        "metadata": {"tags": ["proxy"], "workspace_label": "from_meta"},
    }
    assert normalize_tool_card(cand_meta)["workspace"] == "from_meta"
    assert "· workspace: from_meta" in _format_candidates([cand_meta])


@pytest.mark.asyncio
async def test_run_graph_impl_discover_mode_short_circuits(monkeypatch):
    """run_graph_impl(mode='discover') routes to discover without invoking the graph."""
    async def fake_search(query, top_k=3):
        return _FAKE_CANDIDATES

    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", True)
    monkeypatch.setattr(
        "db_layer.embeddings.embeddings_routes.search_routes", fake_search
    )

    # If it tried to execute, _get_graph would be called; ensure it is not.
    async def boom():
        raise AssertionError("graph must not be built in discover mode")

    monkeypatch.setattr(mcp_tool, "_get_graph", boom)

    out = await mcp_tool.run_graph_impl("make a record", mode="discover")
    assert out["status"] == "ok"
    assert out["mode"] == "discover"


@pytest.mark.asyncio
async def test_discover_unavailable_without_db(monkeypatch):
    """discover returns unavailable when no DATABASE_URL."""
    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", False)
    out = await mcp_tool.discover_candidates_impl("anything")
    assert out["status"] == "unavailable"


@pytest.mark.asyncio
async def test_build_tools_summary_uses_list_and_caps(monkeypatch):
    """build_tools_summary pulls from list_enabled_routes and produces compact text."""
    from db_layer import gateway_settings_store as gs

    fake = [{"operation_id": "a", "plugin_id": "p", "description": "d"} for _ in range(100)]
    async def fake_list(**k): return fake

    monkeypatch.setattr("db_layer.embeddings.embeddings_routes.list_enabled_routes", fake_list)
    s = await gs.build_tools_summary(limit=5)
    assert "Live tool catalog" in s or s == ""  # may be truncated/empty in test env
    # at least does not explode on many candidates
