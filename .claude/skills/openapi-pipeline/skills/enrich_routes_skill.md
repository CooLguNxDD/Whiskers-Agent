# Enrich Routes — Operation Object Generation Rules

Generate a valid **OpenAPI 3.0 Operation Object** for one HTTP route by reading
its handler source. Framework-agnostic: use `entry.adapter` to pick the scan
dialect.

Used by Mode B subagents. Pipeline orchestration:
[openapi-pipeline.md](../openapi-pipeline.md).

Skip this entire step when ingest marked routes `already_specified` (live or
file OpenAPI already has operations).

---

## Inputs

| Input | Description |
|-------|-------------|
| Manifest entry | `method`, `path`, `operationId`, `adapter`, `handlerFile` / `controllerFile` |
| Handler source | Repo-relative path in `handlerFile` |

---

## Workflow

### 1. Load the route

Read the batch/manifest entry. Resolve `handlerFile` or `controllerFile`
relative to the repo root. If missing, infer only from `method` + `path`.

### 2. Scan by adapter

**Path params** — every `:name` and `{name}` in `path` (and Flask `<name>` /
`<int:name>` already normalized to `{name}` by ingest).

**express**

- `req.params.*` — path
- `req.query.*` — query
- `req.body.*` — JSON body
- `req.headers.*` — header params when used for the contract
- Nested assigns (`const item = req.body.item`) → nested `requestBody` objects
- Trace local helpers / services the handler calls for validators (joi, zod,
  express-validator)

**fastapi**

- Function parameters: `Path`, `Query`, `Header`, `Cookie`, `Body`
- Pydantic models on the handler (fields, `Optional`, nested models, `Literal`)
- `response_model` / `status_code` for responses

**flask**

- `request.view_args` / path converters
- `request.args` — query
- `request.json` / `request.get_json()` / `request.form` — body
- marshmallow / pydantic schemas in the same module

### 3. Required vs optional

A field is **required** when the handler uses it with no null/default guard,
or a validator marks it required. Optional/nullable fields use
`anyOf: [{type: X}, {type: null}]` with `default: null` unless a realistic
default is known.

Do **not** put query parameters in `requestBody`.

### 4. Nested documents

- Arrays of objects: `items: {type: object, properties: {...}}` — never `items: {}`
- Multi-level nesting: document each level; `additionalProperties: true` when unsure
- Enums: `switch` / `Literal[...]` / `.includes(['a','b'])` → `"enum": ["a","b"]`
- Conditional requirements: keep out of top-level `required`, document in
  `description` (e.g. `"Required when status is published."`)

### 5. Operation Object shape

Exactly **one** key: the HTTP method in lowercase.

1. `operationId` — copy from the manifest
2. `summary` — one sentence from handler + description
3. `tags` — one domain tag from the first meaningful path segment
4. `parameters` — all path params (`in: path`, `required: true`) plus query/header
   params. Each needs `description` (with an `e.g.` snippet) and `default`.
5. `requestBody` — POST/PUT/PATCH only; nested schema from the scan
6. `responses`:
   - `200` always, with a JSON schema when the return shape is known
   - `400` always
   - `401` when `security` is non-empty
   - `403` when the handler enforces a role/permission check
7. `security` — `[{ "<schemeName>": [] }]` per manifest `security` entry
8. Inline JSON Schema only (no `$ref`). `additionalProperties: true` when incomplete.

**Path parameter example**

```json
{
  "name": "orderId",
  "in": "path",
  "required": true,
  "description": "Order identifier. e.g. 42",
  "schema": { "type": "integer" },
  "default": 42
}
```

**Nullable property example**

```json
"note": {
  "anyOf": [{"type": "string"}, {"type": "null"}],
  "default": null,
  "description": "Optional free-text note. e.g. null"
}
```

**POST example**

```json
{
  "post": {
    "operationId": "createOrder",
    "summary": "Create an order in the catalog.",
    "tags": ["orders"],
    "parameters": [
      {
        "name": "storeId",
        "in": "path",
        "required": true,
        "description": "Store that owns the order. e.g. 1",
        "schema": { "type": "integer" },
        "default": 1
      }
    ],
    "requestBody": {
      "required": true,
      "content": {
        "application/json": {
          "schema": {
            "type": "object",
            "properties": {
              "sku": {
                "type": "string",
                "description": "Catalog SKU. e.g. \"sku-100\"",
                "default": "sku-100"
              },
              "qty": {
                "type": "integer",
                "description": "Units to order. e.g. 2",
                "default": 2
              }
            },
            "additionalProperties": true
          }
        }
      }
    },
    "responses": {
      "200": { "description": "Success" },
      "400": { "description": "Bad request" },
      "401": { "description": "Unauthorized" }
    },
    "security": [{ "BearerAuth": [] }]
  }
}
```

Every parameter and requestBody property needs `description` + `default`
(not `example`). `openapi_to_mcp.py` copies `default` onto MCP `inputSchema`.

---

## After enrichment

Merge batches, then run `build_openapi.py` → `openapi_to_mcp.py` →
`split_mcp_tools.py` as in [openapi-pipeline.md](../openapi-pipeline.md).
