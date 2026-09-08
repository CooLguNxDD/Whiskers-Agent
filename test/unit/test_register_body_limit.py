"""RegisterScopeSanitizerMiddleware must reject oversized POST /register bodies."""

import pytest

from api.middleware import RegisterScopeSanitizerMiddleware
from utils.server_config import MAX_REGISTER_BODY_BYTES


@pytest.mark.asyncio
async def test_register_body_over_limit_returns_413():
    """Oversized DCR bodies are rejected before buffering completes."""
    captured = {}

    async def _app(scope, receive, send):
        captured["reached_app"] = True

    middleware = RegisterScopeSanitizerMiddleware(_app)

    async def _receive():
        chunk = b"x" * (MAX_REGISTER_BODY_BYTES + 1)
        yield {"type": "http.request", "body": chunk, "more_body": False}

    receive_iter = _receive()

    async def receive():
        return await receive_iter.__anext__()

    status = {}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/register",
        "headers": [],
    }

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("api.middleware.oauth_provider", object())
        await middleware(scope, receive, send)

    assert status.get("code") == 413
    assert "reached_app" not in captured