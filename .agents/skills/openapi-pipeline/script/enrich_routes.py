#!/usr/bin/env python3
"""
enrich_routes.py
================
Step 2 of the OpenAPI pipeline.

Reads route-manifest.json, calls the Claude API for each route to produce
an OpenAPI 3.0 path object, and writes enriched-manifest.json.

Requires:
    pip install anthropic

Usage
-----
    # Enrich a single route by index
    python openapi/enrich_routes.py --index 0

    # Enrich a range of routes (inclusive)
    python openapi/enrich_routes.py --from 0 --to 49

    # Enrich all routes (skips already-enriched by default)
    python openapi/enrich_routes.py --all

    # Force re-enrichment (ignore existing enriched-manifest.json entries)
    python openapi/enrich_routes.py --all --force

    # Control concurrency (default: 3)
    python openapi/enrich_routes.py --all --concurrency 5

Defaults (relative to repo root):
    --manifest       openapi/route-manifest.json
    --out            openapi/enriched-manifest.json
    --handlers-root  <repo root>
    --model          claude-sonnet-4-6
    --concurrency    3
    --retries        3
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Source hash helper
# ---------------------------------------------------------------------------

def _hash_source(text: str) -> str:
    """Return a short sha256 hex digest of the controller source text."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# Prompt template (mirrors enrich_routes_skill.md)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = "You are a JSON-only OpenAPI 3.0 generator. Output ONLY valid JSON — no markdown, no explanation."

DIALECT_HINTS = {
    "express": (
        "Express/Koa. Scan req.body, req.query, req.params, req.headers, and "
        "express-validator / joi / zod schemas in the same file."
    ),
    "fastapi": (
        "FastAPI/Starlette. Scan function signatures, Query/Path/Body/Depends, "
        "Pydantic models, and response_model."
    ),
    "flask": (
        "Flask. Scan request.json, request.args, request.view_args, request.form, "
        "and marshmallow/pydantic schemas."
    ),
    "spec": "Source of truth is already OpenAPI; only fill gaps, do not invent fields.",
    "live": "Source of truth is already OpenAPI; only fill gaps, do not invent fields.",
}

USER_PROMPT_TEMPLATE = """\
Analyse this backend handler and produce a single OpenAPI 3.0 "path item" object
for the operation described below.

## Route metadata
- Method:       {method}
- Path:         {path}
- operationId:  {operation_id}
- Description:  {description}
- Security:     {security}
- Adapter:      {adapter}

## Handler dialect
{dialect_hint}

Path parameters may use Express `:name` or OpenAPI `{{name}}` — document both as
OpenAPI path parameters.

## Handler source
```
{controller_source}
```

## Output rules
1. Produce a JSON object with exactly ONE key: the HTTP method in lowercase (e.g. "get").
2. The value must be a valid OpenAPI 3.0 Operation Object.
3. Include:
   - "operationId": "{operation_id}"
   - "summary": one-sentence description inferred from the handler and description
   - "tags": [domain tag inferred from the path]
   - "parameters": path params and query params extracted from the handler (use OpenAPI Parameter Object schema)
   - "requestBody": if the method is POST/PUT/PATCH — infer shape from body usage in the handler
   - "responses":
       "200": {{ "description": "Success", "content": {{ "application/json": {{ "schema": {{ ... }} }} }} }}
       "400": {{ "description": "Bad request" }}
       "401": {{ "description": "Unauthorized" }}  (if security is non-empty)
       "403": {{ "description": "Forbidden" }}     (if security includes role checks)
   - "security": map each entry in {security} to [{{ "<schemeName>": [] }}]
4. For schema shapes, use JSON Schema inline (no $ref) with best-effort field names and types
   inferred from the handler source. Use "additionalProperties: true" when uncertain.
5. Path parameters that appear in {path} as :paramName or {{paramName}} must appear in
   "parameters" with "in": "path", "required": true.
6. **Nested / embedded document rules** (CRITICAL — do NOT flatten or omit):
   a. Arrays of embedded objects — when a field holds an array of sub-documents
      (each sub-document has its own known fields), use the full items schema:
      {{ "type": "array", "items": {{ "type": "object", "properties": {{ ... }}, "additionalProperties": true }} }}
      Never collapse to {{ "items": {{}} }} — always document the sub-document fields.
   b. Nullable / optional fields — always use anyOf: [{{type: X}}, {{type: null}}] with default: null.
      Never omit the null variant; bare optional fields without null will be typed as required.
   c. Multi-level nesting — trace all levels (3+). Each nested object level must have
      its own "properties" sub-object; use "additionalProperties": true at every level.
   d. Mixed objects — an object property may have both scalar fields and array-of-object
      fields at the same level; document all of them.
   e. Body-splitting — if the handler assigns `const x = req.body.x` / `body.x` /
      a Pydantic nested model, document `x` as a nested object and trace callees.

Now produce the JSON for the route above. Output ONLY the JSON object.
"""


