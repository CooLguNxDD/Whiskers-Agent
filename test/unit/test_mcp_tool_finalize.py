"""Acceptance tests for the MCP run_graph final-state fix.

Ensures the MCP-client streaming path returns the authoritative terminal
envelope (from the checkpointer) rather than a stale planner-stage snapshot,
and that the finalizer fallback no longer silently drops execution data.
"""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import core_graph.mcp_tool as mcp_tool


@pytest.fixture(autouse=True)
def _stub_minio_offload(monkeypatch):
    """Prevent envelope sanitize/compress from hanging on live MinIO/DB.

    ``_sanitize_envelope`` / ``_compress_carrier`` run the full response_shape
    pipeline with ``offload_minio=True``. Without a stub, unit tests that build
    oversized payloads may block forever trying to reach MinIO.
    """

    async def _noop_extract(payload, **_kw):
        return payload, []

    monkeypatch.setattr(
        "core.artifact_store.store.extract_large_artifacts",
        _noop_extract,
    )
    # Also disable the offload default gate used by apply_shape_async.
    monkeypatch.setattr(
        "utils.response_shape._minio_offload_default",
        lambda: False,
    )


# --- _finalize_graph_response hardening -------------------------------------

@pytest.mark.asyncio
async def test_finalize_returns_response_envelope_when_present():
    result = {"response": {"status": "ok", "steps_executed": 2, "data": [1, 2]}}
    out = await mcp_tool._finalize_graph_response(result)
    assert out["status"] == "ok"
    assert out["steps_executed"] == 2


@pytest.mark.asyncio
async def test_finalize_collapses_model_audit_into_model_used():
    """model_audit (per-rung detail, admin-playground-only) collapses into a
    small {node_id: model_name} shorthand on the plain tool response, so a
    caller with no playground access can still see which model actually ran."""
    result = {
        "response": {"status": "ok", "steps_executed": 1},
        "model_audit": [
            {"node": "triage", "model": "claude-fast", "status": "invalid", "escalated": True},
            {"node": "triage", "model": "claude-sonnet-5", "status": "ok", "escalated": True},
            {"node": "summary_node", "model": "claude-sonnet-5", "status": "ok"},
            {"node": "no_model_yet", "model": None, "status": "ok"},
        ],
    }
    out = await mcp_tool._finalize_graph_response(result)
    assert out["model_used"] == {"triage": "claude-sonnet-5", "summary_node": "claude-sonnet-5"}


@pytest.mark.asyncio
async def test_finalize_omits_model_used_when_no_audit():
    result = {"response": {"status": "ok", "steps_executed": 1}}
    out = await mcp_tool._finalize_graph_response(result)
    assert "model_used" not in out


@pytest.mark.asyncio
async def test_finalize_fallback_includes_summary_and_step_results():
    """When response is missing, the fallback must surface any partial
    execution data (summary / step_results), not just gate metadata."""
    result = {
        "response": None,
        "gate_decision": "confirm",
        "confidence": 0.63,
        "summary": "Did two things",
        "step_results": [{"status": "ok"}, {"status": "ok"}],
    }
    out = await mcp_tool._finalize_graph_response(result)
    assert out["gate_decision"] == "confirm"
    assert out["summary"] == "Did two things"
    assert out["step_results"] == [{"status": "ok"}, {"status": "ok"}]


@pytest.mark.asyncio
async def test_finalize_pure_gate_metadata_when_nothing_executed():
    result = {"response": None, "gate_decision": "clarify", "confidence": 0.4}
    out = await mcp_tool._finalize_graph_response(result)
    assert out["status"] == "ok"
    assert out["gate_decision"] == "clarify"


@pytest.mark.asyncio
async def test_finalize_flips_stale_error_to_ok_when_layout_present():
    """Reproduces the design_layout self-heal case: an early step_dispatcher
    'recover' leg terminates on a stale per-step error (bypassing summary_node),
    but goap_goal.get_terminal_response salvaged a real layout from
    working_memory. The client must see status ok, not a failed turn."""
    result = {
        "summary": "I have successfully updated the portfolio layout to a neon theme.",
        "response": {
            "status": "error",
            "message": "Execution failed (?): design_layout requires a 'spec' JSON "
                        "object argument (missing required parameter 'spec') | args={}",
            "unresolved_required": [],
            "layout": {"version": 1, "meta": {"audience": "default"}, "blocks": []},
            "carry": {"layout": {"version": 1, "meta": {"audience": "default"}, "blocks": []}},
        },
    }
    out = await mcp_tool._finalize_graph_response(result)
    assert out["status"] == "ok"
    assert out["message"] == "I have successfully updated the portfolio layout to a neon theme."
    assert out["layout"]["version"] == 1
    assert out["carry"]["layout"]["version"] == 1


