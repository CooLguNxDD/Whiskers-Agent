---
name: career-ops-pipeline
description: '**WORKFLOW SKILL** — One-call apply pipeline pairing job_search_plugin + portfolio_plugin for a CLI agent driving Career-Ops. USE FOR: fetching a job posting, scoring fit, baking a job-tailored portfolio link, tailoring resume/cover letter, tracking the application — all from one goal. DO NOT USE FOR: portfolio redesign/ask turns (use portfolio_bake_v1/portfolio_ask_v1), submitting an application (this pipeline only drafts), editing career-ops files directly.'
---

# Career-Ops pipeline — one flow, ten stages

## When to use this skill

You are a CLI agent (Codex or similar) connected to the Whiskers Agent tunnel with
`job_search_plugin` and `portfolio_plugin` installed, driven by a Career-Ops mode
(`auto-pipeline`, `oferta`, `apply`, …). Career-Ops handed you a job URL or pasted JD and wants it
evaluated, tailored, and tracked. Use this skill instead of calling `fetch_job_posting` →
`evaluate_offer_fit` → `bake_portfolio_for_job` → `tailor_resume` → `render_resume_pdf` →
`tailor_cover_letter` → `sync_application_status` yourself — one dispatch does all ten stages and
already handles every slot-name mismatch between those tools.

**Do not** hand-chain the individual tools for a normal apply flow. The flow exists precisely
because those tools don't share field names (`fetch_job_posting`'s `title` vs.
`evaluate_offer_fit`'s `offer_text`, `bake_portfolio_for_job`'s `short_id` vs.
`sync_application_status`'s `portfolio_job_id`) — re-deriving that wiring by hand is how the alias
bugs this flow fixes got introduced in the first place.

## The one call

Call the MCP tool directly — this is the reliable path, not a fallback:

```python
run_career_ops_pipeline(
    goal="apply to this job",          # free text, feeds signal resolution
    applicant_profile_id=42,
    url="https://ca.indeed.com/viewjob?jk=…",   # keep the URL even when the board is gated
    raw_text="…pasted JD…",                    # required for Indeed/LinkedIn after Cloudflare 401
    theme="",                          # optional bake theme (cozy|neon|paper|latte|frappe|macchiato|mocha)
)
```

`url` and `raw_text` may be supplied **together**. A paste is parsed into the same
ATS slot schema (`title`, `company`/`via`, `location`, `compensation`, `job_type`,
`job_id`). An Indeed URL still contributes `jk`/`vjk` as `job_id` — do not drop the
URL just because fetch was blocked. `raw_text` alone also works (provider
`raw_text`, synthesized tracking id downstream).

**Do not rely on natural-language routing through `run_graph` for this.** The flow is also
registered as a synthetic catalog op (`specialist/career_ops_apply_v1`, reachable server-side via
`execute_operation("specialist", "career_ops_apply_v1", {...})` or `POST
/api/catalog/session_gated/execute`) and *claims* apply-flavored goals in `specialist_entry` — but
getting there depends on the turn's LLM triage classifying the message as `"specialist"` first
(`core_graph/prompts/triage_prompt.py`), and that classification is probabilistic: the same goal
phrasing can fall through to the generic linear planner, which will improvise a partial, wrong
subset of these tools (verified live — a `run_graph` call with a clear "apply to this job" goal
fell through to `planner_linear` and repeated `tailor_resume` four times, never reaching `bake`,
`evaluate_offer_fit`, or `track`). `run_career_ops_pipeline` reads caller scopes straight from the
MCP access token (`core_graph.mcp_tool._caller_scopes`) and dispatches `run_flow` directly — no
triage LLM call in the path, no chance of misclassification.

## Mental model

```
url+raw_text ──▶ ingest ──▶ signals ──▶ liveness ──▶ fit ──▶ bake ──▶ links ──▶ resume ──▶ resume_pdf ──▶ cover ──▶ track
              fetch_job_    resolve_    check_job_  evaluate  bake_    prepare_   tailor_    render_       tailor_    sync_
              posting       pipeline_   liveness    offer_    portfolio application_ resume    resume_pdf   cover_    application_
                            signals                 fit       _for_job record                                letter    status
```

