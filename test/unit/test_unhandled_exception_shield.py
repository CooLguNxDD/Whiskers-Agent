"""UnhandledExceptionShieldMiddleware — the outermost HTTP catch-all.

A route/middleware exception that escapes the whole stack must come back as a
generic 500 JSON body, never a bare Uvicorn stack trace; a Starlette
HTTPException (deliberate, already carries a real status) must pass through
unshielded so Starlette's own machinery still handles it.
"""

from __future__ import annotations

import pytest
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from api.middleware import UnhandledExceptionShieldMiddleware


async def _raw_app(scope, receive, send):
    """A bare ASGI app with no Starlette ServerErrorMiddleware of its own, so the
    shield is exercised directly rather than seeing an already-caught exception."""
    path = scope.get("path", "")

    if path == "/boom":
        raise RuntimeError("kaboom")

    if path == "/http-exc":
        raise HTTPException(status_code=404, detail="not found")

    if path == "/boom-after-start":
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        })
        raise RuntimeError("boom mid-stream")

    response = JSONResponse({"status": "ok"})
    await response(scope, receive, send)


def _build_app():
    return TestClient(UnhandledExceptionShieldMiddleware(_raw_app))


def test_shield_converts_unhandled_exception_to_500_json():
    client = _build_app()
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == "internal_error"
    assert "kaboom" not in resp.text  # no raw exception text leaked


def test_shield_passes_through_http_exception():
    """The shield itself must not swallow a deliberate HTTPException into a
    generic 500 — it re-raises so an outer Starlette layer (present in the
    real app, absent from this bare test app) can convert it properly."""
    client = _build_app()
    with pytest.raises(HTTPException):
        client.get("/http-exc")


def test_shield_passes_through_success():
    client = _build_app()
    resp = client.get("/ok")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_shield_reraises_when_response_already_started(caplog):
    """A mid-stream failure can't be converted (headers already sent) — it
    must propagate rather than attempt a second http.response.start, and
    since Uvicorn's own error logging may be muted/unstructured, the shield
    must log it before re-raising rather than letting it vanish."""
    import logging

    client = _build_app()
    with caplog.at_level(logging.ERROR, logger="whiskers"):
        with pytest.raises(RuntimeError, match="boom mid-stream"):
            client.get("/boom-after-start")

    assert any("streaming response" in r.message and "RuntimeError" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_shield_passes_through_non_http_scopes():
    """websocket/lifespan scopes must reach the app unmodified."""
    calls = []

    async def inner_app(scope, receive, send):
        calls.append(scope["type"])

    mw = UnhandledExceptionShieldMiddleware(inner_app)
    await mw({"type": "lifespan"}, None, None)
    assert calls == ["lifespan"]
