"""
Shared helper functions for API error response formatting.
"""

import logging
from typing import Any

from starlette.responses import JSONResponse

logger = logging.getLogger("whiskers")


def safe_error_response(
    exc: Exception,
    *,
    status_code: int = 500,
    code: str = "internal_error",
    message: str = "An internal server error occurred.",
    log_ctx: str = "Unhandled exception in API endpoint",
) -> JSONResponse:
    """
    Log full exception details internally and return a sanitized JSONResponse.

    Prevents raw str(e) exception leaks in 500-class API responses.
    """
    if log_ctx:
        logger.exception("%s: %s", log_ctx, exc)
    else:
        logger.exception("API exception: %s", exc)

    return JSONResponse(
        status_code=status_code,
        content={
            "error": code,
            "message": message,
        },
    )


def tool_error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    """Return the uniform ``{"status": "error", ...}`` payload for MCP tool returns.

    Distinct from :func:`safe_error_response`, which builds an HTTP JSONResponse.
    Any ``extra`` keys are merged in for tool-specific detail; keys that collide
    with the reserved ``status``/``error``/``message`` fields are ignored (logged),
    never overwritten.
    """
    out: dict[str, Any] = {"status": "error", "error": error, "message": message}
    for key, value in extra.items():
        if key in out:
            logger.warning("tool_error: ignoring reserved key %r in extra", key)
            continue
        out[key] = value
    return out