@pytest.mark.asyncio
async def test_finalize_flips_stale_error_via_carry_only_layout():
    """Same salvage path, but layout only lives under carry (not top-level)."""
    result = {
        "summary": "Done.",
        "response": {
            "status": "error",
            "message": "stale failure",
            "carry": {"layout": {"version": 1, "meta": {}, "blocks": []}},
        },
    }
    out = await mcp_tool._finalize_graph_response(result)
    assert out["status"] == "ok"
    assert out["message"] == "Done."


@pytest.mark.asyncio
async def test_finalize_keeps_error_status_when_no_layout_present():
    """A genuine failure with no salvaged layout must still report as error —
    this guard must not paper over real failures."""
    result = {
        "summary": None,
        "response": {"status": "error", "message": "actually failed"},
    }
    out = await mcp_tool._finalize_graph_response(result)
    assert out["status"] == "error"
    assert out["message"] == "actually failed"


@pytest.mark.asyncio
async def test_finalize_injects_summary_message_into_response_envelope():
    result = {
        "summary": "Fetched contact IAMANDREW and posted a greeting.",
        "response": {
            "status": "ok",
            "steps_executed": 2,
            "data": [{"id": 1}, {"id": 2}],
        },
    }
    out = await mcp_tool._finalize_graph_response(result)
    assert out["message"] == "Fetched contact IAMANDREW and posted a greeting."
    assert out["summary"] == "Fetched contact IAMANDREW and posted a greeting."


# --- stream_graph_impl emits authoritative final state ----------------------

def _protocol_updates(update_chunks):
    """Build v3 protocol events for per-node update chunks."""
    return [
        {"method": "updates", "params": {"data": chunk}}
        for chunk in update_chunks
    ]


class _FakeStream:
    """Mimics the astream_events v3 projection object."""

    def __init__(self, value_snaps, protocol_events=None):
        self._value_snaps = value_snaps
        self._protocol_events = protocol_events or []

    def __aiter__(self):
        return self._protocol_iter()

    async def _protocol_iter(self):
        for evt in self._protocol_events:
            yield evt

    @property
    def values(self):
        async def _gen():
            for s in self._value_snaps:
                yield s
        return _gen()

    @property
    def messages(self):
        async def _gen():
            if False:
                yield None
        return _gen()


class _FakeGraph:
    """Streams stale planner snapshots, but holds the true terminal state in
    the checkpointer (returned by aget_state)."""

    def __init__(self, stale_snaps, terminal_state, update_chunks=None):
        self._stale = stale_snaps
        self._terminal = terminal_state
        self._update_chunks = update_chunks or []

    async def astream_events(self, initial_state, version=None, config=None):
        return _FakeStream(self._stale, _protocol_updates(self._update_chunks))

    async def aget_state(self, config):
        return types.SimpleNamespace(values=self._terminal)


@pytest.mark.asyncio
async def test_stream_emits_active_node_before_full_snapshot(monkeypatch):
    """Updates stream must surface active_node so the playground DAG can pulse."""
    stale = [{"user_query": "hi", "triage_mode": "task"}]
    terminal = stale[0]
    fake = _FakeGraph(
        stale,
        terminal,
        update_chunks=[{"decompose": {"sub_tasks": ["hi"]}}, {"embedder": {}}],
    )

    monkeypatch.setattr(mcp_tool, "_LLM_USABLE", True)
    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", True)
    monkeypatch.setattr(mcp_tool, "_pg_saver", None)
    monkeypatch.setattr(mcp_tool, "_get_graph", AsyncMock(return_value=fake))
    # Force root stack — avoid live oneshot provider resolution hanging unit tests.
    monkeypatch.setenv("GRAPH_MODE", "root")

    events = []
    async for ev in mcp_tool.stream_graph_impl("hi", force_execute=True):
        if ev.get("type") == "values":
            events.append(ev["state"])

    active_hits = [s.get("active_node") for s in events if s.get("active_node")]
    assert "decompose" in active_hits
    assert any(s.get("triage_mode") == "task" for s in events)


