# job_search_plugin — Setup Guide

Job search / enrich / evaluate / apply / track pipeline for the Whiskers Agent MCP server, paired with **OpenClaw** for browser-hands automation. Five tool groups, MinIO-backed resume/CV PDFs, pgvector RAG offer-fit scoring.

For the runtime *operating* flow (canonical chain, OpenClaw handoff, write-confirmation), see [skills/job-search-pipeline/JOB_SEARCH_SKILL.md](skills/job-search-pipeline/JOB_SEARCH_SKILL.md). This document covers **install + configuration** only.

---

## 1. Prerequisites

- Whiskers Agent MCP server stack running (`python scripts/dev.py`) — Postgres + pgvector + `whiskers-agent-server` container.
- An LLM provider configured (`LLM_PROVIDER` + key) — used by the enrich + evaluate tools.
- An embedding profile configured (`config/embedding_config.json`) — used by offer-fit RAG.

## 2. Install

### 2.1 Dependencies
Added to `requirements.txt`:
```
minio>=7.2.0
reportlab>=4.0.0
```
Rebuild the image so they persist (a bare container restart will NOT pick them up and boot fails on `import minio`):
```bash
python scripts/dev.py            # rebuilds + restarts the stack
# or:  docker compose build whiskers-agent && docker compose up -d
```

### 2.2 Start MinIO
The `minio` service is declared in `docker-compose.yml`:
```bash
docker compose up -d minio
```
Console: http://localhost:9001 (default `minioadmin` / `minioadmin`). The bucket (`job-search-resumes`) is created automatically on first upload via `ensure_bucket`.

### 2.3 Apply the database migration
Creates `job_applicant_profiles`, `job_applications`, `job_preference_embeddings` (plugin-local migration `plugins/job_search_plugin/migrations/0001_init.py`, applied on load via `PluginSchemaMigrator`):
```bash
python scripts/migrate.py
```
> Tool calls fail with a DB error until this runs. Plugin import/boot is unaffected.

### 2.4 Enable the plugin
Already registered in `config/plugin_config.json` under `"plugins"`:
```json
"plugins.job_search_plugin"
```
Restart the server to load it.

## 3. Configuration

### 3.1 Environment variables (`.env` / compose)
| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `MINIO_ENDPOINT` | Yes | `minio:9000` | MinIO host:port |
| `MINIO_ACCESS_KEY` | Yes | `minioadmin` | MinIO access key |
| `MINIO_SECRET_KEY` | Yes | `minioadmin` | MinIO secret key |
| `MINIO_BUCKET` | No | `job-search-resumes` | PDF bucket |
| `MINIO_SECURE` | No | `false` | TLS to MinIO |
| `EMBED_DIMENSIONS` | No | `1536` | Vector dim for the embedding column / migration |

### 3.2 Provider API keys (vault)
Search/details providers read their keys from the encrypted vault at call time. Missing keys → that provider is **silently skipped** (never raises). Set only the ones you use:
```bash
whiskers vault set job_search_plugin GREENHOUSE_API_KEY <key>
whiskers vault set job_search_plugin LEVER_API_KEY      <key>
whiskers vault set job_search_plugin ADZUNA_APP_ID      <id>
whiskers vault set job_search_plugin ADZUNA_APP_KEY     <key>
```
- `remotive` needs no key (public API).
- `linkedin` / `indeed` have no REST API → returned with `needs_browser_scrape: true` for OpenClaw to handle.

## 4. Verify

```bash
# Unit tests (in the container)
docker exec -e PYTHONPATH=/app whiskers-agent-server \
  python -m pytest test/unit/test_minio_client.py \
  plugins/job_search_plugin/tests/test_job_search_store.py \
  plugins/job_search_plugin/tests/test_job_search_tools.py \
  plugins/job_search_plugin/tests/test_job_enrich_tools.py \
  plugins/job_search_plugin/tests/test_offer_fit.py \
  plugins/job_search_plugin/tests/test_job_application_flow.py -q
```
Then exercise the chain through the playground (`POST /api/playground/stream_goap`) or `run_graph`:
`search_jobs → get_job_details → tailor_resume → render_resume_pdf → evaluate_offer_fit → create_application → submit_application`.
`render_resume_pdf` should return a working MinIO presigned URL; `submit_application` should prompt for confirmation.

## 5. Tool reference

| Group | Tools | Kind |
|-------|-------|------|
| Search | `search_jobs`, `get_job_details` | read |
| Enrich | `tailor_resume`, `tailor_cover_letter`, `render_resume_pdf` | read / write (PDF→MinIO) |
| Evaluate | `index_preferences`, `evaluate_offer_fit` | write / read (RAG) |
| Apply | `create_application`, `submit_application`, `mark_application_submitted` | write (confirm-gated) |
| Track | `update_application`, `list_applications` | write / read |
| Profile | `get_applicant_profile`, `upsert_applicant_profile` | read / write |

Write tools (`submit_application`, `mark_application_submitted`, `update_application`, …) are gated by the existing `permission_gate` node, which defaults unconfigured write ops to `require_confirmation=True`. Override per-tool via the `tool_permissions` store if needed.

## 6. OpenClaw pairing

No server code change required — the server speaks standard MCP over HTTP.
1. Point OpenClaw's MCP client config at `http://<host>:<MCP_PORT>/mcp`.
2. If `OAUTH_ENABLED`, OpenClaw runs the Layer-1 OAuth + PKCE dance; `AutoRegisterMiddleware` auto-registers clients that skip `/register`.
3. OpenClaw uses [JOB_SEARCH_SKILL.md](skills/job-search-pipeline/JOB_SEARCH_SKILL.md) as its operating guide: browser-hands handle `needs_browser_scrape` (search) and `needs_browser_apply` (submit → callback `mark_application_submitted`); a heartbeat task polls `list_applications`.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Boot fails on `ModuleNotFoundError: minio` | Deps pip-installed only in a transient container — rebuild the image (§2.1). |
| Tool returns a DB / relation-not-found error | Migration not applied — run `python scripts/migrate.py` (§2.3). |
| `render_resume_pdf` errors / no URL | MinIO not up or `MINIO_*` unset — `docker compose up -d minio` + check env (§2.2, §3.1). |
| A provider returns nothing | Its vault key is unset → provider skipped (check the `skipped` list in the `search_jobs` result). |
| `evaluate_offer_fit` returns an empty/garbage verdict | Run `index_preferences` first so the applicant's resume + preferences are embedded. |
