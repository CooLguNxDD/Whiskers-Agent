---
name: error-handling-wrapper
description: '**CODE PATTERN SKILL** — Defensive API call wrapper and structured error responses. USE FOR: understanding safe_api_call, error dict format, adding new API calls, debugging HTTP failures, extending error extraction. DO NOT USE FOR: tool registration (use tools-builder-generator skill), OAuth token errors (use oauth-handler skill).'
---

# Error Handling Wrapper

## Overview

All Whiskers Agent tools use a **defensive API call wrapper** (`safe_api_call`) that converts HTTP failures into structured error dicts. The LLM always receives a meaningful, parseable response — never a raw exception or cryptic status code.

## File

`utils/api_utils.py` — the sole location for API error handling logic.

## Core Function: `safe_api_call`

```python
async def safe_api_call(
    make_request: Callable[[], requests.Response],
    on_success: Callable[[requests.Response], T],
    context: str = "",
    **extra_error_fields: Any,
) -> T | dict[str, Any]:
```

### Parameters

| Param | Type | Purpose |
|---|---|---|
| `make_request` | `Callable[[], Response]` | Zero-arg callable that performs the HTTP request |
| `on_success` | `Callable[[Response], T]` | Transforms successful response into the tool's return value |
| `context` | `str` | Human-readable operation label for error messages |
| `**extra_error_fields` | `Any` | Extra k/v pairs merged into the error dict |

### Return Value

- **On HTTP 2xx**: returns `on_success(resp)` (typically `resp.json()`)
- **On HTTP error**: returns a structured error dict
- **On network/timeout error**: returns a structured error dict

### Usage Pattern

```python
return await safe_api_call(
    lambda: requests.post(url, json=payload, headers=_auth_headers(), timeout=30),
    lambda resp: resp.json(),
    context=f"Creating record {given_name} {last_name}",
    record_name=f"{given_name} {last_name}",
)
```

Or with a named success handler for post-processing:

```python
def _on_success(resp: requests.Response) -> dict[str, Any]:
    result = resp.json()
    logger.info(f"✓ Resource created (id={result.get('id')})")
    return result

return await safe_api_call(
    lambda: requests.post(url, json=payload, headers=_auth_headers(), timeout=30),
    _on_success,
    context=f"Creating record {given_name} {last_name}",
)
```

## Error Dict Structure

Every error response follows this shape:

```python
{
    "status": "error",
    "error": "api_error",           # or "network_error", "timeout_error"
    "http_status": 400,             # only for API errors
    "message": "Context: extracted error message",
    # ...any extra_error_fields
}
```

### Error Extraction (`_extract_api_error`)

The extractor tries these strategies in order:

1. Parse response body as JSON.
2. Check fields: `message`, `error`, `errors`, `detail`, `msg`.
3. If `errors` is a list, extract the first item's `message` field.
4. Fall back to raw `resp.text[:300]`.
5. Last resort: `"HTTP {status_code}"`.

```python
_ERROR_FIELDS = ("message", "error", "errors", "detail", "msg")
```

## Two-Layer Error Handling

The codebase uses **two layers** of validation:

### Layer 1: Required-Field Validation (in the tool function)

Before any API call, validate that required fields are present. This catches user omissions early:

```python
if missing:
    return {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["given_name", "phone"],
        "message": "Please provide: given_name, phone",
    }
```

### Layer 2: API Error Wrapping (in `safe_api_call`)

If the backend rejects the request (400, 404, 500, etc.), `safe_api_call` extracts a readable error message:

```python
{
    "status": "error",
    "error": "api_error",
    "http_status": 400,
    "message": "Creating record: Phone number already in use",
}
```

## Helper: `api_error_dict`

For cases where you need to build an error dict manually (rare). Note that `api_error_dict` automatically truncates the `raw_body` string to a maximum of 2000 characters (appending `…` if truncated) to avoid bloating logs and LLM contexts:

```python
from .api_utils import api_error_dict

error = api_error_dict(
    resp,
    context="Updating appointment",
    appointment_id=appt_id,
)
```

## Rules

1. **Never let exceptions propagate to the MCP client.** Always catch and return a dict.
2. **Always pass `context`** — it makes error messages actionable.
3. **Use `extra_error_fields`** to include identifiers (record name, user ID) in error dicts.
4. **Use `timeout=30`** on all `requests.*` calls.
5. **Don't duplicate error handling** — `safe_api_call` is the single point. Don't wrap it in try/except.

## Network & Timeout Errors

`safe_api_call` also handles `requests.ConnectionError` and `requests.Timeout`:

```python
{
    "status": "error",
    "error": "network_error",
    "message": "Context: Connection refused",
}
```

```python
{
    "status": "error",
    "error": "timeout_error",
    "message": "Context: Request timed out after 30s",
}
```

## Proxy Tools Are a Different Layer: They Raise, Not Return

`core/proxy_tools/proxy_tool_loader.py::make_proxy_callable` is the fast-path callable for a mounted
proxy's tools (not a plugin's own `@mcp.tool()` function using `safe_api_call` above). A failure there
**raises** `fastmcp.exceptions.ToolError` (via `_attach_error_meta`, same `http_status`/`api_error_type`
stamp `_handle_api_exception` uses) instead of returning `{"status": "error", ...}`. Returning the dict
made FastMCP report the call as a 200-OK success to the client/LLM, which then treated a failure as a
usable result.

Every caller that dispatches through `execute_operation` (`core/route_registry/execute.py`) sees this
as `ExecuteError(code="invoke_failed", status=500)` — the callable already ran and raised. **Never**
retry that call through a fast-path fallback on `code == "invoke_failed"`; the callable already
executed against the upstream, so a retry duplicates the side effect. `core_graph/agent_loop/runner.py
::_dispatch_tool` and `plugins/portfolio_plugin/discovery/sources.py::invoke_proxy` both guard this
explicitly — mirror that guard in any new dispatcher that falls back from `execute_operation` to
`route_registry.fast_path_callable`.