| # | stage | op | reads → writes | on_fail |
|---|---|---|---|---|
| 1 | `ingest` | `job_search_plugin__fetch_job_posting` | url, raw_text → clean_description, title, company, location, job_id, provider, source_url, via, posting_entity, end_employer, employment_class, needs_browser_scrape, ingest_status | fail_closed |
| 2 | `signals` | `job_search_plugin__resolve_pipeline_signals` | goal, title, company, clean_description, source_url, job_id, via, … → company, role, job_description, offer_text, posting_url, job_id, url, role_title, via, end_employer | fail_closed |
| 3 | `liveness` | `job_search_plugin__check_job_liveness` | url, company, role_title → is_live, is_potential_ghost_job, legitimacy_tier, ghost_reasons | continue |
| 4 | `fit` | `job_search_plugin__evaluate_offer_fit` | applicant_profile_id, offer_text → verdict, hybrid_used | continue |
| 5 | `bake` | `portfolio_plugin__bake_portfolio_for_job` | job_description, company, role, posting_url, theme → short_id, portfolio_job_id, query_param, quality, degraded | continue |
| 6 | `links` | `job_search_plugin__prepare_application_record` | portfolio_job_id, job_id, company, role, verdict, via, end_employer, employment_class → portfolio_url, job_id, application_status, notes | continue |
| 7 | `resume` | `job_search_plugin__tailor_resume` | applicant_profile_id, job_description, company, role, job_id → tailored_text, kind | fail_closed |
| 8 | `resume_pdf` | `job_search_plugin__render_resume_pdf` | applicant_profile_id, tailored_text, kind, job_id, portfolio_job_id → object_key, presigned_url | continue |
| 9 | `cover` | `job_search_plugin__tailor_cover_letter` | applicant_profile_id, job_description, job_id, portfolio_url → cover_letter_text | continue |
| 10 | `track` | `job_search_plugin__sync_application_status` | applicant_profile_id, job_id, application_status, notes, portfolio_job_id → application, created | continue |

`liveness` is a separate stage rather than something `fetch_job_posting` does inline: that tool is
tagged `read` and declares `readOnlyHint: True`, while a liveness check writes a
`job_posting_liveness` row. It runs after `signals` because `check_job_liveness` names its inputs
`url`/`role_title`, which is what `resolve_pipeline_signals` emits (see the slot-name contract
below). It is `continue` — a ghost-job signal is advisory and must never cost you the apply.
A Cloudflare/401/captcha wall is **not** a ghost-job signal: ingest classifies that as
`needs_browser_scrape` / `ingest_status=needs_browser_scrape` and
`is_potential_ghost_job=false`. `run_career_ops_pipeline` returns that degraded envelope
(status `ok`, `application_status=drafted`, no resume) instead of fail-closing the flow.
Paste the JD as `raw_text` (keep the URL) and call again.

### Paste ingest, agency, employment class

`fetch_job_posting` no longer returns empty `title`/`company`/`location`/`job_id` for a paste.
It parses those from the text into the ATS schema. Staffing phrasing ("our client", known
agencies including CorGTA) is modeled as data:

- `posting_entity` — who posted (the agency on an agency listing)
- `via` — agency name when detected
- `end_employer` — named client, else `null` (never invented)
- `company` — `end_employer` when known; otherwise empty at ingest. Signals fills the
  pipeline `company` slot from `via` so bake/resume can run without pretending the agency
  is the client.
- `employment_class` — `fte` / `contract` / `unknown`. Indeed's "Full-time" chip plus
  body text "contract capacity" is `unknown`, not silently FTE.

### Portfolio links

`bake` is deliberately `continue`, not `fail_closed`: a failed bake must not cost you the tailored
resume. `links` is also `continue` and does **not** require `portfolio_job_id` — an empty bake
id yields `portfolio_url=""` and the resume stage still runs.

`prepare_application_record` / `render_resume_pdf` / `tailor_cover_letter` only emit a
portfolio URL when `CATPORTFOLIO_PUBLIC_DOMAIN` is a public host. `localhost`, `127.0.0.1`,
and an unset domain omit the link — never write `http://localhost:11000/?j=…` into an
application record, resume header, or cover letter. The cover prompt is instructed to never
invent a placeholder.

Only one PDF is rendered — the resume's. If you need a cover-letter PDF too, call
`render_resume_pdf(kind="cover_letter", ...)` yourself with the flow's `cover_letter_text`;
the flow doesn't do it, since a second call would overwrite `object_key`/`presigned_url`
on the shared blackboard.

## Slot-name contract