@pytest.mark.asyncio
async def test_stream_emits_authoritative_final_state(monkeypatch):
    """The last `values` event from stream_graph_impl must be the authoritative
    checkpointed terminal state, not the last streamed planner snapshot."""
    stale = [{"gate_decision": "confirm", "confidence": 0.63, "response": None}]
    terminal = {
        "gate_decision": "execute",
        "confidence": 0.63,
        "response": {"status": "ok", "steps_executed": 1, "data": {"x": 1},
                     "summary": "done"},
    }
    fake = _FakeGraph(stale, terminal)

    monkeypatch.setattr(mcp_tool, "_LLM_USABLE", True)
    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", True)
    monkeypatch.setattr(mcp_tool, "_pg_saver", None)  # ephemeral eviction skipped
    monkeypatch.setattr(mcp_tool, "_get_graph", AsyncMock(return_value=fake))
    monkeypatch.setenv("GRAPH_MODE", "root")

    value_events = []
    async for ev in mcp_tool.stream_graph_impl("do something", force_execute=True):
        if ev.get("type") == "values":
            value_events.append(ev["state"])

    assert value_events, "expected at least one values event"
    # Last values event must be the authoritative terminal state.
    assert value_events[-1]["response"]["status"] == "ok"
    assert value_events[-1]["response"]["steps_executed"] == 1


@pytest.mark.asyncio
async def test_run_graph_with_progress_emits_execution_keepalive(monkeypatch):
    """Slow graph start must still ping progress so MCP clients don't hit -32001."""

    class _SlowFakeGraph(_FakeGraph):
        async def astream_events(self, initial_state, version=None, config=None):
            await asyncio.sleep(0.05)
            return _FakeStream(self._stale)

    stale = [{"gate_decision": "execute", "response": {"status": "ok"}}]
    terminal = stale[0]
    fake = _SlowFakeGraph(stale, terminal)

    monkeypatch.setattr(mcp_tool, "_LLM_USABLE", True)
    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", True)
    monkeypatch.setattr(mcp_tool, "_pg_saver", None)
    monkeypatch.setattr(mcp_tool, "_get_graph", AsyncMock(return_value=fake))
    monkeypatch.setenv("GRAPH_MODE", "root")

    from core_graph.node.helpers import mcp_ctx

    original_interval = mcp_ctx.ELICIT_KEEPALIVE_INTERVAL_S
    mcp_ctx.ELICIT_KEEPALIVE_INTERVAL_S = 0.01
    try:
        ctx = MagicMock()
        meta = MagicMock()
        meta.progressToken = "tok-exec"
        ctx.request_context.meta = meta
        ctx.report_progress = AsyncMock()

        await mcp_tool._run_graph_with_progress("warm model", force_execute=True, ctx=ctx)

        keepalive_calls = [
            c for c in ctx.report_progress.call_args_list
            if '"awaiting": "execution"' in (c.args[2] if len(c.args) > 2 else "")
        ]
        assert keepalive_calls, "expected execution keepalive progress during silent graph start"
    finally:
        mcp_ctx.ELICIT_KEEPALIVE_INTERVAL_S = original_interval


@pytest.mark.asyncio
async def test_run_graph_with_progress_returns_authoritative_envelope(monkeypatch):
    """End-to-end MCP path: a stale planner snapshot followed by the
    authoritative terminal state yields the full execution envelope."""
    stale = [{"gate_decision": "confirm", "confidence": 0.63, "response": None}]
    terminal = {
        "gate_decision": "execute",
        "confidence": 0.63,
        "response": {"status": "ok", "steps_executed": 2, "data": [1, 2],
                     "summary": "done"},
    }
    fake = _FakeGraph(stale, terminal)

    monkeypatch.setattr(mcp_tool, "_LLM_USABLE", True)
    monkeypatch.setattr(mcp_tool, "_DB_AVAILABLE", True)
    monkeypatch.setattr(mcp_tool, "_pg_saver", None)
    monkeypatch.setattr(mcp_tool, "_get_graph", AsyncMock(return_value=fake))
    monkeypatch.setenv("GRAPH_MODE", "root")

    ctx = MagicMock()
    ctx.report_progress = AsyncMock()

    out = await mcp_tool.run_graph_impl("do something", force_execute=True, ctx=ctx)

    assert out["status"] == "ok"
    assert out["steps_executed"] == 2
    assert out["data"] == [1, 2]


# --- _sanitize_envelope unit tests -------------------------------------------

