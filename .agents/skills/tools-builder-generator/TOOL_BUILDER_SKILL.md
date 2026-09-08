---
name: tools-builder-generator
description: 'CODE PATTERN SKILL — MCP tool authoring patterns and the YAML-to-Python code generator. USE FOR: writing new @mcp.tool() functions, required-field validation, YAML config format, running the generator. DO NOT USE FOR: OAuth or server setup (use other skills).'
---

# MCP Tools Builder & Generator

## Tool Authoring Pattern

```python
from core.context import mcp
from plugins.core_mcp_plugin.plugin_config import API_URL, plugin_auth_headers
from utils import safe_api_call
import requests, logging
from typing import Any

logger = logging.getLogger("whiskers")

async def _auth_headers(): return await _get_registry().get_auth_headers("my_plugin")

@mcp.tool()
async def my_tool(required_param: str, optional_param: str | None = None,
                  project_id: int | None = None) -> dict[str, Any]:
    """Docstring becomes the MCP tool description."""
    # 1. Validate required fields
    missing = []
    if not required_param or not required_param.strip():
        missing.append("required_param (description)")
    if missing:
        return {"status": "error", "error": "missing_required_fields",
                "missing_fields": missing, "message": f"Please provide: {', '.join(missing)}"}
    # 2. Await auth headers BEFORE lambda (not inside — returns coroutine)
    headers = await _auth_headers()
    url = f"{API_URL}/api/v1/projects/{project_id or PROJECT_ID}/resource"
    return safe_api_call(
        lambda: requests.post(url, json={"key": required_param}, headers=headers, timeout=30),
        lambda resp: resp.json(),
        context=f"my_tool: {required_param}",
    )
```

**For static read/list tools with response shaping:**
```python
from plugins.core_mcp_plugin.config_loader import with_response_hints

@mcp.tool(description=with_response_hints("Get items for a resource."))
async def get_records(user_id: int, _response_shape: dict[str, Any] | None = None) -> dict:
    headers = await _auth_headers()
    return safe_api_call(
        lambda: requests.get(url, headers=headers, timeout=30),
        lambda resp: resp.json(),
        context=f"get_records {user_id}",
        tool_name="get_records",
        shape=_response_shape,
    )
```

### Critical Rules
1. Validate required fields before API call — return `missing_required_fields` dict, never send bad data
2. Never generate fake/random record data — ask the user
3. All HTTP via `safe_api_call` — never bare `requests.*`
4. Return `dict[str, Any]` always
5. Await `_auth_headers()` before lambda — inline call returns coroutine, breaks headers iteration
6. Add `project_id` override to every project-scoped endpoint tool

---

## Registration Checklist

1. `MCPTools/new_module.py` — tool function(s) with `@mcp.tool(tags={"core_mcp_plugin", ...})`
2. `MCPTools/__init__.py` — `from . import new_module  # noqa: F401`
3. CLAUDE.md "Available Tools" — add entry

(`_log_startup()` auto-introspects tools, and the plugin's `on_load()` lifecycle automatically registers them to the `route_registry` on boot).

---

## Confirmation-Required Bulk Operations

For safe bulk operations (e.g., sending messages to many resources), use a two-step confirmation pattern:

1. **Phase 1 (No payload supplied)**: The tool calculates the impact, generates drafts, and returns `status: "confirmation_needed"`.
2. **Phase 2 (Payload supplied)**: The tool receives the confirmed payload/template and executes the operation.

```python
@mcp.tool()
async def bulk_send_messages(template_id: int, record_ids: list[int],
                             confirmation_payload: dict | None = None) -> dict:
    if not confirmation_payload:
        # Step 1: Generate drafts
        drafts = await _generate_drafts(template_id, record_ids)
        return {
            "status": "confirmation_needed",
            "message": f"Prepared {len(drafts)} messages. Please confirm sending.",
            "drafts": drafts
        }

    # Step 2: Execute send
    return await _execute_bulk_send(confirmation_payload)
```

---

## YAML Code Generator

```bash
python Tools/tools_generator.py --config tools_config.yaml --output MCPTools/lab_results.py --schema
python Tools/tools_generator.py --interactive
python Tools/tools_generator.py --example
```

`--output` paths may be nested; generators use `Tools/_write_utils.py` (`write_text_safe` / `emit_generated`) to create parent directories and exit with a clear error on permission failures. Credential scaffolding (`--scaffold-credentials`) reports per-plugin write failures.

### YAML Config Format

```yaml
module_doc: "Lab result tools."

tools:
  - name: get_lab_results
    description: "Get related items for a resource."
    method: GET
    endpoint: "/api/v1/projects/{pid}/records/{user_id}/items"
    log_msg: "Getting related items for record {user_id}"
    params:
      required:
        - { name: user_id, type: int, description: "Resource user ID." }
      optional:
        - { name: from_date, type: "str | None", default: "None", description: "Filter from date (YYYY-MM-DD)." }
        - { name: project_id, type: "int | None", default: "None", description: "Override project ID." }

  - name: create_lab_result
    description: "Record a new lab result."
    method: POST
    endpoint: "/api/v1/projects/{pid}/records/{user_id}/items"
    params:
      required:
        - { name: user_id, type: int, description: "Resource user ID." }
        - { name: test_name, type: str, description: "Name of the lab test." }
        - { name: result_value, type: str, description: "Test result value." }
      optional:
        - { name: project_id, type: "int | None", default: "None", description: "Override project ID." }
    payload_fields:
      - { key: testName, value: test_name }
      - { key: resultValue, value: result_value }
```

Generator produces: `@mcp.tool()` decorated function + typed params + docstring + required-field validation + `safe_api_call` with correct HTTP method.

`--schema` flag also prints the JSON schema definitions.

---

## RouteRegistry (Dynamic Graph Awareness)

Tools registered via `@mcp.tool()` are dynamically collected at startup and contributed to the central `RouteRegistry`. The dynamic LangGraph orchestrator relies on the `RouteRegistry` and `db_layer/embeddings` to dynamically map natural language user queries to these tools, eliminating the need for hardcoded list bindings or `langgraph_flows/tool_schemas.py` files.

---

## Dropped
- Files reference table (obvious from CLAUDE.md project structure)
- "What the Generator Produces" prose section (implied by YAML format)
- `format_required_fields_summary()` explanation (implementation detail)
- Verbose required-field validation example (consolidated into tool pattern)
- Legacy `langgraph_flows/tool_schemas.py` and `TOOL_SCHEMAS` references.
