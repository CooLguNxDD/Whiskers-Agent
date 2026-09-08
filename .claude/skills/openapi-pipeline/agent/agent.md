# Subagent Prompt Template — Batch Enrichment (Component M2)

Canonical prompt for Mode B / Component M2. Each subagent processes one
`openapi/AITemp/batch_NN.json` and writes `openapi/AITemp/enriched_batch_NN.json`.

Substitute `NN` and `TOTAL` before dispatch. Do not send this file verbatim.

---

## Template Variables

| Placeholder | Description |
|-------------|-------------|
| `NN` | Zero-padded batch index (`00`, `01`, …) |
| `TOTAL` | Number of route entries in this batch |

---

## Prompt Template

```
You are enriching HTTP API routes for OpenAPI documentation.

### Context

- Repo root: the current working directory
- Batch file: openapi/AITemp/batch_NN.json   (TOTAL routes)
- Output file: openapi/AITemp/enriched_batch_NN.json
- Generation rules: .claude/skills/openapi-pipeline/skills/enrich_routes_skill.md

### Task — one entry at a time

For EACH entry in openapi/AITemp/batch_NN.json:

1. **Read the handler source.**
   Use `handlerFile` or `controllerFile` (repo-relative). If missing, proceed
   from `operationId` + `path` + `method` alone.

2. **Scan using entry.adapter.**
   - express: req.body / req.query / req.params; nested body assigns; local validators
   - fastapi: Path/Query/Body/Depends, Pydantic models, response_model
   - flask: request.json / request.args / request.view_args / request.form
   Path parameters may be `:name` or `{name}`.

3. **Produce an `openapi_path_item` object.**
   Exactly ONE key — the HTTP method in lowercase — whose value is a valid
   OpenAPI 3.0 Operation Object.

   Follow `.claude/skills/openapi-pipeline/skills/enrich_routes_skill.md`.
   Summary:

   - `operationId` — copy from the manifest
   - `summary` — one sentence
   - `tags` — one domain tag from the path (e.g. `orders`, `users`)
   - `parameters` — all path params (`in: path`, `required: true`) and query
     params (`in: query`). Do not put query params in requestBody.
     Each parameter needs `description` (with `e.g.`) and `default`.
   - `requestBody` — POST/PUT/PATCH only; nested objects when the handler
     splits the body
   - `responses` — always `200` and `400`; `401` when `security` is non-empty;
     `403` when the handler checks a role/permission
   - `security` — `[{ "<schemeName>": [] }]` per manifest `security` list
   - Inline JSON Schema only; `additionalProperties: true` when uncertain

4. **Attach the result.**
   Set `openapi_path_item` on the original entry (merge, do not drop fields).

### Output

Write `openapi/AITemp/enriched_batch_NN.json` as a JSON array of exactly TOTAL
objects (same order as input).

- Do NOT write any other file.
- Do NOT return a summary — the file on disk is the result.

### Acceptance criteria

- Array length == TOTAL
- Every entry has `openapi_path_item` with exactly one HTTP-method key
- All path parameters in the URL appear in `parameters`
- No query params buried in `requestBody`
- `operationId` values unchanged
- Every path/query parameter and requestBody property has `description` + `default`
```

---

## Root agent dispatch

For each `batch_NN.json`, skip if `enriched_batch_NN.json` exists. Fill `NN` /
`TOTAL`, spawn a subagent, then check array length and `openapi_path_item`.
Delete a bad output file and re-dispatch that batch only.

---

## Related Files

| File | Role |
|------|------|
| `.claude/skills/openapi-pipeline/openapi-pipeline.md` | Pipeline orchestration |
| `.claude/skills/openapi-pipeline/skills/enrich_routes_skill.md` | Operation Object rules |
| `.claude/skills/openapi-pipeline/script/slice_batches.py` | Component M1 |
| `.claude/skills/openapi-pipeline/script/merge_batches.py` | Component M3 |