A `FlowSpec` deterministic stage builds its next op's kwargs as `{k: board.get(k) for k in
stage.reads}` — a `reads` entry *is* the next tool's parameter name, verbatim, with no renaming.
When two adjacent tools don't already share a field name, this flow closes the gap one of two ways
— know which, because editing either tool's return dict without checking both is how this breaks:

- **Additive alias keys** on an existing tool's return, when the value already exists under another
  name. `bake_portfolio_for_job` returns `portfolio_job_id` as an alias of `short_id`;
  `tailor_cover_letter` returns `cover_letter_text` as an alias of `tailored_text` (so it doesn't
  clobber the resume's own `tailored_text` slot when both stages write to one blackboard).
- **Adapter ops** (`plugins/job_search_plugin/MCPTools/pipeline_tools.py`) when a value must be
  *derived*, not just renamed — `resolve_pipeline_signals` maps `title`→`role`,
  `clean_description`→`job_description`/`offer_text`, fills `company` from
  `end_employer` then `via` (agency paste), and falls back to
  `portfolio_plugin__resolve_bake_job_signals` (a free-text regex parse) when an ATS left
  `company`/`role` blank, and re-emits `posting_url`/`role` a second time as `url`/`role_title`
  for `check_job_liveness`, which spells those two inputs differently; `prepare_application_record` builds a recruiter-safe `portfolio_url` (omitted on localhost / failed bake) and emits the fixed
  `application_status: "drafted"` value under a name that doesn't collide with its own envelope's
  `status` key.

**Neither existing tool's original field names changed.** Career-Ops (or any external caller of the
individual tools) sees the same signatures as before — everything above is additive.

## Scopes

A Career-Ops key needs the full `job_search_plugin` group set — `read`, `write`, `profile`,
`application`, `search`, `liveness`, `enrich`, `pipeline` — plus `group:portfolio_plugin:read`/
`write` for stage 4. The synthetic `specialist/career_ops_apply_v1` op itself carries
`required_scopes=()` (see `flow_registry._flow_to_operation`) — it is **not** the gate. Each stage's
own op is authorized independently as it dispatches (`execute_operation`'s `_authorize`), so a key
missing one scope doesn't 403 the whole flow up front — it fails closed or continues at exactly the
stage that needed the missing scope, per the table above.

## Driving from Career-Ops

Career-Ops itself is untouched — it has no MCP client and doesn't need one (see
`plugins/job_search_plugin/skills/job-search-pipeline/JOB_SEARCH_SKILL.md` for the full tool
inventory). You, the connected CLI agent, are the bridge:

1. Career-Ops' mode (e.g. `auto-pipeline`) hands you a URL or pasted JD plus the local profile from
   `config/profile.yml` / `cv.md` (already synced into `applicant_profile_id` via
   `upsert_applicant_profile`).
2. Make the one call above.
3. Write results back through Career-Ops' **existing** surfaces — never touch its data files
   directly:
   - Evaluation summary → `reports/{NNN}-{company}-{date}.md`, number reserved via
     `node reserve-report-num.mjs`.
   - Resume PDF → download `presigned_url` into `output/`, register in `data/pdf-index.tsv`.
   - Tracker row → `node tracker.mjs sync` / `node set-status.mjs` so `data/applications.md` (the
     canonical source per `DATA_CONTRACT.md`) reflects the new `drafted` row.
4. Stop there. `application_status` is always `"drafted"` — Career-Ops' own doctrine ("never
   submits applications on your behalf") means the human reviews the report and clicks apply
   themselves; this flow never calls `submit_application`.

## Tests

```bash
docker exec -e PYTHONPATH=/app whiskers-mcp-server pytest \
  plugins/job_search_plugin/tests/test_career_ops_flow.py \
  plugins/job_search_plugin/tests/test_posting_ingest.py \
  plugins/job_search_plugin/tests/test_job_fetch_tools.py -q
```

## Anti-patterns

| Don't | Do instead |
|---|---|
| Call `fetch_job_posting` → `evaluate_offer_fit` → … as 7 separate MCP calls | One `run_career_ops_pipeline(goal, applicant_profile_id, url|raw_text)` |
| Ask `run_graph` to "apply to this job" in plain language and expect all 10 stages | Triage may misclassify it as `classic`; call `run_career_ops_pipeline` directly instead |
| Rename `tailored_text` or `short_id` on the underlying tools to "fix" a slot mismatch | Add an alias key or a `pipeline_tools.py` adapter op — those signatures are a held Career-Ops contract |
| Set `application_status` to `"applied"` in `prepare_application_record` or after the flow | Leave it `"drafted"`; a human submits, never this pipeline |
| Assume a failed `bake` stage aborts the flow | It's `on_fail: "continue"` — check `portfolio_job_id`/`degraded` in the result before assuming a link exists. `links` is also `continue`; no bake id must not drop the resume |
| Treat Indeed Cloudflare 401 as a ghost job or a hard ingest failure | It is `needs_browser_scrape`. Paste the JD as `raw_text` and keep the URL. Ghost scoring stays in `check_job_liveness` only |
| Write `http://localhost:11000/?j=…` into a resume, cover, or tracker row | Omit the link unless `CATPORTFOLIO_PUBLIC_DOMAIN` is a public host |
| Store a staffing agency as the end employer when the client is unnamed | Keep `via`/`posting_entity`; leave `end_employer` null — do not invent the client |
| Write directly into `career-ops`'s `data/applications.md` | Go through `tracker.mjs sync` / `set-status.mjs` — it owns that file |

## Related skills

- `job-search-pipeline` (`plugins/job_search_plugin/skills/job-search-pipeline/JOB_SEARCH_SKILL.md`) — full per-tool inventory, injected into planner prompts
- `inference-guide` — OperationCatalog / `execute_operation` mechanics this flow relies on
- `scope-management` — scope grammar behind the token list above