@pytest.mark.asyncio
async def test_sanitize_envelope_small_passthrough():
    """Small envelopes must be returned byte-for-byte unchanged."""
    import core_graph.mcp_tool as mt
    env = {"status": "ok", "summary": "done", "data": [1, 2, 3]}
    result = await mt._sanitize_envelope(env, max_bytes=450_000)
    assert result is env  # same object — no copy


@pytest.mark.asyncio
async def test_sanitize_envelope_oversized_data_list_compressed_to_csv():
    """An oversized data list must be compressed through the pipeline (strip/limit/CSV)
    before falling back to a truncated sentinel.  The result should be a dense
    CSV string, well within budget."""
    import core_graph.mcp_tool as mt
    import json
    # 1000 items each with a 600-byte blob = ~600 KB total — exceeds 450 KB budget
    big_item = {"id": "1", "name": "session", "blob": "X" * 600}
    env = {
        "status": "ok",
        "summary": "Big response",
        "data": [dict(big_item, id=str(i)) for i in range(1000)],  # ~600 KB
    }
    result = await mt._sanitize_envelope(env, max_bytes=450_000)
    # Must be under budget and serializable without masking errors with default=str
    serialized = json.dumps(result)
    assert len(serialized.encode()) <= 450_000
    # data must have been either compressed to a CSV string OR replaced with the
    # truncation sentinel — a bare identity check is insufficient because it
    # allows the implementation to silently drop data.
    assert isinstance(result["data"], str) or (
        isinstance(result["data"], dict)
        and result["data"].get("_truncated") is True
    ), f"data must be CSV string or sentinel, got {type(result['data'])}"
    # Sacred fields must still be intact
    assert result["summary"] == "Big response"
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_sanitize_envelope_compressed_data_is_csv_string():
    """The compress_carrier helper must return a CSV string for a list of dicts."""
    import core_graph.mcp_tool as mt

    # MinIO offload stubbed by module autouse fixture.
    items = [{"id": str(i), "name": f"sess-{i}", "state": "COMPLETED"} for i in range(30)]
    result = await mt._compress_carrier(items)
    # CSV is a string, not a list or dict
    assert isinstance(result, str), f"expected CSV string, got {type(result)}"
    # Should contain a header row
    assert "id" in result
    assert "name" in result


@pytest.mark.asyncio
async def test_sanitize_envelope_sacred_fields_never_dropped():
    """Sacred fields (summary, message, carry, layout, status, artifacts) must survive even after sanitization."""
    import core_graph.mcp_tool as mt
    sacred_carry = {"layout": {"blocks": []}}
    artifacts = [{"short_id": "art_diff_001", "kind": "diff", "bytes": 99}]
    env = {
        "status": "ok",
        "summary": "Keep me",
        "message": "Keep me too",
        "carry": sacred_carry,
        "layout": {"version": 1},
        "artifacts": artifacts,
        "data": [{"blob": "X" * 2000}] * 300,  # oversized
    }
    result = await mt._sanitize_envelope(env, max_bytes=450_000)
    assert result["summary"] == "Keep me"
    assert result["message"] == "Keep me too"
    assert result["carry"] is sacred_carry
    assert result["layout"]["version"] == 1
    assert result["status"] == "ok"
    assert result["artifacts"] is artifacts


@pytest.mark.asyncio
async def test_sanitize_envelope_keeps_ask_overlay_fields():
    import core_graph.mcp_tool as mt

    blocks = [{"type": "fishTank", "id": "fish-tank-1", "props": {"fish": [{"slug": "fisoul"}]}}]
    env = {
        "status": "ok",
        "summary": "spawned",
        "message": "spawned",
        "blocks": blocks,
        "focus_slug": "fisoul",
        "pending_job": {"job_id": "j1"},
        "flow_id": "portfolio_ask_v1",
        "recommendations": [{"slug": "fisoul", "name": "Fisoul", "in_tank": False}],
        "data": [{"blob": "X" * 2000}] * 300,
    }
    result = await mt._sanitize_envelope(env, max_bytes=450_000)
    assert result["blocks"] is blocks
    assert result["focus_slug"] == "fisoul"
    assert result["pending_job"]["job_id"] == "j1"
    assert result["flow_id"] == "portfolio_ask_v1"
    assert result["recommendations"][0]["slug"] == "fisoul"



@pytest.mark.asyncio
async def test_sanitize_envelope_output_is_always_valid_json():
    """Sanitized output must always be serializable to valid JSON."""
    import core_graph.mcp_tool as mt
    import json
    env = {
        "status": "ok",
        "summary": "Done",
        "data": [{"field": "X" * 5000}] * 200,
    }
    result = await mt._sanitize_envelope(env, max_bytes=450_000)
    # Must not raise
    serialized = json.dumps(result)
    # Must be parseable back
    parsed = json.loads(serialized)
    assert parsed["status"] == "ok"


