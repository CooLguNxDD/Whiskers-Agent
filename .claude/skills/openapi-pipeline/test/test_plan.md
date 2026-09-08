# Test Plan: Generated MCP Tools from a Generic API

Covers tools produced by the OpenAPI pipeline for an arbitrary backend (live
spec or source scan). Goal: MCP calls reach the origin API with the right
method, path, query/body split, and auth.

## Target Tools (example)

Replace these with operations from the generated `mcp-tools-ai.json`.

- `getOrder` — GET `/v1/orders/{orderId}`
- `createOrder` — POST `/v1/orders`

## Setup

1. Origin API running (the same base URL written into `openapi.json` `servers`).
2. A valid Bearer token if the spec declares `BearerAuth`.
3. Seed data for the example resources.

## Test Cases

### 1. Tool call: `getOrder`

- **Input:** `{"orderId": 1}`
- **Success:** JSON payload for that order (HTTP 200).
- **Failures:** 401 without token; 404 unknown id; 400 missing `orderId`.

### 2. Tool call: `createOrder`

- **Input:** body fields from the generated `inputSchema` (path params vs JSON
  body must match `_meta` / OpenAPI `in` values).
- **Success:** 200/201 with the created resource.
- **Failures:** 401 without token; 400 when a required field is omitted.

## Execution

Connect an MCP client to the generated server, invoke each tool with the
inputs above, and confirm the HTTP method and path against the origin access
log.
