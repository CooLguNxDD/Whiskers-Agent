# OpenAPI Pipeline Skill

Generic pipeline: **any HTTP backend → OpenAPI 3.0 → MCP tool definitions**.

Prefer a **live spec** from the running server (`/openapi.json`, `/swagger.json`,
`/v3/api-docs`, …). Source adapters are a fallback when the backend does not
publish OpenAPI.

Implementation lives in `Tools/openapi_pipeline/` (imported by tests and CLIs).
Skill wrappers live in `.claude/skills/openapi-pipeline/script/`.

---

## Scripts Index

| Script | Step | Purpose |
|--------|------|---------|
| `ingest.py` | 1 | Live URL, spec file, or source tree → `route-manifest.json` (+ `openapi.json` when possible) |
| `enrich_routes.py` | 2-A | Bulk AI enrichment (source scans only; skipped when the spec is already complete) |
| `slice_batches.py` | 2-B/M1 | Slice manifest into `AItemp/batch_NN.json` |
| `merge_batches.py` | 2-B/M3 | Merge `AItemp/enriched_batch_*.json` → `enriched-manifest.json` |
| `build_openapi.py` | 3 | Manifest → OpenAPI 3.0 (no-op pass-through if ingest already wrote a spec) |
| `openapi_to_mcp.py` | 4 | OpenAPI spec → flat `mcp-tools-ai.json` |
| `strip_tools.py` | 4.5 | Optional CSV whitelist |
| `split_mcp_tools.py` | 5 | Split flat tools → `<section>/<op_type>/mcp-tools.json` |

Canonical CLI (same as the skill wrapper):

```bash
python Tools/openapi_pipeline/ingest.py --from-url http://127.0.0.1:8000
python .claude/skills/openapi-pipeline/script/ingest.py --from-url http://127.0.0.1:8000
```

---

## Pipeline Overview

```
┌─ A. Live backend  ── GET well-known OpenAPI URLs ─┐
│                                                   │
├─ B. Spec file     ── openapi.json / swagger.yaml ─┤
│                                                   ├── Step 1 ingest
└─ C. Source scan   ── FastAPI / Flask / Express ───┘
        │
        ▼
route-manifest.json
        │
        ├── already_specified (live/file spec) → skip enrichment
        │
        └── source scan → Step 2 enrich (API key or batch subagents)
        │
        ▼  Step 3
openapi.json
        │
        ▼  Step 4
mcp-tools-context/mcp-tools-ai.json
        │
        ▼  Step 4.5 (optional CSV strip)
        │
        ▼  Step 5
mcp-tools-context/<section>/<op>/mcp-tools.json
```

---

## Step 1 — Ingest

**Preferred:** point ingest at a running API. FastAPI, Springdoc, Swagger-UI,
and many gateways already serve a complete document. No handler regex, no
framework-specific registry file.

```bash
# Origin — probes /openapi.json, /swagger.json, /v3/api-docs, …
python Tools/openapi_pipeline/ingest.py \
  --from-url http://127.0.0.1:8000 \
  --out-dir  openapi

# Exact document URL
python Tools/openapi_pipeline/ingest.py \
  --from-url http://127.0.0.1:8080/v3/api-docs \
  --out-dir  openapi

# Checked-in spec
python Tools/openapi_pipeline/ingest.py \
  --from-spec path/to/openapi.yaml \
  --out-dir  openapi
```

When every operation already has an OpenAPI body, ingest writes
`enriched-manifest.json` as well and prints `enrichment: skip`.

### Source fallback (no published spec)

```bash
python Tools/openapi_pipeline/ingest.py \
  --from-source ./backend \
  --adapter auto \
  --out-dir openapi

# Pin a framework
python Tools/openapi_pipeline/ingest.py --from-source ./backend --adapter fastapi
python Tools/openapi_pipeline/ingest.py --from-source ./backend --adapter flask
python Tools/openapi_pipeline/ingest.py --from-source ./backend --adapter express \
  --base-path /api/v1
```

`--adapter auto` scores FastAPI / Flask / Express hits under the tree.

Optional JSON config (prefixes, extra security schemes, ignore-list):

```bash
python Tools/openapi_pipeline/ingest.py \
  --from-url http://127.0.0.1:8000 \
  --config .claude/skills/openapi-pipeline/examples/ingest.config.example.json
```

Each manifest entry has: `method`, `path`, `operationId`, `adapter`,
`handlerFile` (source scans), `security[]`, and `openapi_path_item` when the
spec already described the operation. Paths may use Express `:id` or OpenAPI
`{id}` — later steps normalize to `{id}`.

---

## Step 2 — Enrichment

Only needed for **source scans**. Skip when Step 1 printed `enrichment: skip`.

Choose **Mode A** (API key) or **Mode B** (batch subagents).

### Mode A — `enrich_routes.py`

Requires `ANTHROPIC_API_KEY`.