@pytest.mark.asyncio
async def test_sanitize_envelope_fallback_step_results_also_capped():
    """step_results in a fallback envelope must also be trimmed when oversized —
    first compressed via the pipeline, then sentinel if still over budget."""
    import core_graph.mcp_tool as mt
    import json
    # 1000 items each with a 600-byte output field = ~600 KB — exceeds 450 KB
    big_result = {"op": "bigOp", "id": "1", "status": "ok", "output": "X" * 600}
    env = {
        "status": "ok",
        "gate_decision": "execute",
        "step_results": [dict(big_result, op=f"op-{i}") for i in range(1000)],  # ~600 KB
    }
    result = await mt._sanitize_envelope(env, max_bytes=450_000)
    serialized = json.dumps(result)
    assert len(serialized.encode()) <= 450_000
    # step_results must have been either compressed to a CSV string OR replaced
    # with the truncation sentinel — identity-only check allows silent data loss.
    assert isinstance(result["step_results"], str) or (
        isinstance(result["step_results"], dict)
        and result["step_results"].get("_truncated") is True
    ), f"step_results must be CSV string or sentinel, got {type(result['step_results'])}"
    assert result["gate_decision"] == "execute"


@pytest.mark.asyncio
async def test_sanitize_envelope_sentinel_used_as_last_resort(monkeypatch):
    """When CSV compression still exceeds the budget, fall back to the
    ``{_truncated: True, ...}`` sentinel.

    Pipeline: oversized list → strip/limit + CSV → if still over budget →
    sentinel. A tiny 5-row payload can fit under a small budget *without*
    sanitizing (or as CSV), so this uses a wide multi-row payload whose
    compressed CSV also exceeds the budget, forcing the last-resort path.

    Uses plain ``json.dumps`` (no ``default=str``) so serialization failures
    are not silently masked."""
    import core_graph.mcp_tool as mt
    import json

    async def _noop_extract(payload, **_kw):
        return payload, []

    monkeypatch.setattr(
        "core.artifact_store.store.extract_large_artifacts", _noop_extract
    )

    # 50 rows × long val → raw ~5 KB; compressed CSV (limit 20) still ~1.5 KB
    env = {
        "status": "ok",
        "summary": "tiny budget",
        "data": [{"id": str(i), "val": "X" * 80} for i in range(50)],
    }
    # Sacred fields + sentinel fit; raw list and compressed CSV do not.
    FEASIBLE_BUDGET = 200
    assert len(json.dumps(env).encode()) > FEASIBLE_BUDGET
    compressed = await mt._compress_carrier(env["data"])
    assert isinstance(compressed, str)
    assert len(json.dumps({**env, "data": compressed}).encode()) > FEASIBLE_BUDGET

    result = await mt._sanitize_envelope(env, max_bytes=FEASIBLE_BUDGET)
    # Plain json.dumps — no default=str so serialization failures are not masked
    serialized = json.dumps(result)
    assert len(serialized.encode()) <= FEASIBLE_BUDGET, (
        f"sanitized envelope must fit within {FEASIBLE_BUDGET} bytes, "
        f"got {len(serialized.encode())}"
    )
    # Sacred field must survive
    assert result["status"] == "ok"
    assert result["summary"] == "tiny budget"
    # data must have been replaced with the _truncated sentinel dict
    assert isinstance(result["data"], dict), (
        f"expected sentinel dict, got {type(result['data'])}"
    )
    assert result["data"]["_truncated"] is True
    assert result["data"].get("item_count") == 50


@pytest.mark.asyncio
async def test_finalize_sanitizes_oversized_envelope():
    """_finalize_graph_response must produce a sanitized, valid-JSON-sized envelope
    when the raw response data is large enough to breach the FastMCP limit.
    The pipeline should compress `data` via CSV (not just drop it)."""
    import core_graph.mcp_tool as mt
    import json
    big_session = {"id": "sess-1", "name": "s", "state": "COMPLETED", "report": "X" * 10_000}
    result = {
        "response": {
            "status": "ok",
            "summary": "Listed Jules sessions",
            "data": [dict(big_session, id=f"sess-{i}") for i in range(70)],  # ~700 KB raw
        }
    }
    out = await mt._finalize_graph_response(result)
    serialized = json.dumps(out)
    assert len(serialized.encode()) <= 450_000, "envelope must fit within budget"
    # data must have been either compressed to a CSV string OR replaced with the
    # truncation sentinel — identity-only check allows silent data loss.
    assert isinstance(out["data"], str) or (
        isinstance(out["data"], dict)
        and out["data"].get("_truncated") is True
    ), f"data must be CSV string or sentinel, got {type(out['data'])}"
    assert out["summary"] == "Listed Jules sessions"


