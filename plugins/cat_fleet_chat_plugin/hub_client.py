"""Async client for the Cat Fleet hub.

The hub URL is manifest settings / ``CAT_FLEET_HUB_URL``, resolved here.
Hub URLs pass through the SSRF-safe transport. Local hub connections require
``CAT_ALLOW_LOCAL_PROXIES=1``.

Inside Docker the default target is ``http://host.docker.internal:8787``
(compose already maps that name). A hub bound only to ``127.0.0.1`` is not
reachable from the container: bind ``0.0.0.0``, set ``CAT_FLEET_TOKEN``, and
set the same value as ``CAT_FLEET_HUB_TOKEN`` on the plugin. On the host,
set ``CAT_FLEET_HUB_URL=http://127.0.0.1:8787``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

from core.proxy.ssrf_safety import _is_safe_url, _safe_async_client
from plugins.cat_fleet_chat_plugin.plugin_config import SETTINGS
from utils.api_utils import safe_api_call

logger = logging.getLogger("whiskers.cat_fleet_chat")

# Dense CSV unless the caller passes `_response_shape`. Merged under the
# caller dict so an explicit response_format still wins.
_CSV_SHAPE: dict[str, Any] = {"response_format": "csv"}

_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
_DEFAULT_HUB = "http://host.docker.internal:8787"
_client: httpx.AsyncClient | None = None


def hub_url() -> str:
    """Resolved base URL. Empty interpolation falls back to the Docker host name."""
    raw = str(SETTINGS.get("hub_url") or "")
    expanded = _PLACEHOLDER.sub(lambda match: os.environ.get(match.group(1), ""), raw).strip()
    if not expanded:
        expanded = os.environ.get("CAT_FLEET_HUB_URL", _DEFAULT_HUB).strip()
    return (expanded or _DEFAULT_HUB).rstrip("/")


def hub_token() -> str:
    """Bearer token read at call time. Never logged."""
    return os.environ.get("CAT_FLEET_HUB_TOKEN", "").strip()


def _setting_int(name: str, default: int) -> int:
    try:
        return int(SETTINGS.get(name, default))
    except (TypeError, ValueError):
        return default


def normal_timeout() -> float:
    return float(_setting_int("request_timeout_s", 10))


def clamp_wait(requested: int | None) -> int:
    """Hub wait, clamped so the HTTP client outlives it by 10 seconds."""
    default = _setting_int("wait_timeout_default_s", 60)
    maximum = min(_setting_int("wait_timeout_max_s", 300), 300)
    try:
        seconds = default if requested is None else int(requested)
    except (TypeError, ValueError):
        seconds = default
    return max(1, min(seconds, maximum))


def _headers() -> dict[str, str]:
    token = hub_token()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def open_client() -> httpx.AsyncClient:
    """One process-wide client. Re-opened if a previous unload closed it."""
    global _client
    if _client is None or _client.is_closed:
        _client = _safe_async_client(timeout=httpx.Timeout(normal_timeout()))
    return _client


async def close_client() -> None:
    global _client
    client = _client
    _client = None
    if client is not None and not client.is_closed:
        await client.aclose()


def _map_error(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("raw_body") or ""
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
            err = parsed["error"]
            mapped: dict[str, Any] = {
                "status": "error",
                "error": err.get("code") or "api_error",
                "message": err.get("message") or result.get("message") or "hub request failed",
                "http_status": result.get("status_code"),
            }
            if err.get("details"):
                mapped["details"] = err["details"]
            return mapped
    return {
        "status": "error",
        "error": result.get("error") or "request_failed",
        "message": result.get("message") or "hub request failed",
        "http_status": result.get("status_code"),
    }


async def request_json(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float | None = None,
    tool_name: str = "",
    shape: dict[str, Any] | None = None,
    raw: bool = False,
) -> Any:
    """Call the hub. Success payloads are dense CSV unless ``shape`` overrides that.

    ``raw=True`` returns the hub's JSON unshaped. Use it when the plugin reads
    the result itself: shaping flattens and renames keys (``attachment`` →
    ``attachment_*``) even when ``response_format`` is json.
    """
    client = await open_client()
    url = f"{hub_url()}{path}"
    if not await _is_safe_url(url):
        return {"status": "error", "error": "unsafe_url", "message": "hub URL is not allowed"}
    request_timeout = httpx.Timeout(timeout if timeout is not None else normal_timeout())

    async def make_request() -> httpx.Response:
        return await client.request(
            method,
            url,
            params={k: v for k, v in (params or {}).items() if v is not None},
            json=body,
            headers=_headers(),
            timeout=request_timeout,
        )

    result = await safe_api_call(
        make_request,
        lambda response: response.json(),
        context=tool_name or path,
        operation_id=tool_name or None,
        shape={**_CSV_SHAPE, **(shape or {})},
        raw_response=raw,
        tool_name=tool_name,
        plugin_id="cat_fleet_chat_plugin",
        raise_tool_error=False,
    )
    if isinstance(result, dict) and result.get("status") == "error":
        mapped = _map_error(result)
        logger.info("%s failed: %s", tool_name or path, mapped.get("error"))
        return mapped
    return result