```bash
python .claude/skills/openapi-pipeline/script/enrich_routes.py --all
python .claude/skills/openapi-pipeline/script/enrich_routes.py --from 0 --to 50
python .claude/skills/openapi-pipeline/script/enrich_routes.py --all --force
```

Output: `openapi/enriched-manifest.json`

Dialect hints follow `entry.adapter` (`express` / `fastapi` / `flask`).

### Mode B — batch subagents

```bash
python .claude/skills/openapi-pipeline/script/slice_batches.py \
  --manifest openapi/route-manifest.json \
  --out-dir  openapi/AITemp
```

Prompt template: [agent/agent.md](agent/agent.md).
Generation rules: [skills/enrich_routes_skill.md](skills/enrich_routes_skill.md).

Each subagent reads `openapi/AITemp/batch_NN.json`, reads `handlerFile`,
writes `openapi/AITemp/enriched_batch_NN.json`.

```bash
python .claude/skills/openapi-pipeline/script/merge_batches.py \
  --in-dir openapi/AITemp \
  --out    openapi/enriched-manifest.json
```

---

## Step 3 — Build OpenAPI Spec

Skip if ingest already wrote `openapi/openapi.json` from a live/file spec.

```bash
python .claude/skills/openapi-pipeline/script/build_openapi.py \
  --input openapi/enriched-manifest.json \
  --out   openapi/openapi.json \
  --title "Example API" \
  --server http://127.0.0.1:8000
```

Uses `openapi_path_item` when present; otherwise a skeleton operation.
Default security scheme is BearerAuth; extra schemes come from ingest config.

---

## Step 4 — Convert to MCP Tools

```bash
mkdir -p openapi/mcp-tools-context
python .claude/skills/openapi-pipeline/script/openapi_to_mcp.py \
  --input openapi/openapi.json \
  --out   openapi/mcp-tools-context/mcp-tools-ai.json
```

---

## Step 4.5 — Strip Unused Tools (Optional)

```bash
python .claude/skills/openapi-pipeline/script/strip_tools.py \
  --csv   openapi/mcp-tools-context/Toolkit.csv \
  --input openapi/mcp-tools-context/mcp-tools-ai.json \
  --out   openapi/mcp-tools-context/mcp-tools-ai-stripped.json
```

---

## Step 5 — Split Into Section/Operation Folders

```bash
python .claude/skills/openapi-pipeline/script/split_mcp_tools.py --build-config \
  --input   openapi/mcp-tools-context/mcp-tools-ai.json \
  --config  openapi/mcp-tools-context/mcp-tools-config.json \
  --out-dir openapi/mcp-tools-context
```

Sections are inferred (not a fixed product table):

1. `_meta.domain`
2. `_meta.tags[*]`
3. Path segments (skip `api` and `vN`)
4. `operationId` / tool `name`

Edit `section_aliases` in the generated config to collapse synonyms, then
re-run without `--build-config`.

| HTTP method | Folder |
|---|---|
| `GET` | `read/` |
| `DELETE` | `delete/` |
| `POST`, `PUT`, `PATCH` | `write_update/` |

---

## Full Run

### Live spec (typical)

```bash
python Tools/openapi_pipeline/ingest.py \
  --from-url http://127.0.0.1:8000 \
  --out-dir  openapi

mkdir -p openapi/mcp-tools-context
python .claude/skills/openapi-pipeline/script/openapi_to_mcp.py \
  --input openapi/openapi.json \
  --out   openapi/mcp-tools-context/mcp-tools-ai.json

python .claude/skills/openapi-pipeline/script/split_mcp_tools.py --build-config \
  --input   openapi/mcp-tools-context/mcp-tools-ai.json \
  --config  openapi/mcp-tools-context/mcp-tools-config.json \
  --out-dir openapi/mcp-tools-context
```

### Source scan (no published spec)

```bash
python Tools/openapi_pipeline/ingest.py --from-source ./backend --adapter auto --out-dir openapi
python .claude/skills/openapi-pipeline/script/enrich_routes.py --all
python .claude/skills/openapi-pipeline/script/build_openapi.py \
  --input openapi/enriched-manifest.json --out openapi/openapi.json
# then Step 4–5 as above
```

---

## Output File Map

```
openapi/
├── route-manifest.json
├── enriched-manifest.json      (from live spec, or after Step 2)
├── openapi.json
├── AITemp/                     (Mode B cache)
└── mcp-tools-context/
    ├── mcp-tools-ai.json
    ├── mcp-tools-config.json
    ├── mcp-tools-index.json
    └── <section>/
        ├── read/mcp-tools.json
        ├── write_update/mcp-tools.json
        └── delete/mcp-tools.json
```

---

## Related Files

- [agent/agent.md](agent/agent.md) — Mode B subagent prompt
- [skills/enrich_routes_skill.md](skills/enrich_routes_skill.md) — Operation Object rules
- [examples/ingest.config.example.json](examples/ingest.config.example.json) — optional ingest config
- `Tools/openapi_pipeline/` — ingest library (`ingest.py`, adapters, `build.py`)