# --- ListSessions response shape tests ---------------------------------------

# Exact set of keys allowed by the ListSessions project shape in tools_api_config.json.
_LIST_SESSIONS_ALLOWED_KEYS = frozenset({
    "id", "name", "title", "createTime", "updateTime", "state", "url",
})


def _make_heavy_session(session_id: str) -> dict:
    """Build a realistic heavy Jules session payload with all embeddable fields."""
    return {
        "id": session_id,
        "name": f"projects/proj/sessions/{session_id}",
        "title": "My Jules Session",
        "createTime": "2026-07-20T00:00:00Z",
        "updateTime": "2026-07-21T00:00:00Z",
        "state": "COMPLETED",
        "url": f"https://jules.google.com/task/{session_id}",
        # Heavy fields that must be dropped:
        "prompt": "Fix all bugs in the repo",
        "summary": "X" * 50_000,  # 50 KB embedded report
        "outputs": ["output1", "output2"],
        "sourceContext": {"repo": "...", "diff": "X" * 10_000},
    }


@pytest.mark.asyncio
async def test_list_sessions_shape_drops_heavy_fields(monkeypatch):
    """The ListSessions response shape must project only whitelisted metadata
    fields and drop any heavy embedded text (report, prompt, outputs, etc.).

    Asserts ``set(item) <= _LIST_SESSIONS_ALLOWED_KEYS`` so any unexpected
    key leaking through the projection is caught as a regression."""
    from utils.response_shape import apply_static_shape_async

    async def _noop_extract(payload, **_kw):
        return payload, []

    monkeypatch.setattr(
        "core.artifact_store.store.extract_large_artifacts", _noop_extract
    )
    payload = [_make_heavy_session("sess-abc"), _make_heavy_session("sess-def")]

    shaped, meta = await apply_static_shape_async(payload, "ListSessions")

    import json
    serialized = json.dumps(shaped)
    # Must be dramatically smaller than the raw payload
    assert len(serialized.encode()) < 5_000, "shaped output should be small"
    # Each item must contain only the whitelisted keys — no extras allowed.
    for item in shaped:
        unexpected = set(item) - _LIST_SESSIONS_ALLOWED_KEYS
        assert not unexpected, (
            f"unexpected keys leaked through ListSessions projection: {unexpected}"
        )
        # Required whitelisted fields must be present.
        assert "id" in item
        assert "title" in item
        assert "state" in item


@pytest.mark.asyncio
async def test_list_sessions_shape_via_real_config_projection_path(monkeypatch):
    """Verify that the *config-backed* ListSessions shape is wired correctly.

    This test exercises ``apply_static_shape_async`` with the real
    ``"ListSessions"`` operation id, ensuring a regression where the config
    entry is removed or renamed is caught.  Also confirms that the base
    shape's ``strip_hex`` + ``strip_empty`` passes have run on top of the
    projection."""
    from utils.response_shape import apply_static_shape_async

    async def _noop_extract(payload, **_kw):
        return payload, []

    monkeypatch.setattr(
        "core.artifact_store.store.extract_large_artifacts", _noop_extract
    )
    # Inject a hex-like field to validate that strip_hex runs after projection.
    session = dict(_make_heavy_session("sess-xyz"))
    session["url"] = "https://jules.google.com/task/" + "a" * 64  # long hex-like suffix
    payload = [session]

    shaped, meta = await apply_static_shape_async(payload, "ListSessions")

    assert isinstance(shaped, list) and len(shaped) == 1
    item = shaped[0]
    # Strict whitelist: set membership must be a subset of the allowed keys.
    unexpected = set(item) - _LIST_SESSIONS_ALLOWED_KEYS
    assert not unexpected, (
        f"config-backed ListSessions projection leaked unexpected keys: {unexpected}"
    )
    # Heavy fields must be absent — confirms the project shape ran.
    assert "prompt" not in item
    assert "summary" not in item
    assert "outputs" not in item
    assert "sourceContext" not in item
    # The projection ran (meta must have registered the project step).
    assert "project" in meta