# ---------------------------------------------------------------------------
# Controller source loader
# ---------------------------------------------------------------------------

def load_controller_source(controller_file: str | None, repo_root: Path) -> str:
    """Load handler source. Path is repo-relative; no framework-specific prefix."""
    if not controller_file:
        return "// handler source not found"
    normalized = controller_file.replace("\\", "/")
    candidates = [
        repo_root / normalized,
        Path(normalized),
    ]
    for path in candidates:
        if path.exists():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return f"// could not read {controller_file}"
    return f"// could not read {controller_file}"


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

async def enrich_one(
    route: dict,
    repo_root: Path,
    client: anthropic.AsyncAnthropic,
    model: str,
    semaphore: asyncio.Semaphore,
    retries: int,
) -> dict:
    """Return route dict with 'openapi_path_item' key added."""
    handler = route.get("handlerFile") or route.get("controllerFile")
    controller_src = load_controller_source(handler, repo_root)
    adapter = str(route.get("adapter") or "auto")
    dialect_hint = DIALECT_HINTS.get(adapter, DIALECT_HINTS["express"])

    prompt = USER_PROMPT_TEMPLATE.format(
        method=route["method"],
        path=route["path"],
        operation_id=route["operationId"],
        description=route.get("description", ""),
        security=json.dumps(route.get("security", [])),
        adapter=adapter,
        dialect_hint=dialect_hint,
        controller_source=controller_src,
    )

    last_err = None
    for attempt in range(1, retries + 1):
        async with semaphore:
            try:
                message = await client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = message.content[0].text.strip()

                # Strip markdown code fences if the model wraps its output
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

                path_item = json.loads(raw)
                return {**route, "openapi_path_item": path_item, "_source_hash": _hash_source(controller_src)}

            except json.JSONDecodeError as e:
                last_err = f"JSON parse error: {e}"
                print(f"  WARN [{route['operationId']}] attempt {attempt}: {last_err}", file=sys.stderr)
            except anthropic.APIError as e:
                last_err = f"API error: {e}"
                print(f"  WARN [{route['operationId']}] attempt {attempt}: {last_err}", file=sys.stderr)
                if attempt < retries:
                    await asyncio.sleep(2 ** attempt)  # exponential back-off

    print(f"  ERROR [{route['operationId']}] all {retries} attempts failed: {last_err}", file=sys.stderr)
    return {**route, "openapi_path_item": {}, "_source_hash": _hash_source(controller_src)}


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

