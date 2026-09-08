"""Unit tests for core/artifact_store/store.py (MinIO offload + extract walk)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.artifact_store.store import (
    extract_large_artifacts,
    is_offload_marker,
    is_valid_short_id,
    offload_text,
    _marker,
)


def test_is_valid_short_id():
    assert is_valid_short_id("art_diff_001")
    assert is_valid_short_id("a")
    assert not is_valid_short_id("")
    assert not is_valid_short_id("Bad-Id!")
    assert not is_valid_short_id("x" * 81)


@pytest.mark.asyncio
async def test_offload_text_uploads_and_persists():
    with (
        patch("core.artifact_store.store.put_object_bytes") as put,
        patch(
            "db_layer.artifact_link_store.artifact_link_short_id_exists",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "db_layer.artifact_link_store.create_artifact_link",
            new_callable=AsyncMock,
            return_value={"short_id": "art_diff_001"},
        ) as create,
    ):
        ref = await offload_text(
            "x" * 100,
            kind="diff",
            session_id="sess-1",
            tenant_id=1,
            source_path="step_results[0].data.patch",
        )
        put.assert_called_once()
        create.assert_awaited_once()
        assert ref["short_id"]
        assert ref["bytes"] == 100
        assert ref["kind"] == "diff"
        assert "console_path" in ref
        assert ref["session_id"] == "sess-1"
        assert "object_key" in ref


@pytest.mark.asyncio
async def test_extract_large_artifacts_rewrites_and_caps():
    # Use spaced text so preview redaction (base64 strip) does not wipe the body.
    big = ("line BBBB of a diff patch body\n") * 250  # ~7500 chars, not pure base64
    small = "s" * 10
    step_results = [
        {"operation_id": "get_activity", "status": "ok", "data": {"unidiffPatch": big, "note": small}},
        {"operation_id": "other", "status": "ok", "log": big},
    ]
    fake_ref = {
        "kind": "diff",
        "short_id": "art_diff_001",
        "bytes": len(big),
        "content_type": "text/markdown",
        "session_id": "s",
        "path": "p",
        "console_path": "/api/artifacts/session_gated/art_diff_001",
        "object_key": "1/s/diff-abc.md",
    }

    async def _fake_offload(content, **kwargs):
        return {**fake_ref, "path": kwargs.get("source_path"), "bytes": len(content.encode())}

    with patch("core.artifact_store.store.offload_text", side_effect=_fake_offload):
        slim, refs = await extract_large_artifacts(
            step_results,
            session_id="s",
            tenant_id=1,
            min_bytes=6000,
            max_artifacts=1,
        )

    assert len(refs) == 1
    # Cap stops at 1 — second large field left intact.
    assert isinstance(slim[0]["data"]["unidiffPatch"], str)
    assert is_offload_marker(slim[0]["data"]["unidiffPatch"])
    assert "art_diff_001" in slim[0]["data"]["unidiffPatch"]
    assert "--- preview ---" in slim[0]["data"]["unidiffPatch"]
    assert "BBBB" in slim[0]["data"]["unidiffPatch"]  # preview content
    assert "ONLY if the preview" in slim[0]["data"]["unidiffPatch"]
    assert slim[0]["data"]["note"] == small
    assert slim[1]["log"] == big  # not offloaded due to cap


@pytest.mark.asyncio
async def test_extract_adaptive_when_total_over_threshold_but_no_leaf():
    """Shaped Jules CSV ~5 KB under a high leaf threshold still offloads via adaptive path."""
    # Two leaves of 3000 each: no leaf >= 6000, but total 6000 triggers adaptive.
    a = "A" * 3000
    b = "B" * 3000
    step_results = [{"data": {"csv": a, "extra": b}}]
    n = 0

    async def _fake_offload(content, **kwargs):
        nonlocal n
        n += 1
        return {
            "kind": "data",
            "short_id": f"art_data_{n:03d}",
            "bytes": len(content.encode()),
            "content_type": "text/markdown",
            "session_id": "s",
            "path": kwargs.get("source_path"),
            "console_path": f"/api/artifacts/session_gated/art_data_{n:03d}",
            "object_key": "k",
        }

    with patch("core.artifact_store.store.offload_text", side_effect=_fake_offload):
        slim, refs = await extract_large_artifacts(
            step_results,
            session_id="s",
            tenant_id=1,
            min_bytes=6000,
            max_artifacts=8,
        )

    assert len(refs) >= 1
    assert is_offload_marker(slim[0]["data"]["csv"]) or is_offload_marker(
        slim[0]["data"]["extra"]
    )


@pytest.mark.asyncio
async def test_extract_keeps_inline_when_content_fits_preview():
    """Leaves that fully fit preview_chars stay inline — no MinIO hop."""
    body = "fit-in-preview " * 50  # ~750 chars, under default-ish 1500
    step_results = [{"data": {"note": body}}]
    called = {"n": 0}

    async def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("should not offload")

    with patch("core.artifact_store.store.offload_text", side_effect=_boom):
        slim, refs = await extract_large_artifacts(
            step_results,
            session_id="s",
            tenant_id=1,
            min_bytes=100,  # force candidacy
            max_artifacts=5,
            preview_chars=1500,
        )

    assert called["n"] == 0
    assert refs == []
    assert slim[0]["data"]["note"] == body


@pytest.mark.asyncio
async def test_marker_preview_zero_is_pointer_only():
    m = _marker("data.patch", "art_diff_9", 5000, content="x" * 100, preview_chars=0)
    assert is_offload_marker(m)
    assert "--- preview ---" not in m
    assert "fetch_artifact ONLY" in m


def test_marker_preview_redacts_credentials():
    """Inline previews must not leak sk-/ghp_/Bearer tokens into LLM context."""
    body = (
        "log start\n"
        "Authorization: Bearer sk-supersecrettokenvalue123456\n"
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
        "normal status: COMPLETED\n"
    )
    m = _marker("data.log", "art_log_1", len(body), content=body, preview_chars=500)
    assert "--- preview ---" in m
    assert "sk-supersecrettokenvalue123456" not in m
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in m
    assert "[redacted]" in m
    assert "COMPLETED" in m


@pytest.mark.asyncio
async def test_extract_large_artifacts_failure_leaves_field():
    big = "Z" * 8000
    step_results = [{"data": {"blob": big}}]

    async def _boom(*_a, **_k):
        raise RuntimeError("minio down")

    with patch("core.artifact_store.store.offload_text", side_effect=_boom):
        slim, refs = await extract_large_artifacts(
            step_results,
            session_id="s",
            tenant_id=1,
            min_bytes=1000,
            max_artifacts=5,
        )

    assert refs == []
    assert slim[0]["data"]["blob"] == big


@pytest.mark.asyncio
async def test_extract_skips_when_disabled_threshold():
    step_results = [{"data": "x" * 10000}]
    slim, refs = await extract_large_artifacts(
        step_results,
        session_id="s",
        tenant_id=1,
        min_bytes=0,
        max_artifacts=5,
    )
    assert slim is step_results
    assert refs == []