async def enrich_batch(
    routes: list[dict],
    existing: dict[str, dict],
    repo_root: Path,
    model: str,
    concurrency: int,
    retries: int,
    force: bool,
) -> list[dict]:
    """Enrich a list of routes, skipping already-done ones unless --force."""
    client = anthropic.AsyncAnthropic()
    semaphore = asyncio.Semaphore(concurrency)

    tasks = []
    skipped = []

    for route in routes:
        op_id = route["operationId"]
        existing_entry = existing.get(op_id)

        if route.get("already_specified") and route.get("openapi_path_item") and not force:
            skipped.append(route)
            continue

        if existing_entry and existing_entry.get("openapi_path_item"):
            if force:
                # --force: always re-enrich
                tasks.append(enrich_one(route, repo_root, client, model, semaphore, retries))
            else:
                # Check if the controller source has changed since last enrichment
                handler = route.get("handlerFile") or route.get("controllerFile")
                current_src = load_controller_source(handler, repo_root)
                current_hash = _hash_source(current_src)
                stored_hash  = existing_entry.get("_source_hash", "")
                if current_hash == stored_hash:
                    skipped.append(existing_entry)
                else:
                    print(
                        f"  [{op_id}] source changed (hash mismatch) — re-enriching …",
                        file=sys.stderr,
                    )
                    tasks.append(enrich_one(route, repo_root, client, model, semaphore, retries))
        else:
            tasks.append(enrich_one(route, repo_root, client, model, semaphore, retries))

    if skipped:
        print(f"  Skipping {len(skipped)} already-enriched route(s). Use --force to re-enrich.")

    results = []
    if tasks:
        total = len(tasks)
        done = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            done += 1
            status = "ok" if result.get("openapi_path_item") else "FAILED"
            print(f"  [{done}/{total}] {status}  {result['method']:6s}  {result['path']}")
            results.append(result)

    return skipped + results


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def index_by_operation_id(entries: list[dict]) -> dict[str, dict]:
    return {e["operationId"]: e for e in entries}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    repo_root = Path(__file__).resolve().parents[4]

    parser = argparse.ArgumentParser(description="Enrich route-manifest.json → enriched-manifest.json via Claude API")
    parser.add_argument("--manifest",     default=str(repo_root / "openapi/route-manifest.json"))
    parser.add_argument("--out",          default=str(repo_root / "openapi/enriched-manifest.json"))
    parser.add_argument(
        "--handlers-root",
        default=str(repo_root),
        help="Repo root used to resolve handlerFile / controllerFile paths",
    )
    parser.add_argument("--model",        default="claude-sonnet-4-6")
    parser.add_argument("--concurrency",  type=int, default=3)
    parser.add_argument("--retries",      type=int, default=3)
    parser.add_argument("--force",        action="store_true", help="Re-enrich even if already present")

    # Selection modes (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all",   action="store_true",  help="Enrich all routes")
    group.add_argument("--index", type=int,             help="Enrich a single route by index")
    group.add_argument("--from",  dest="from_idx", type=int, help="Start index (inclusive, use with --to)")

    parser.add_argument("--to", dest="to_idx", type=int, help="End index (inclusive, use with --from)")

    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    out_path      = Path(args.out)

    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    all_routes = load_manifest(manifest_path)
    existing   = index_by_operation_id(load_manifest(out_path))

    # Select the target slice
    if args.all:
        target = all_routes
        label  = f"all {len(target)} routes"
    elif args.index is not None:
        if args.index >= len(all_routes):
            print(f"ERROR: index {args.index} out of range (manifest has {len(all_routes)} routes)", file=sys.stderr)
            sys.exit(1)
        target = [all_routes[args.index]]
        label  = f"route {args.index}"
    else:
        if args.to_idx is None:
            parser.error("--from requires --to")
        target = all_routes[args.from_idx : args.to_idx + 1]
        label  = f"routes {args.from_idx}–{args.to_idx}"

    print(f"Enriching {label} with model {args.model} (concurrency={args.concurrency}) …")

    repo_root = Path(args.handlers_root)

    new_results = asyncio.run(enrich_batch(
        routes=target,
        existing=existing,
        repo_root=repo_root,
        model=args.model,
        concurrency=args.concurrency,
        retries=args.retries,
        force=args.force,
    ))

    # Merge into the full existing manifest (upsert by operationId)
    existing.update(index_by_operation_id(new_results))
    final = list(existing.values())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_manifest(out_path, final)

    ok      = sum(1 for e in new_results if e.get("openapi_path_item"))
    failed  = len(new_results) - ok
    skipped = len(new_results) - len([r for r in new_results if r not in list(index_by_operation_id(load_manifest(out_path) if False else []).values())])

    print(f"\nDone. enriched: {ok}  failed: {failed}")
    print(f"Manifest written to {out_path}  ({len(final)} total entries)")


if __name__ == "__main__":
    main()
